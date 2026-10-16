"""`items` 表模型（数据库设计 §3.2）。

安全要点：
- `hidden_features` 是敏感字段，**任何面向普通用户的序列化路径都必须剔除**；
  统一的收口点是 `app/services/serializers.py::serialize_item()`。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import AIStatus, ItemStatus, PostType
from app.models.base import Base, TimestampMixin


class Item(Base, TimestampMixin):
    __tablename__ = "items"
    __table_args__ = (
        Index("idx_items_match_pool", "post_type", "category", "status", "event_time"),
        Index("idx_items_status_created", "status", "created_at"),
        Index("idx_items_owner", "owner_user_id", "created_at"),
        Index("idx_items_event_time", "event_time"),
        Index("idx_items_category", "category"),
        {"comment": "物品记录表（失物/拾物）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="物品记录 ID")
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_items_owner"), nullable=False, comment="发布者"
    )
    post_type: Mapped[str] = mapped_column(String(10), nullable=False, comment="lost/found")
    category: Mapped[str] = mapped_column(String(64), nullable=False, comment="类别")
    title: Mapped[str] = mapped_column(String(80), nullable=False, comment="标题 5~80 字")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="原始描述 10~2000 字")
    color: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="颜色")
    brand: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="品牌/型号")
    location: Mapped[str] = mapped_column(String(128), nullable=False, comment="校园地点（原始字符串）")
    location_parsed: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="地点解析结果 {campus,building,floor,spot}"
    )
    event_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="事件时间(UTC)")
    public_features: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True, comment="公开特征")
    hidden_features: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, comment="隐藏特征（严格权限：仅发布者与管理员可见）"
    )
    contact_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="脱敏联系方式")

    # ---- AI 相关 ----
    ai_attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="AI 属性提取结果")
    ai_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AIStatus.PENDING, comment="pending/success/failed"
    )
    ai_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="模型名称+版本")
    ai_prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="提示词版本")
    ai_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="分析时间(UTC)")
    attributes_edited_by_user: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, comment="用户是否手工修改过 AI 属性（用户修改优先于 AI）"
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ItemStatus.PUBLISHED, comment="业务状态"
    )

    # ---- 关系 ----
    owner: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="items", foreign_keys=[owner_user_id], lazy="joined"
    )
    images: Mapped[list["ItemImage"]] = relationship(
        "ItemImage",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="ItemImage.sort_order",
        lazy="selectin",
    )

    # ------------------------------------------------------------------
    @property
    def is_lost(self) -> bool:
        return self.post_type == PostType.LOST

    @property
    def is_found(self) -> bool:
        return self.post_type == PostType.FOUND

    @property
    def primary_image(self) -> "ItemImage | None":
        if not self.images:
            return None
        for img in self.images:
            if img.is_primary:
                return img
        return self.images[0]

    def is_owned_by(self, user_id: int | None) -> bool:
        return user_id is not None and self.owner_user_id == user_id

    def hidden_features_as_text(self) -> str:
        """把隐藏特征拼成检索/比对用文本。

        ⚠ 仅用于服务端内部（问题生成、答案比对），**结果不得写入日志或返回前端**。
        """
        parts: list[str] = []
        for feat in self.hidden_features or []:
            if isinstance(feat, dict):
                parts.append(str(feat.get("value") or feat.get("text") or ""))
            else:
                parts.append(str(feat))
        return " ".join(p for p in parts if p)

    def to_search_text(self) -> str:
        """用于匹配的公开文本（标题 + 描述 + 公开特征 + AI 可见特征）。**不含隐藏特征。**"""
        chunks: list[str] = [self.title, self.description]
        for feat in self.public_features or []:
            chunks.append(str(feat.get("value") if isinstance(feat, dict) else feat))
        if self.ai_attributes:
            for key in ("color", "material", "brand", "model"):
                if self.ai_attributes.get(key):
                    chunks.append(str(self.ai_attributes[key]))
            for key in ("visible_features", "keywords", "accessories"):
                for value in self.ai_attributes.get(key) or []:
                    chunks.append(str(value))
        if self.color:
            chunks.append(self.color)
        if self.brand:
            chunks.append(self.brand)
        return " ".join(c for c in chunks if c)


class ItemImage(Base):
    """物品图片。

    说明：需求说明书 §5.2 中 `items` 表只有 `image_key` / `image_url` 两个字段（单图）。
    原型与上传需求要求「每条记录最多 3 张图片」，因此拆出本表；
    `items` 保持一对一主图语义时可继续使用 `primary_image`。
    本变更为**非破坏性扩展**，已登记在 docs/database-design.md。
    """

    __tablename__ = "item_images"
    __table_args__ = (
        Index("idx_item_images_item", "item_id", "sort_order"),
        {"comment": "物品图片表（每条记录最多 3 张）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("items.id", name="fk_item_images_item", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(
        String(512), nullable=False, comment="对象存储 Key：items/{yyyy}/{mm}/{uuid}.{ext}"
    )
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    width: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    height: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sort_order: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_primary: Mapped[int] = mapped_column(TINYINT(1), nullable=False, default=0, comment="是否主图")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    item: Mapped["Item"] = relationship("Item", back_populates="images")
