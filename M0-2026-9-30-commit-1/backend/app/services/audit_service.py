"""审计日志写入（需求 §4.1.2 红线 4：管理员操作必须写入 audit_logs）。

使用要点：
- 写入前必须过 `sanitize_detail()`，剔除敏感键；
- `detail` 只记录**摘要**（长度、动作、前后状态），不记录原文；
- 与业务操作在同一事务内提交，保证「操作成功但日志丢失」不会发生。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditAction, AuditTargetType
from app.core.request_id import get_request_id
from app.core.security import mask_ip
from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

# 绝不允许出现在审计详情里的键
_FORBIDDEN_DETAIL_KEYS = {
    "hidden_features",
    "answer",
    "answers",
    "answers_plain",
    "standard_answer",
    "password",
    "password_hash",
    "token",
    "access_token",
    "authorization",
    "secret",
    "api_key",
    "access_key",
    "obs_secret_key",
    "maas_api_key",
    "contact_id",  # 联系方式在审计详情中只记录「是否有」
}

_MAX_DETAIL_STR_LEN = 120


def sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any] | None:
    """脱敏审计详情：剔除敏感键、截断长文本、替换长字符串为长度标记。"""
    if not detail:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in detail.items():
        if str(key).lower() in _FORBIDDEN_DETAIL_KEYS:
            cleaned[f"{key}_present"] = bool(value)
            continue
        if isinstance(value, str):
            cleaned[key] = value if len(value) <= _MAX_DETAIL_STR_LEN else {"length": len(value)}
        elif isinstance(value, dict):
            cleaned[key] = sanitize_detail(value)
        elif isinstance(value, list):
            cleaned[key] = f"[{len(value)} items]"
        else:
            cleaned[key] = value
    return cleaned


def write_audit_log(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: int | None,
    operator: User | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
    request_id: str | None = None,
    commit: bool = False,
) -> AuditLog:
    """写入一条审计日志。

    :param commit: 为 True 时立即提交（用于独立于业务事务的场景，如登录）
    """
    log = AuditLog(
        operator_id=operator.id if operator else None,
        operator_role=operator.role if operator else None,
        target_type=target_type,
        target_id=target_id,
        action=action,
        detail=sanitize_detail(detail),
        request_id=request_id or get_request_id() or None,
        ip_masked=mask_ip(ip),
    )
    db.add(log)
    if commit:
        db.commit()
    logger.info(
        "审计日志：action=%s target=%s#%s operator=%s",
        action,
        target_type,
        target_id,
        operator.username if operator else "system",
    )
    return log


# ----------------------------------------------------------------------
# 语义化封装（推荐在业务代码中使用这些函数，避免拼错 action 字符串）
# ----------------------------------------------------------------------


def log_item_create(db: Session, *, item_id: int, operator: User, post_type: str, ai_status: str) -> None:
    write_audit_log(
        db,
        action=AuditAction.ITEM_CREATE,
        target_type=AuditTargetType.ITEM,
        target_id=item_id,
        operator=operator,
        detail={"post_type": post_type, "ai_status": ai_status},
    )


def log_item_status_change(
    db: Session, *, item_id: int, operator: User | None, from_status: str, to_status: str, reason: str
) -> None:
    write_audit_log(
        db,
        action=AuditAction.ITEM_STATUS_CHANGE,
        target_type=AuditTargetType.ITEM,
        target_id=item_id,
        operator=operator,
        detail={"from": from_status, "to": to_status, "reason": reason},
    )


def log_match_compute(
    db: Session, *, item_id: int, operator: User | None, model_version: str, candidate_count: int, degraded: bool
) -> None:
    write_audit_log(
        db,
        action=AuditAction.MATCH_COMPUTE,
        target_type=AuditTargetType.ITEM,
        target_id=item_id,
        operator=operator,
        detail={
            "model_version": model_version,
            "candidate_count": candidate_count,
            "degraded": degraded,
        },
    )


def log_claim_create(db: Session, *, claim_id: int, operator: User, match_id: int, question_count: int) -> None:
    write_audit_log(
        db,
        action=AuditAction.CLAIM_CREATE,
        target_type=AuditTargetType.CLAIM,
        target_id=claim_id,
        operator=operator,
        detail={"match_id": match_id, "question_count": question_count},
    )


def log_claim_verify(
    db: Session,
    *,
    claim_id: int,
    operator: User,
    verification_score: float,
    credibility_score: float,
    risk_level: str,
    passed: bool,
) -> None:
    """记录验证结果。**只记分数与结论，绝不记录答案原文。**"""
    write_audit_log(
        db,
        action=AuditAction.CLAIM_VERIFY if passed else AuditAction.CLAIM_VERIFY_FAILED,
        target_type=AuditTargetType.CLAIM,
        target_id=claim_id,
        operator=operator,
        detail={
            "verification_score": round(verification_score, 5),
            "credibility_score": round(credibility_score, 5),
            "risk_level": risk_level,
            "passed": passed,
        },
    )


def log_claim_review(
    db: Session, *, claim_id: int, operator: User, action: str, comment: str | None
) -> None:
    write_audit_log(
        db,
        action=AuditAction.CLAIM_REVIEW,
        target_type=AuditTargetType.CLAIM,
        target_id=claim_id,
        operator=operator,
        detail={
            "action": action,
            "comment_length": len(comment) if comment else 0,
            "comment": comment,  # 管理员备注可记录，但会被长度截断保护
        },
    )


def log_user_login(db: Session, *, user: User, ip: str | None, success: bool) -> None:
    write_audit_log(
        db,
        action=AuditAction.USER_LOGIN,
        target_type=AuditTargetType.USER,
        target_id=user.id,
        operator=user if success else None,
        detail={"success": success, "role": user.role},
        ip=ip,
        commit=True,
    )


# ----------------------------------------------------------------------
# 查询（管理员日志页）
# ----------------------------------------------------------------------


def query_audit_logs(
    db: Session,
    *,
    target_type: str | None = None,
    target_id: int | None = None,
    operator_id: int | None = None,
    actions: list[str] | None = None,
    date_from=None,
    date_to=None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[AuditLog], int]:
    """按条件分页查询审计日志。"""
    stmt = select(AuditLog)
    count_stmt = select(AuditLog.id)

    conditions = []
    if target_type:
        conditions.append(AuditLog.target_type == target_type)
    if target_id is not None:
        conditions.append(AuditLog.target_id == target_id)
    if operator_id is not None:
        conditions.append(AuditLog.operator_id == operator_id)
    if actions:
        conditions.append(AuditLog.action.in_(actions))
    if date_from is not None:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.created_at <= date_to)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = len(db.execute(count_stmt).scalars().all())
    rows = (
        db.execute(stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        .scalars()
        .all()
    )
    return list(rows), total
