"""物品路由（契约 §3.3 / §3.4 / §3.5 / §3.14）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `POST /api/v1/items` | 登录 | multipart 发布，201；AI 失败不阻断发布，存储失败仅影响含图请求 |
| `GET  /api/v1/items` | 公开 | 筛选 + 分页 + 关键词，**列表一律脱敏** |
| `GET  /api/v1/items/{item_id}` | 公开（按角色脱敏） | 详情 |
| `POST /api/v1/items/{item_id}/extract-attributes` | 登录（本人或管理员） | 同步 AI 属性提取 |

红线：

- 物品响应**只能**由 `app/services/serializers.serialize_item()` 产出（字段级权限唯一收口点）；
- `owner_user_id` 过滤参数仅管理员可用，否则 `403 FORBIDDEN`。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import ValidationError as PydanticValidationError

from app.api.deps import (
    CurrentUser,
    DbSession,
    OptionalUser,
    current_request_id,
    raise_validation_error,
    rate_limit,
)
from app.core.enums import Category, ItemStatus, PostType
from app.core.errors import AIServiceError, ForbiddenError, ValidationError
from app.core.response import ok, paged
from app.schemas.common import PaginationParams, split_csv
from app.schemas.item import ExtractAttributesData, ItemCreate
from app.services import item_service, serializers
from app.services.file_service import validate_image_count
from app.services.item_service import IncomingImage, ItemFilters

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/items", tags=["物品"])

VALID_POST_TYPES = {item.value for item in PostType}
VALID_STATUSES = {item.value for item in ItemStatus}
VALID_CATEGORIES = {item.value for item in Category}


# ----------------------------------------------------------------------
# 发布
# ----------------------------------------------------------------------
@router.post(
    "",
    status_code=201,
    summary="发布物品",
    description="multipart/form-data；图片校验顺序：扩展名 → Content-Type → 魔数 → 大小 → Pillow 可解析性",
)
async def create_item(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    post_type: Annotated[str, Form(description="lost / found")],
    category: Annotated[str, Form(description="类别（契约 §2.2）")],
    title: Annotated[str, Form(description="标题 5~80 字")],
    description: Annotated[str, Form(description="描述 10~2000 字")],
    location: Annotated[str, Form(description="校园地点 1~128 字")],
    event_time: Annotated[str, Form(description="ISO 8601 事件时间")],
    hidden_features: Annotated[str, Form(description="隐藏特征（字符串形式的 JSON 数组）")],
    color: Annotated[str | None, Form()] = None,
    brand: Annotated[str | None, Form()] = None,
    public_features: Annotated[str | None, Form(description="公开特征（字符串形式的 JSON）")] = None,
    contact_id: Annotated[str | None, Form(description="联系方式，入库前自动脱敏")] = None,
    image: Annotated[list[UploadFile] | None, File(description="图片，最多 3 张（重复该字段名）")] = None,
    _limit: Annotated[None, Depends(rate_limit("item_create", 30, 3600))] = None,
) -> dict:
    """发布物品（需登录，契约 §3.3）。

    **降级行为（强制）**：

    - AI/算法不可用 → 物品照常创建成功，`ai_status="failed"`，`message` 提示手动补充属性；
    - 存储不可用 → 含图请求返回 `502 STORAGE_SERVICE_ERROR`，无图请求正常创建。
    """
    try:
        payload = ItemCreate(
            post_type=post_type,
            category=category,
            title=title,
            description=description,
            location=location,
            event_time=event_time,
            hidden_features=hidden_features,
            color=color,
            brand=brand,
            public_features=public_features,
            contact_id=contact_id,
        )
    except PydanticValidationError as exc:
        raise_validation_error(exc, message="发布参数校验失败")

    files = [item for item in (image or []) if item is not None]
    validate_image_count(len(files))
    incoming = [
        IncomingImage(filename=upload.filename, content_type=upload.content_type, data=await upload.read())
        for upload in files
    ]

    item, attribute_result = item_service.create_item(
        db, owner=current_user, payload=payload, images=incoming
    )
    data = item_service.publish_result_data(item, owner=current_user)
    message = "发布成功" if attribute_result.succeeded else attribute_result.message
    return ok(data, message=message, request_id=current_request_id(request))


# ----------------------------------------------------------------------
# 列表
# ----------------------------------------------------------------------
@router.get("", summary="物品列表", description="公开接口，按角色脱敏；支持筛选、分页与关键词")
def list_items(
    request: Request,
    db: DbSession,
    viewer: OptionalUser,
    params: PaginationParams,
    post_type: Annotated[str | None, Query(description="lost / found")] = None,
    category: Annotated[str | None, Query(description="类别精确匹配")] = None,
    status: Annotated[str | None, Query(description="状态，逗号分隔多值")] = None,
    keyword: Annotated[str | None, Query(description="在标题/描述/AI 关键词中模糊匹配")] = None,
    location: Annotated[str | None, Query(description="地点模糊匹配")] = None,
    time_from: Annotated[datetime | None, Query(description="事件时间下界（ISO 8601）")] = None,
    time_to: Annotated[datetime | None, Query(description="事件时间上界（ISO 8601）")] = None,
    owner_user_id: Annotated[int | None, Query(description="仅管理员可用")] = None,
    sort_by: Annotated[str, Query(description="created_at / event_time / id")] = "created_at",
    order: Annotated[str, Query(description="asc / desc")] = "desc",
) -> dict:
    """物品列表（公开，按角色脱敏；契约 §3.4）。

    **红线**：响应中绝不出现 `hidden_features`、`owner_user_id`、
    `contact_id` 明文与 `image_key`（仅给签名 URL）。
    """
    if post_type and post_type not in VALID_POST_TYPES:
        raise ValidationError(
            "post_type 取值不合法",
            detail=[{"field": "post_type", "reason": "只能是 lost 或 found"}],
        )
    if category and category not in VALID_CATEGORIES:
        raise ValidationError(
            "category 取值不合法",
            detail=[{"field": "category", "reason": f"只能取：{' / '.join(sorted(VALID_CATEGORIES))}"}],
        )
    statuses = split_csv(status)
    invalid = [value for value in statuses if value not in VALID_STATUSES]
    if invalid:
        raise ValidationError(
            "status 取值不合法",
            detail=[{"field": "status", "reason": f"不支持的状态：{', '.join(invalid)}"}],
        )
    if owner_user_id is not None and not (viewer and viewer.is_admin):
        # 越权查询他人发布记录 → 403（不泄露是否存在）
        raise ForbiddenError("按发布者过滤仅管理员可用")

    filters = ItemFilters(
        post_type=post_type,
        category=category,
        statuses=statuses,
        keyword=keyword,
        location=location,
        time_from=time_from,
        time_to=time_to,
        owner_user_id=owner_user_id,
        sort_by=sort_by,
        order=order,
    )
    rows, total = item_service.list_items(
        db, filters=filters, offset=params.offset, limit=params.limit
    )
    payload = [serializers.serialize_item(row, viewer=viewer, detail=False) for row in rows]
    return paged(
        payload,
        page=params.page,
        page_size=params.page_size,
        total=total,
        request_id=current_request_id(request),
    )


# ----------------------------------------------------------------------
# 详情
# ----------------------------------------------------------------------
@router.get("/{item_id}", summary="物品详情", description="公开接口，按角色脱敏（契约 §3.5）")
def get_item(
    request: Request,
    db: DbSession,
    viewer: OptionalUser,
    item_id: int,
) -> dict:
    """物品详情（公开；契约 §3.5）。

    `hidden_features` / `contact_id` / `image_key` 仅发布者本人与管理员可见，
    是否输出完全由 `serialize_item()` 决定。
    """
    item = item_service.get_item_or_404(db, item_id)
    return ok(
        serializers.serialize_item(item, viewer=viewer, detail=True),
        request_id=current_request_id(request),
    )


# ----------------------------------------------------------------------
# AI 属性提取
# ----------------------------------------------------------------------
@router.post(
    "/{item_id}/extract-attributes",
    summary="AI 属性提取",
    description="同步提取物品属性；仅发布者本人或管理员可用",
)
def extract_attributes(
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    item_id: int,
    _limit: Annotated[None, Depends(rate_limit("ai_extract", 30, 3600))] = None,
) -> dict:
    """AI 属性提取（登录 + 本人/管理员；契约 §3.14）。

    降级行为：算法与 MaaS 均不可用时**仍写回规则提取结果**并置 `ai_status=failed`，
    同时按契约返回 `502 AI_SERVICE_ERROR`（物品本身不受影响，可稍后重试）。
    """
    item = item_service.get_item_or_404(db, item_id)
    if not current_user.is_admin and item.owner_user_id != current_user.id:
        raise ForbiddenError("只能对自己的物品执行属性提取")

    result = item_service.extract_attributes_for_route(db, item=item, force=True)

    if not result.succeeded:
        raise AIServiceError(
            result.message or "AI 分析服务暂不可用，请稍后重试或手动补充物品属性",
            detail=[{"field": "ai", "reason": "已降级为规则提取（ai_status=failed）"}],
        )

    data = ExtractAttributesData(
        item_id=item.id,
        ai_status=result.ai_status,
        ai_attributes=result.attributes,
        ai_model_version=result.ai_model_version,
        ai_prompt_version=result.ai_prompt_version,
        ai_analyzed_at=result.ai_analyzed_at.isoformat() if result.ai_analyzed_at else None,
        degraded=result.degraded,
        message=result.message,
    )
    return ok(data.model_dump(), message=result.message, request_id=current_request_id(request))


__all__ = ["router"]
