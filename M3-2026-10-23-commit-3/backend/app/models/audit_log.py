"""`audit_logs` 表模型（数据库设计 §3.5）。

约束：
- **只增不改**：应用数据库账号对本表仅有 SELECT / INSERT 权限（见 docs/deployment.md §6.6）；
- `detail` 必须已脱敏，不得含联系方式明文、隐藏特征明文、答案原文、密钥；
- `request_id` 贯穿「请求 → 业务 → 审计」，是排障与答辩演示的关键线索。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("idx_audit_target", "target_type", "target_id", "created_at"),
        Index("idx_audit_operator", "operator_id", "created_at"),
        Index("idx_audit_action_created", "action", "created_at"),
        Index("idx_audit_request", "request_id"),
        {"comment": "审计日志表（只增不改）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="日志 ID")
    operator_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_audit_operator"), nullable=True, comment="操作人（系统动作为 NULL）"
    )
    operator_role: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="操作时角色快照")
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="item/claim/user/config"
    )
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="对象 ID")
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="动作，见审计动作枚举")
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="**已脱敏**详情")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="请求追踪 ID")
    ip_masked: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="脱敏 IP，如 10.1.*.*")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp(), comment="时间(UTC)"
    )

    def to_public_dict(self, operator_username: str | None = None) -> dict[str, Any]:
        """对外（管理员日志页）可展示的结构。"""
        return {
            "id": self.id,
            "operator_id": self.operator_id,
            "operator_username": operator_username,
            "operator_role": self.operator_role,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "action": self.action,
            "detail": self.detail,
            "request_id": self.request_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
