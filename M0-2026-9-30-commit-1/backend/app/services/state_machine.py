"""物品状态机（数据库设计 §2.3 / API 契约附录 B）。

**唯一**允许修改 `items.status` 的地方。任何路由都不得直接赋值 `item.status = ...`。
非法跳转抛 `StateConflictError`（HTTP 409）。
"""

from __future__ import annotations

from app.core.enums import ClaimStatus, ItemStatus, ReviewAction
from app.core.errors import StateConflictError

# 物品状态 → 允许的下一状态集合
ITEM_TRANSITIONS: dict[str, set[str]] = {
    ItemStatus.PUBLISHED: {ItemStatus.MATCHED, ItemStatus.SUSPENDED, ItemStatus.CLOSED},
    ItemStatus.MATCHED: {ItemStatus.CLAIMING, ItemStatus.SUSPENDED, ItemStatus.CLOSED},
    ItemStatus.CLAIMING: {
        ItemStatus.PENDING_REVIEW,
        ItemStatus.MATCHED,  # 认领人撤回
        ItemStatus.SUSPENDED,
    },
    ItemStatus.PENDING_REVIEW: {
        ItemStatus.CLAIMED,
        ItemStatus.REJECTED,
        ItemStatus.REQUEST_MORE_INFO,
        ItemStatus.DISPUTED,
    },
    ItemStatus.REQUEST_MORE_INFO: {
        ItemStatus.PENDING_REVIEW,
        ItemStatus.REJECTED,
        ItemStatus.CLOSED,
    },
    ItemStatus.CLAIMED: {ItemStatus.CLOSED},
    ItemStatus.REJECTED: {ItemStatus.CLAIMING, ItemStatus.CLOSED},
    ItemStatus.SUSPENDED: {ItemStatus.PUBLISHED, ItemStatus.MATCHED},
    ItemStatus.DISPUTED: {ItemStatus.CLAIMED, ItemStatus.REJECTED, ItemStatus.CLOSED},
    ItemStatus.CLOSED: set(),  # 终态
}

# 认领状态 → 允许的下一状态集合
CLAIM_TRANSITIONS: dict[str, set[str]] = {
    ClaimStatus.CLAIMING: {ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED},
    ClaimStatus.PENDING_REVIEW: {
        ClaimStatus.APPROVED,
        ClaimStatus.REJECTED,
        ClaimStatus.REQUEST_MORE_INFO,
    },
    ClaimStatus.REQUEST_MORE_INFO: {ClaimStatus.PENDING_REVIEW, ClaimStatus.REJECTED},
    ClaimStatus.APPROVED: set(),
    ClaimStatus.REJECTED: set(),
}

# 管理员审核动作 → 目标物品状态
REVIEW_ACTION_TO_ITEM_STATUS: dict[str, str] = {
    ReviewAction.APPROVE: ItemStatus.CLAIMED,
    ReviewAction.REJECT: ItemStatus.REJECTED,
    ReviewAction.REQUEST_MORE_INFO: ItemStatus.REQUEST_MORE_INFO,
}

# 管理员审核动作 → 目标认领状态
REVIEW_ACTION_TO_CLAIM_STATUS: dict[str, str] = {
    ReviewAction.APPROVE: ClaimStatus.APPROVED,
    ReviewAction.REJECT: ClaimStatus.REJECTED,
    ReviewAction.REQUEST_MORE_INFO: ClaimStatus.REQUEST_MORE_INFO,
}


def can_transition_item(current: str, target: str) -> bool:
    return target in ITEM_TRANSITIONS.get(current, set())


def can_transition_claim(current: str, target: str) -> bool:
    return target in CLAIM_TRANSITIONS.get(current, set())


def assert_item_transition(current: str, target: str) -> None:
    """校验物品状态流转，非法则抛 409。"""
    if current == target:
        return
    if not can_transition_item(current, target):
        raise StateConflictError(f"当前状态（{current}）不允许变更为（{target}）")


def assert_claim_transition(current: str, target: str) -> None:
    if current == target:
        return
    if not can_transition_claim(current, target):
        raise StateConflictError(f"认领当前状态（{current}）不允许变更为（{target}）")


def next_status_for_review(action: str) -> tuple[str, str]:
    """管理员审核动作 → (目标认领状态, 目标物品状态)。"""
    if action not in REVIEW_ACTION_TO_CLAIM_STATUS:
        raise StateConflictError(f"不支持的审核动作：{action}")
    return REVIEW_ACTION_TO_CLAIM_STATUS[action], REVIEW_ACTION_TO_ITEM_STATUS[action]


def is_terminal_item_status(status: str) -> bool:
    return not ITEM_TRANSITIONS.get(status, set())


def allowed_next_statuses(status: str) -> list[str]:
    """供 OpenAPI 文档/前端展示可用操作。"""
    return sorted(ITEM_TRANSITIONS.get(status, set()))
