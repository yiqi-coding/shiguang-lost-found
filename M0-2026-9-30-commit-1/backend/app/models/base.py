"""SQLAlchemy 声明式基类与公共 Mixin。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """统一使用不带时区的 UTC 时间（MySQL DATETIME 存储约定）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """所有模型的基类。"""

    def to_dict(self, exclude: set[str] | None = None) -> dict:
        """转换为 dict。**注意：序列化必须经过 schemas 层的脱敏逻辑，不要直接返回给前端。**"""
        exclude = exclude or set()
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
            if c.name not in exclude
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        pk = getattr(self, "id", None)
        return f"<{self.__class__.__name__} id={pk}>"


class TimestampMixin:
    """创建/更新时间字段。数据库侧同样有 DEFAULT CURRENT_TIMESTAMP 兜底。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        server_default=func.current_timestamp(),
        comment="创建时间(UTC)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        server_default=func.current_timestamp(),
        comment="更新时间(UTC)",
    )
