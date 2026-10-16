"""管理员路由（契约 §3.11 / §3.12 / §3.13）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET  /api/v1/admin/claims/{claim_id}` | 管理员 | 审核台详情聚合 |
| `POST /api/v1/admin/claims/{claim_id}/review` | 管理员 | 审核动作（原子事务） |
| `GET  /api/v1/admin/audit-logs` | 管理员 | 审计日志分页查询 |

红线（契约 §1.6 第 4 条）：**管理员操作必须写入 `audit_logs`（含 `request_id`）**，
由 `admin_service.review_claim()` 在同一事务内完成。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import AdminUser, DbSession, current_request_id, get_client_ip
from app.core.response import ok, paged
from app.schemas.admin import AdminClaimQuery, AuditLogQuery, ClaimReviewRequest
from app.schemas.common import PageParams
from app.services import admin_service
from app.services.claim_service import get_claim_or_404

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["管理员"])


@router.get(
    "/claims",
    summary="待审核认领列表",
    description="管理员专用：审核台左侧待办列表，高风险优先 + 徽标计数",
)
def list_claims(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    params: Annotated[PageParams, Query()],
    query: Annotated[AdminClaimQuery, Query()],
) -> dict:
    """待审核认领列表（管理员；契约 §3.10.2）。

    排序默认**高风险优先**，同风险等级按 `created_at` 升序（先来先审）；
    列表**不含**隐藏特征与答案线索，具体证据走 §3.12 详情接口。
    """
    payload, total, counts = admin_service.build_admin_claim_list(
        db, query=query, page=params.page, page_size=params.page_size, viewer=admin
    )
    body = paged(
        payload,
        page=params.page,
        page_size=params.page_size,
        total=total,
        request_id=current_request_id(request),
    )
    # 契约 §3.10.2：分页结构内额外提供 counts（审核台徽标），避免前端额外请求
    body["data"]["counts"] = counts
    return body


@router.get(
    "/claims/{claim_id}",
    summary="认领审核详情",
    description="管理员专用：聚合 claim / match / lost_item / found_item / verification / history",
)
def get_claim_detail(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    claim_id: int,
) -> dict:
    """审核台详情（管理员；契约 §3.12）。

    `data` 中**不得**包含标准答案原文，只含「是否命中 + 得分 + 理由」。
    """
    claim = get_claim_or_404(db, claim_id)
    data = admin_service.build_claim_detail(db, claim=claim)
    return ok(data, request_id=current_request_id(request))


@router.post(
    "/claims/{claim_id}/review",
    summary="审核认领",
    description="原子事务：改认领 → 按状态机改物品 → 写审计 → 清缓存",
)
def review_claim(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    claim_id: int,
    payload: ClaimReviewRequest,
    ip: Annotated[str, Depends(get_client_ip)],
) -> dict:
    """审核认领（管理员；契约 §3.11）。

    - `approve` → 物品 `pending_review → claimed`（**唯一**能产生 `claimed` 的路径）；
    - `reject` → `pending_review → rejected`；
    - `request_more_info` → `pending_review → request_more_info`；
    - 非法流转返回 `409 STATE_CONFLICT`，事务整体回滚。
    """
    claim = get_claim_or_404(db, claim_id)
    outcome = admin_service.review_claim(
        db, claim=claim, admin=admin, action=payload.action, comment=payload.comment, ip=ip
    )

    data = {
        "claim_id": outcome.claim_id,
        "action": outcome.action,
        "claim_status": outcome.claim_status,
        "item_id": outcome.item_id,
        "item_status": outcome.item_status,
        "reviewed_at": outcome.reviewed_at.isoformat() if outcome.reviewed_at else None,
        "audit_log_id": outcome.audit_log_id,
    }
    return ok(data, message="审核完成", request_id=current_request_id(request))


@router.get(
    "/audit-logs",
    summary="审计日志",
    description="管理员专用：按对象/操作人/动作/时间范围分页查询；detail 已脱敏",
)
def list_audit_logs(
    request: Request,
    db: DbSession,
    admin: AdminUser,
    params: Annotated[PageParams, Query()],
    query: Annotated[AuditLogQuery, Query()],
) -> dict:
    """审计日志分页查询（管理员；契约 §3.13）。

    **红线**：`detail` 必须已脱敏——不得出现联系方式明文、隐藏特征明文、密钥、`Authorization` 头。
    """
    payload, total = admin_service.list_audit_logs(
        db, query=query, page=params.page, page_size=params.page_size
    )
    return paged(
        payload,
        page=params.page,
        page_size=params.page_size,
        total=total,
        request_id=current_request_id(request),
    )


__all__ = ["router"]
