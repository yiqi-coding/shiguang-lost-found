"""`matches` 表模型（数据库设计 §3.3）。

设计要点：
- `uk_matches_pair` 保证同一对 (lost, found) 只有一行，重算走 UPSERT；
- `weights_snapshot` 保存计算时的权重，保证权重调整后历史结果仍可复现（答辩要求）；
- `total_score` 必须等于六维加权和，有单元测试断言（容差 1e-6）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, JSON, String, UniqueConstraint
from sqlalchemy.dialects.mysql import DECIMAL, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

SCORE_PRECISION = (6, 5)  # DECIMAL(6,5) → 0.00000 ~ 9.99999，足以表达 [0,1]


class Match(Base, TimestampMixin):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("lost_item_id", "found_item_id", name="uk_matches_pair"),
        Index("idx_matches_lost_score", "lost_item_id", "total_score"),
        Index("idx_matches_found_score", "found_item_id", "total_score"),
        CheckConstraint("total_score >= 0 AND total_score <= 1", name="chk_matches_total"),
        CheckConstraint("lost_item_id <> found_item_id", name="chk_matches_self"),
        {"comment": "匹配结果表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="匹配 ID")
    lost_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("items.id", name="fk_matches_lost"), nullable=False, comment="失物 ID"
    )
    found_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("items.id", name="fk_matches_found"), nullable=False, comment="拾物 ID"
    )

    # ---- 六维分数 ----
    category_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    attribute_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    image_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    time_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    location_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    brand_model_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)
    total_score: Mapped[Decimal] = mapped_column(DECIMAL(*SCORE_PRECISION), nullable=False, default=0)

    # ---- 可解释性与可复现性 ----
    evidence: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="正向匹配依据")
    uncertainties: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="不确定因素")
    risk_tips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="风险提示")
    weights_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="计算时的权重快照，用于复现与消融实验"
    )
    model_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="baseline-1.0", comment="算法版本"
    )
    is_degraded: Mapped[int] = mapped_column(
        TINYINT(1), nullable=False, default=0, comment="是否为降级（规则/Mock）结果"
    )

    # ---- 关系 ----
    lost_item: Mapped["Item"] = relationship(  # noqa: F821
        "Item", foreign_keys=[lost_item_id], lazy="joined"
    )
    found_item: Mapped["Item"] = relationship(  # noqa: F821
        "Item", foreign_keys=[found_item_id], lazy="joined"
    )

    # ------------------------------------------------------------------
    def score_detail(self) -> dict[str, float]:
        """六维分数（float，用于响应体）。"""
        return {
            "category_score": float(self.category_score or 0),
            "attribute_score": float(self.attribute_score or 0),
            "image_score": float(self.image_score or 0),
            "time_score": float(self.time_score or 0),
            "location_score": float(self.location_score or 0),
            "brand_model_score": float(self.brand_model_score or 0),
        }

    def recompute_total(self, weights: dict[str, float]) -> Decimal:
        """按给定权重重算总分并写回。权重键名与 `score_detail()` 对应。"""
        total = (
            weights["category"] * float(self.category_score or 0)
            + weights["attribute"] * float(self.attribute_score or 0)
            + weights["image"] * float(self.image_score or 0)
            + weights["time"] * float(self.time_score or 0)
            + weights["location"] * float(self.location_score or 0)
            + weights["brand_model"] * float(self.brand_model_score or 0)
        )
        # 量化到 5 位小数，与 DECIMAL(6,5) 对齐，避免浮点尾差
        self.total_score = Decimal(f"{total:.5f}")
        return self.total_score
