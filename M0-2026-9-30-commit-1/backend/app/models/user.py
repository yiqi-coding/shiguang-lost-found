"""`users` 表模型（数据库设计 §3.1）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import UserRole, UserStatus
from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index("uk_users_username", "username", unique=True),
        Index("idx_users_role_status", "role", "status"),
        {"comment": "用户表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="用户 ID")
    username: Mapped[str] = mapped_column(String(64), nullable=False, comment="昵称/账号")
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserRole.STUDENT, comment="student/admin"
    )
    campus_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="所属校区")
    contact_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="**已脱敏**联系方式，如 138****8888"
    )
    password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="密码哈希（bcrypt/argon2），禁止 MD5/SHA1"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserStatus.ACTIVE, comment="active/disabled"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最近登录时间(UTC)")

    # 关系（仅用于内部查询，接口输出必须走 schemas 脱敏）
    items: Mapped[list["Item"]] = relationship(  # noqa: F821
        "Item", back_populates="owner", foreign_keys="Item.owner_user_id", lazy="select"
    )
    claims: Mapped[list["Claim"]] = relationship(  # noqa: F821
        "Claim", back_populates="applicant", foreign_keys="Claim.applicant_id", lazy="select"
    )

    # ------------------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def to_public_dict(self) -> dict:
        """对外（含审核台）可展示的用户摘要。**绝不包含 password_hash。**"""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "campus_id": self.campus_id,
            "contact_id": self.contact_id,  # 入库前已脱敏
        }
