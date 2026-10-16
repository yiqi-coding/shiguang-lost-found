"""我的记录路由（契约 §3.10）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET /api/v1/me/items` | 登录 | 我发布的物品（**本人视角**，含 `hidden_features`） |
| `GET /api/v1/me/claims` | 登录 | 我发起的认领（含关联物品摘要，**不含标准答案**） |
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, DbSession, current_request_id
from app.core.enums import ItemStatus, PostType
from app.core.errors import ValidationError
from app.core.response import ok, paged
from app.schemas.common import PageParams, split_csv
from app.services import claim_service, item_service, serializers
from app.services.item_service import ItemFilters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["我的记录"])

VALID_POST_TYPES = {item.value for item in PostType}
VALID_ITEM_STATUSES = {item.value for item in ItemStatus}


@router.get("/items", summary="我发布的物品", description="本人视角，含隐藏特征；支持 post_type / status 过滤")
def my_items(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    params: Annotated[PageParams, Query()],
    post_type: Annotated[str | None, Query(description="lost / found")] = None,
    status: Annotated[str | None, Query(description="状态，逗号分隔多值")] = None,
) -> dict:
    """我发布的物品（需登录；契约 §3.10）。

    本人视角：`serialize_item(viewer=自己)` 会输出 `hidden_features`，
    这是**唯一**允许普通用户读取自己隐藏特征的路径。
    """
    if post_type and post_type not in VALID_POST_TYPES:
        raise ValidationError(
            "post_type 取值不合法", detail=[{"field": "post_type", "reason": "只能是 lost 或 found"}]
        )
    statuses = split_csv(status)
    invalid = [value for value in statuses if value not in VALID_ITEM_STATUSES]
    if invalid:
        raise ValidationError(
            "status 取值不合法",
            detail=[{"field": "status", "reason": f"不支持的状态：{', '.join(invalid)}"}],
        )

    filters = ItemFilters(
        post_type=post_type,
        statuses=statuses,
        owner_user_id=current_user.id,  # 强制限定本人，忽略任何外部传入
        sort_by="created_at",
        order="desc",
    )
    rows, total = item_service.list_items(
        db, filters=filters, offset=params.offset, limit=params.limit
    )
    payload = [serializers.serialize_item(row, viewer=current_user, detail=True) for row in rows]
    return paged(
        payload,
        page=params.page,
        page_size=params.page_size,
        total=total,
        request_id=current_request_id(request),
    )


@router.get("/claims", summary="我发起的认领", description="含关联物品摘要与得分，不含标准答案")
def my_claims(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    params: Annotated[PageParams, Query()],
    status: Annotated[str | None, Query(description="认领状态，逗号分隔多值")] = None,
) -> dict:
    """我发起的认领（需登录；契约 §3.10）。

    **红线**：不含标准答案、`answers_hash`、`answers_plain`；
    只提供「是否命中 + 得分 + 理由」。
    """
    statuses = split_csv(status)
    rows, total = claim_service.list_my_claims(
        db, applicant=current_user, statuses=statuses or None, offset=params.offset, limit=params.limit
    )
    payload = [claim_service.claim_list_item(claim, viewer=current_user) for claim in rows]
    return paged(
        payload,
        page=params.page,
        page_size=params.page_size,
        total=total,
        request_id=current_request_id(request),
    )


__all__ = ["router"]
