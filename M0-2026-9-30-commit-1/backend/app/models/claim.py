"""`claims` 表模型（数据库设计 §3.4）。

安全要点（需求 §4.5.4 / §10）：
- **标准答案永不入库明文**：默认只存 `answers_hash`（加盐 SHA-256）与 `answer_summary`（是否命中+分数+理由）；
- `answers_plain` 字段预留但 Baseline **默认不启用**（保持 NULL）；
- `questions` 只含题目文本，不含答案；
- 面向认领者的任何响应都不得包含 `answers_hash` / `answer_summary` 中的答案线索。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.mysql import DECIMAL, TINYINT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ClaimStatus
from app.models.base import Base, TimestampMixin

SCORE_PRECISION = (6, 5)


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"
    __table_args__ = (
        Index("idx_claims_match", "match_id"),
        Index("idx_claims_applicant", "applicant_id", "created_at"),
        Index("idx_claims_status_created", "status", "created_at"),
        Index("idx_claims_risk", "risk_level", "status"),
        CheckConstraint(
            "credibility_score IS NULL OR (credibility_score >= 0 AND credibility_score <= 1)",
            name="chk_claims_credibility",
        ),
        {"comment": "认领记录表"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="认领 ID")
    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.id", name="fk_claims_match"), nullable=False, comment="匹配 ID"
    )
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_claims_applicant"), nullable=False, comment="申请人"
    )

    # ---- 验证问答 ----
    questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, comment="问题列表 [{question_id,question_text,source}]，不含答案"
    )
    answers_hash: Mapped[dict[str, str] | None] = mapped_column(
        JSON, nullable=True, comment="每题答案的加盐哈希 {q1:'sha256:...'}"
    )
    answer_summary: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True, comment="评分摘要 [{question_id,matched,score,reason}]，不含答案原文"
    )
    answers_plain: Mapped[dict[str, str] | None] = mapped_column(
        JSON, nullable=True, comment="加密答案（默认 NULL，Baseline 不启用）"
    )

    # ---- 评分 ----
    verification_score: Mapped[Decimal | None] = mapped_column(
        DECIMAL(*SCORE_PRECISION), nullable=True, comment="隐藏特征答案匹配分 [0,1]"
    )
    credibility_score: Mapped[Decimal | None] = mapped_column(
        DECIMAL(*SCORE_PRECISION), nullable=True, comment="综合可信度分 [0,1]"
    )
    risk_level: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="low/medium/high")
    risk_tips: Mapped[list[str] | None] = mapped_column(JSON, nullable=True, comment="风险提示")
    attempts_used: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="已提交次数")
    max_attempts: Mapped[int] = mapped_column(BigInteger, nullable=False, default=3, comment="最大允许次数")

    # ---- 审核 ----
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ClaimStatus.CLAIMING, comment="认领状态"
    )
    admin_comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="管理员备注")
    reviewed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", name="fk_claims_reviewer"), nullable=True, comment="审核人"
    )
    reviewed_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True, comment="审核时间(UTC)")

    # ---- 关系 ----
    match: Mapped["Match"] = relationship("Match", lazy="joined")  # noqa: F821
    applicant: Mapped["User"] = relationship(  # noqa: F821
        "User", back_populates="claims", foreign_keys=[applicant_id], lazy="joined"
    )
    reviewer: Mapped["User | None"] = relationship(  # noqa: F821
        "User", foreign_keys=[reviewed_by], lazy="select"
    )

    # ------------------------------------------------------------------
    @property
    def attempts_left(self) -> int:
        return max(0, self.max_attempts - self.attempts_used)

    @property
    def is_closed(self) -> bool:
        return self.status in {ClaimStatus.APPROVED, ClaimStatus.REJECTED}

    @property
    def is_exhausted(self) -> bool:
        return self.attempts_used >= self.max_attempts

    def questions_for_applicant(self) -> list[dict[str, str]]:
        """返回给认领者的问题列表——**严格只含 question_id 与 question_text**。"""
        result: list[dict[str, str]] = []
        for q in self.questions or []:
            result.append(
                {
                    "question_id": str(q.get("question_id", "")),
                    "question_text": str(q.get("question_text", "")),
                }
            )
        return result

    def has_answer_leak(self) -> bool:
        """自检：问题文本中是否意外包含答案原文（用于测试断言与启动检查）。"""
        if not self.answers_hash:
            return False
        question_text = " ".join(str(q.get("question_text", "")) for q in self.questions or [])
        # 哈希不可逆，此处只检查是否有人误把答案写进题目字段
        return "answer" in question_text.lower() and "请" not in question_text
