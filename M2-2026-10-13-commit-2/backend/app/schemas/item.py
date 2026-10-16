"""物品 Schema（契约 §3.3 / §3.4 / §3.5 / §3.14）。

字段名、长度约束、JSON 字段形态**逐条对齐** `docs/api-contract.md` §3.3 的表单说明：

| 字段 | 必填 | 校验规则 |
|---|---|---|
| `post_type` | 是 | `lost` / `found` |
| `category` | 是 | 见契约 §2.2 |
| `title` | 是 | 5~80 字（去首尾空格后校验） |
| `description` | 是 | 10~2000 字 |
| `location` | 是 | 1~128 字 |
| `event_time` | 是 | ≤ 当前时间 +5 分钟；≥ 当前时间 -2 年 |
| `hidden_features` | 是 | 非空；序列化后 ≤ 2000 字 |
| `color` | 否 | ≤ 32 字 |
| `brand` | 否 | ≤ 64 字 |
| `public_features` | 否 | 数组或对象 |
| `contact_id` | 否 | ≤ 128 字；**入库前必须脱敏** |

红线：`contact_id` 在**校验阶段**就调用 `app.core.security.mask_contact()` 完成脱敏，
保证任何后续代码路径拿到的都已经是脱敏值，不存在「忘记脱敏」的可能。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import AIStatus, Category, ItemStatus, PostType
from app.core.security import mask_contact
from app.schemas.common import parse_json_text, to_utc_naive

TITLE_MIN = 5
TITLE_MAX = 80
DESCRIPTION_MIN = 10
DESCRIPTION_MAX = 2000
LOCATION_MIN = 1
LOCATION_MAX = 128
COLOR_MAX = 32
BRAND_MAX = 64
CONTACT_MAX = 128

# 事件时间容差（契约 §3.3）
EVENT_TIME_FUTURE_TOLERANCE = timedelta(minutes=5)
EVENT_TIME_PAST_LIMIT = timedelta(days=365 * 2)

# 隐藏特征序列化后的长度上限（契约 §3.3）
HIDDEN_FEATURES_MAX_LEN = 2000

VALID_POST_TYPES = tuple(item.value for item in PostType)
VALID_CATEGORIES = tuple(item.value for item in Category)


def _utcnow_naive() -> datetime:
    """当前 UTC 时间（不带时区，与 MySQL DATETIME 存储一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ItemCreate(BaseModel):
    """`POST /api/v1/items` 的表单字段（multipart/form-data）。

    路由把 `Form(...)` 收到的原始值交给本模型完成全部语义校验，
    校验失败由全局异常处理器统一转成 `400 VALIDATION_ERROR` + 字段级 detail。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    # ---- 必填 ----
    post_type: str = Field(..., description="lost / found")
    category: str = Field(..., description="类别（契约 §2.2）")
    title: str = Field(..., description=f"标题 {TITLE_MIN}~{TITLE_MAX} 字")
    description: str = Field(..., description=f"描述 {DESCRIPTION_MIN}~{DESCRIPTION_MAX} 字")
    location: str = Field(..., description=f"校园地点 {LOCATION_MIN}~{LOCATION_MAX} 字")
    event_time: datetime = Field(..., description="ISO 8601 事件时间")
    hidden_features: list[Any] = Field(..., description="隐藏特征（JSON 数组，仅本人与管理员可见）")

    # ---- 可选 ----
    color: str | None = Field(None, description=f"颜色，≤{COLOR_MAX} 字")
    brand: str | None = Field(None, description=f"品牌/型号，≤{BRAND_MAX} 字")
    public_features: list[Any] | dict[str, Any] | None = Field(None, description="对外展示特征")
    contact_id: str | None = Field(None, description=f"联系方式，≤{CONTACT_MAX} 字，**入库前脱敏**")

    # ------------------------------------------------------------------
    # 枚举校验
    # ------------------------------------------------------------------
    @field_validator("post_type")
    @classmethod
    def _check_post_type(cls, value: str) -> str:
        if value not in VALID_POST_TYPES:
            raise ValueError(f"post_type 只能是 {' / '.join(VALID_POST_TYPES)}")
        return value

    @field_validator("category")
    @classmethod
    def _check_category(cls, value: str) -> str:
        if value not in VALID_CATEGORIES:
            raise ValueError(f"category 只能取：{' / '.join(VALID_CATEGORIES)}")
        return value

    # ------------------------------------------------------------------
    # 文本长度校验
    # ------------------------------------------------------------------
    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        length = len(value)
        if length < TITLE_MIN or length > TITLE_MAX:
            raise ValueError(f"标题长度需在 {TITLE_MIN}~{TITLE_MAX} 字之间（当前 {length} 字）")
        return value

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str) -> str:
        length = len(value)
        if length < DESCRIPTION_MIN or length > DESCRIPTION_MAX:
            raise ValueError(
                f"描述长度需在 {DESCRIPTION_MIN}~{DESCRIPTION_MAX} 字之间（当前 {length} 字）"
            )
        return value

    @field_validator("location")
    @classmethod
    def _check_location(cls, value: str) -> str:
        length = len(value)
        if length < LOCATION_MIN or length > LOCATION_MAX:
            raise ValueError(f"地点长度需在 {LOCATION_MIN}~{LOCATION_MAX} 字之间（当前 {length} 字）")
        return value

    @field_validator("color")
    @classmethod
    def _check_color(cls, value: str | None) -> str | None:
        if value and len(value) > COLOR_MAX:
            raise ValueError(f"颜色长度不能超过 {COLOR_MAX} 字")
        return value or None

    @field_validator("brand")
    @classmethod
    def _check_brand(cls, value: str | None) -> str | None:
        if value and len(value) > BRAND_MAX:
            raise ValueError(f"品牌/型号长度不能超过 {BRAND_MAX} 字")
        return value or None

    # ------------------------------------------------------------------
    # 事件时间
    # ------------------------------------------------------------------
    @field_validator("event_time")
    @classmethod
    def _check_event_time(cls, value: datetime) -> datetime:
        normalized = to_utc_naive(value)
        now = _utcnow_naive()
        if normalized > now + EVENT_TIME_FUTURE_TOLERANCE:
            raise ValueError("事件时间不能晚于当前时间（允许 5 分钟时钟误差）")
        if normalized < now - EVENT_TIME_PAST_LIMIT:
            raise ValueError("事件时间不能早于两年前")
        return normalized

    # ------------------------------------------------------------------
    # JSON 字段（表单里是字符串形式的 JSON）
    # ------------------------------------------------------------------
    @field_validator("hidden_features", mode="before")
    @classmethod
    def _parse_hidden_features(cls, value: Any) -> Any:
        parsed = parse_json_text(value, field="隐藏特征", expect="array", allow_none=False)
        if not parsed:
            raise ValueError("隐藏特征不能为空，请至少填写 1 条只有失主/拾到者才知道的特征")
        cleaned: list[Any] = []
        for entry in parsed:
            if isinstance(entry, str) and not entry.strip():
                continue
            cleaned.append(entry.strip() if isinstance(entry, str) else entry)
        if not cleaned:
            raise ValueError("隐藏特征不能为空，请至少填写 1 条有效特征")
        return cleaned

    @field_validator("hidden_features")
    @classmethod
    def _check_hidden_features_length(cls, value: list[Any]) -> list[Any]:
        # 契约：序列化后 ≤ 2000 字（按 UTF-8 中文不转义计算，贴近真实存储体积）
        serialized = json.dumps(value, ensure_ascii=False, default=str)
        if len(serialized) > HIDDEN_FEATURES_MAX_LEN:
            raise ValueError(
                f"隐藏特征内容过长（序列化后 {len(serialized)} 字，上限 {HIDDEN_FEATURES_MAX_LEN} 字）"
            )
        return value

    @field_validator("public_features", mode="before")
    @classmethod
    def _parse_public_features(cls, value: Any) -> Any:
        return parse_json_text(value, field="公开特征", expect="array_or_object", allow_none=True)

    # ------------------------------------------------------------------
    # 联系方式：入库前必须脱敏（安全红线）
    # ------------------------------------------------------------------
    @field_validator("contact_id")
    @classmethod
    def _mask_contact_id(cls, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) > CONTACT_MAX:
            raise ValueError(f"联系方式长度不能超过 {CONTACT_MAX} 字")
        # ⚠ 此处是联系方式进入系统的唯一入口，必须先脱敏再落库
        return mask_contact(value)

    # ------------------------------------------------------------------
    def to_item_kwargs(self, *, owner_user_id: int) -> dict[str, Any]:
        """转换为 `Item` 模型构造参数（字段名与数据库列一一对应）。"""
        return {
            "owner_user_id": owner_user_id,
            "post_type": self.post_type,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "color": self.color,
            "brand": self.brand,
            "location": self.location,
            "event_time": self.event_time,
            "public_features": self.public_features,
            "hidden_features": self.hidden_features,
            "contact_id": self.contact_id,  # 已在校验器中脱敏
            "status": ItemStatus.PUBLISHED,
            "ai_status": AIStatus.PENDING,
        }


class ItemPublishImage(BaseModel):
    """发布响应中的图片条目（契约 §3.3 的 `images` 元素：key / url / size）。"""

    key: str
    url: str | None = None
    size: int | None = None


class ItemPublishData(BaseModel):
    """`POST /api/v1/items` 的 201 响应 `data`（契约 §3.3，字段**逐字对齐**）。"""

    id: int
    post_type: str
    status: str
    ai_status: str
    images: list[ItemPublishImage] = Field(default_factory=list)
    ai_attributes: dict[str, Any] | None = None


class ExtractAttributesData(BaseModel):
    """`POST /api/v1/items/{item_id}/extract-attributes` 的响应 `data`（契约 §3.14 + §4.3）。"""

    item_id: int
    ai_status: str
    ai_attributes: dict[str, Any] | None = None
    ai_model_version: str | None = None
    ai_prompt_version: str | None = None
    ai_analyzed_at: str | None = None
    degraded: bool = Field(False, description="True 表示算法/MaaS 不可用，已降级为规则提取")
    message: str = "success"


__all__ = [
    "TITLE_MIN",
    "TITLE_MAX",
    "DESCRIPTION_MIN",
    "DESCRIPTION_MAX",
    "LOCATION_MIN",
    "LOCATION_MAX",
    "HIDDEN_FEATURES_MAX_LEN",
    "VALID_POST_TYPES",
    "VALID_CATEGORIES",
    "ItemCreate",
    "ItemPublishImage",
    "ItemPublishData",
    "ExtractAttributesData",
]
