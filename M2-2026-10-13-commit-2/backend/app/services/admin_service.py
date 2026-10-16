"""管理员业务编排服务（契约 §3.11 / §3.12 / §3.13）。

两条硬性要求：

1. **审核动作必须是原子事务**：改认领 → 按状态机改物品 → 写审计 → 清缓存，
   其中任何一步失败都整体回滚（`db.rollback()`），绝不出现
   「认领已通过但物品状态没变」或「状态变了但没有审计记录」；
2. **审核详情聚合不得泄露答案**：标准答案与 `answers_hash` 永不出现在响应里，
   只输出「是否命中 + 得分 + 理由」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import AuditTargetType, ClaimStatus, RiskLevel, UserFacingText
from app.core.errors import ClaimNotFoundError
from app.models.audit_log import AuditLog
from app.models.claim import Claim
from app.models.item import Item
from app.models.match import Match
from app.models.user import User
from app.schemas.admin import AdminClaimQuery, AuditLogQuery
from app.services import audit_service, serializers
from app.services.cache import cache
from app.services.scoring import match_level_for
from app.services.state_machine import assert_claim_transition, assert_item_transition, next_status_for_review

logger = logging.getLogger(__name__)

# 审核台历史记录最多返回条数
HISTORY_LIMIT = 50


@dataclass
class ReviewOutcome:
    """审核动作结果（供路由组装契约 §3.11 响应）。"""

    claim_id: int
    action: str
    claim_status: str
    item_id: int
    item_status: str
    reviewed_at: datetime | None
    audit_log_id: int | None = None


# ----------------------------------------------------------------------
# 审核详情聚合（契约 §3.12）
# ----------------------------------------------------------------------
def build_claim_detail(db: Session, *, claim: Claim) -> dict[str, Any]:
    """组装审核台详情：`claim` + `match` + `lost_item` + `found_item` + `verification` + `history`。

    ⚠ 全部字段均来自 `serializers`（字段级权限唯一收口点），
    本函数只做「结构化拼装」，不得新增任何原始敏感字段。
    """
    admin_claim = serializers.serialize_claim_for_admin(claim)
    match = claim.match
    lost_item = match.lost_item if match else None
    found_item = match.found_item if match else None

    claim_block = {
        "id": admin_claim.get("claim_id"),
        "status": admin_claim.get("status"),
        "verification_score": admin_claim.get("verification_score"),
        "credibility_score": admin_claim.get("credibility_score"),
        "risk_level": admin_claim.get("risk_level"),
        "risk_level_text": admin_claim.get("risk_level_text"),
        "created_at": admin_claim.get("created_at"),
        "attempts_used": admin_claim.get("attempts_used"),
        "max_attempts": admin_claim.get("max_attempts"),
        "admin_comment": admin_claim.get("admin_comment"),
        "reviewed_at": admin_claim.get("reviewed_at"),
        "applicant": admin_claim.get("applicant"),
        "reviewer": admin_claim.get("reviewer"),
        "questions": admin_claim.get("questions"),
    }

    match_block = serializers.serialize_match(match) if match else {}

    verification = {
        "per_question": admin_claim.get("per_question") or [],
        "attempts_used": admin_claim.get("attempts_used", 0),
        "attempts_left": admin_claim.get("attempts_left", 0),
        "risk_tips": admin_claim.get("risk_tips") or [],
    }

    return {
        "claim": claim_block,
        "match": match_block,
        "lost_item": serializers.serialize_item_for_admin(lost_item) if lost_item else None,
        "found_item": serializers.serialize_item_for_admin(found_item) if found_item else None,
        "verification": verification,
        "history": _claim_history(db, claim_id=claim.id),
    }


def _claim_history(db: Session, *, claim_id: int, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
    """认领的审计轨迹（契约 §3.12 的 `history`）。"""
    stmt = (
        select(AuditLog)
        .where(AuditLog.target_type == AuditTargetType.CLAIM)
        .where(AuditLog.target_id == claim_id)
        .order_by(AuditLog.created_at.asc(), AuditLog.id.asc())
        .limit(limit)
    )
    logs = list(db.execute(stmt).scalars().all())
    usernames = _usernames_for(db, [log.operator_id for log in logs if log.operator_id])
    return [
        {
            "id": log.id,
            "action": log.action,
            "operator": usernames.get(log.operator_id),
            "operator_id": log.operator_id,
            "created_at": log.created_at.isoformat() if log.created_at else None,
            "detail": log.detail or {},
            "request_id": log.request_id,
        }
        for log in logs
    ]


def _usernames_for(db: Session, user_ids: list[int]) -> dict[int, str]:
    """批量取用户名（避免 N+1 查询）。"""
    unique_ids = sorted({int(uid) for uid in user_ids if uid})
    if not unique_ids:
        return {}
    stmt = select(User.id, User.username).where(User.id.in_(unique_ids))
    return {int(row[0]): str(row[1]) for row in db.execute(stmt).all()}


# ----------------------------------------------------------------------
# 待审核认领列表（契约 §3.10.2）
# ----------------------------------------------------------------------
@dataclass
class AdminClaimFilters:
    """管理员列表过滤条件（由 `schemas.admin.AdminClaimQuery` 解析而来）。"""

    statuses: list[str] = field(default_factory=list)
    risk_levels: list[str] = field(default_factory=list)
    category: str | None = None
    keyword: str | None = None
    sort_by: str = "risk_level"
    order: str = "desc"


def _risk_bucket():
    """风险等级排序桶：high(0) → medium(1) → low(2) → 其它(3)，跨库可用的 CASE 表达式。

    这样「高风险优先」不依赖 MySQL 的 `FIELD()` 函数，SQLite（测试）同样可用。
    """
    from sqlalchemy import case

    return case(
        (Claim.risk_level == RiskLevel.HIGH, 0),
        (Claim.risk_level == RiskLevel.MEDIUM, 1),
        (Claim.risk_level == RiskLevel.LOW, 2),
        else_=3,
    )


def _build_claim_conditions(filters: AdminClaimFilters) -> list[Any]:
    """把过滤条件翻译成 SQLAlchemy 条件（列表展示的是申请人所属的 found 记录）。"""
    conditions: list[Any] = []
    if filters.statuses:
        conditions.append(Claim.status.in_(filters.statuses))
    if filters.risk_levels:
        conditions.append(Claim.risk_level.in_(filters.risk_levels))
    return conditions


def list_claims(
    db: Session, *, filters: AdminClaimFilters, offset: int, limit: int
) -> tuple[list[Claim], int]:
    """待审核认领分页查询（契约 §3.10.2）。

    排序规则：

    - `sort_by=risk_level`（默认）：高风险优先，**同风险等级按 `created_at` 升序**（先来先审）；
    - `sort_by=created_at` / `verification_score`：按 `order` 指定方向；
    - `category` / `keyword` 过滤的是**列表展示的物品**（申请人所属的 found 记录），
      与响应中的 `item` 保持一致。
    """
    stmt = select(Claim).join(Match, Claim.match_id == Match.id).join(Item, Match.found_item_id == Item.id)
    count_stmt = (
        select(func.count())
        .select_from(Claim)
        .join(Match, Claim.match_id == Match.id)
        .join(Item, Match.found_item_id == Item.id)
    )

    conditions = _build_claim_conditions(filters)
    if filters.category:
        conditions.append(Item.category == filters.category)
    if filters.keyword:
        conditions.append(Item.title.like(f"%{filters.keyword.strip()}%"))

    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)

    if filters.sort_by == "risk_level":
        # 契约：默认高风险优先 + 同风险等级先来先审（此处 created_at 固定升序）
        stmt = stmt.order_by(_risk_bucket().asc(), Claim.created_at.asc())
    elif filters.sort_by == "verification_score":
        column = Claim.verification_score
        stmt = stmt.order_by(
            (column.desc() if filters.order == "desc" else column.asc()), Claim.created_at.asc()
        )
    else:
        column = Claim.created_at
        stmt = stmt.order_by(column.desc() if filters.order == "desc" else column.asc())

    rows = db.execute(stmt.offset(offset).limit(limit)).scalars().unique().all()
    return list(rows), total


def claim_counts(db: Session) -> dict[str, int]:
    """审核台徽标计数（契约 §3.10.2 的 `counts`）。

    - `pending_review`：待审核认领数；
    - `high_risk`：**待办队列内**（待审核 + 待补充信息）的高风险认领数；
    - `request_more_info`：待补充信息认领数。
    """

    def _count(*conditions: Any) -> int:
        stmt = select(func.count()).select_from(Claim)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(db.execute(stmt).scalar_one() or 0)

    pending = _count(Claim.status == ClaimStatus.PENDING_REVIEW)
    high_risk = _count(
        Claim.risk_level == RiskLevel.HIGH,
        Claim.status.in_([ClaimStatus.PENDING_REVIEW, ClaimStatus.REQUEST_MORE_INFO]),
    )
    more_info = _count(Claim.status == ClaimStatus.REQUEST_MORE_INFO)
    return {"pending_review": pending, "high_risk": high_risk, "request_more_info": more_info}


def serialize_claim_list_item(claim: Claim, *, viewer: User) -> dict[str, Any]:
    """待审核列表元素序列化（契约 §3.10.2）。

    **不含**隐藏特征与答案线索：`item` 使用列表视图
    （`serialize_item(detail=False)` 本身不输出 `hidden_features`）。
    """
    match = claim.match
    item = match.found_item if match else None
    total = float(match.total_score or 0) if match else None
    item_summary = serializers.serialize_item(item, viewer=viewer, detail=False) if item else None

    applicant = claim.applicant.to_public_dict() if claim.applicant else None
    return {
        "claim_id": claim.id,
        "status": claim.status,
        "status_text": UserFacingText.CLAIM_STATUS.get(claim.status, claim.status),
        "risk_level": claim.risk_level,
        "risk_level_text": UserFacingText.RISK_LEVEL.get(claim.risk_level, claim.risk_level),
        "verification_score": float(claim.verification_score) if claim.verification_score is not None else None,
        "credibility_score": float(claim.credibility_score) if claim.credibility_score is not None else None,
        "attempts_used": claim.attempts_used,
        "applicant": (
            {
                "id": applicant.get("id"),
                "username": applicant.get("username"),
                "contact_id": applicant.get("contact_id"),
            }
            if applicant
            else None
        ),
        "item": (
            {
                "id": item_summary.get("id"),
                "title": item_summary.get("title"),
                "category": item_summary.get("category"),
                "post_type": item_summary.get("post_type"),
                "thumbnail_url": item_summary.get("thumbnail_url"),
            }
            if item_summary
            else None
        ),
        "total_score": total,
        "match_level": match_level_for(total) if total is not None else None,
        "created_at": claim.created_at.isoformat() if claim.created_at else None,
    }


def build_admin_claim_list(
    db: Session, *, query: AdminClaimQuery, page: int, page_size: int, viewer: User
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """待审核列表的完整数据聚合（列表 + 总数 + 徽标计数）。"""
    filters = AdminClaimFilters(
        statuses=query.statuses,
        risk_levels=query.risk_levels,
        category=query.category,
        keyword=query.keyword,
        sort_by=query.sort_by,
        order=query.order,
    )
    rows, total = list_claims(
        db, filters=filters, offset=(page - 1) * page_size, limit=page_size
    )
    payload = [serialize_claim_list_item(claim, viewer=viewer) for claim in rows]
    return payload, total, claim_counts(db)


# ----------------------------------------------------------------------
# 审核动作（契约 §3.11）
# ----------------------------------------------------------------------
def review_claim(
    db: Session,
    *,
    claim: Claim,
    admin: User,
    action: str,
    comment: str | None,
    ip: str | None = None,
) -> ReviewOutcome:
    """审核认领（原子事务）。

    事务内依次完成（契约 §3.11「副作用」四条）：

    1. 更新 `claims.status` / `admin_comment` / `reviewed_by` / `reviewed_at`；
    2. 按**状态机**更新被认领物品（`lost_item`）的状态：
       `pending_review → claimed / rejected / request_more_info`；
    3. 写入审计：`data/claim.review`（含 `operator_id`、`target_type=claim`、`request_id`）
       —— `detail` 由 `audit_service.sanitize_detail()` 脱敏；
    4. 事务提交后清理缓存：`match:item:{item_id}:*`、`claim:session:{claim_id}`。

    :raises StateConflictError: 当前状态不允许该动作（`409`）
    """
    # 审核动作 → 目标状态（映射定义在 state_machine，禁止在此硬编码）
    target_claim_status, target_item_status = next_status_for_review(action)

    # 状态机校验（先校验后修改，避免半提交）
    assert_claim_transition(claim.status, target_claim_status)
    match = claim.match
    item = match.lost_item if match else None
    if item is None:
        raise ClaimNotFoundError("认领关联的物品不存在，无法审核")
    assert_item_transition(item.status, target_item_status)

    from_status = item.status
    reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        claim.status = target_claim_status
        claim.admin_comment = comment
        claim.reviewed_by = admin.id
        claim.reviewed_at = reviewed_at
        db.add(claim)

        item.status = target_item_status
        db.add(item)
        audit_service.log_item_status_change(
            db,
            item_id=item.id,
            operator=admin,
            from_status=from_status,
            to_status=target_item_status,
            reason=f"管理员审核认领（{action}）",
        )

        audit_log = audit_service.log_claim_review(
            db, claim_id=claim.id, operator=admin, action=action, comment=comment
        )
        db.flush()  # 取 audit_log.id（同一事务内可见）
        audit_log_id = audit_log.id

        db.commit()
    except Exception:
        # 原子性保证：任何一步失败都回滚，绝不留下「半审核」状态
        db.rollback()
        logger.error("审核事务失败已回滚 claim_id=%s action=%s", claim.id, action)
        raise

    db.refresh(claim)

    # 缓存失效（事务提交后执行，避免回滚后缓存与数据库不一致）
    cache.invalidate_item(item.id)
    cache.invalidate_claim(claim.id)

    logger.info(
        "审核完成 claim_id=%s action=%s claim_status=%s item_id=%s item_status=%s operator=%s",
        claim.id,
        action,
        claim.status,
        item.id,
        item.status,
        admin.id,
    )
    return ReviewOutcome(
        claim_id=claim.id,
        action=action,
        claim_status=claim.status,
        item_id=item.id,
        item_status=item.status,
        reviewed_at=claim.reviewed_at,
        audit_log_id=audit_log_id,
    )


# ----------------------------------------------------------------------
# 审计日志查询（契约 §3.13）
# ----------------------------------------------------------------------
def list_audit_logs(
    db: Session, *, query: AuditLogQuery, page: int, page_size: int
) -> tuple[list[dict[str, Any]], int]:
    """分页查询审计日志并完成序列化（`detail` 已由写入侧脱敏）。"""
    from app.schemas.common import split_csv

    actions = split_csv(query.action)
    rows, total = audit_service.query_audit_logs(
        db,
        target_type=query.target_type,
        target_id=query.target_id,
        operator_id=query.operator_id,
        actions=actions or None,
        date_from=query.date_from,
        date_to=query.date_to,
        page=page,
        page_size=page_size,
    )
    usernames = _usernames_for(db, [log.operator_id for log in rows if log.operator_id])
    payload = [
        serializers.serialize_audit_log(log, operator_username=usernames.get(log.operator_id))
        for log in rows
    ]
    return payload, total


__all__ = [
    "ReviewOutcome",
    "AdminClaimFilters",
    "build_claim_detail",
    "review_claim",
    "list_audit_logs",
    "list_claims",
    "claim_counts",
    "serialize_claim_list_item",
    "build_admin_claim_list",
]
