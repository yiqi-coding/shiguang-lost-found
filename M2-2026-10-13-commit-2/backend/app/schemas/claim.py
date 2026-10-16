"""认领相关 Schema（契约 §3.8 / §3.9 / §3.10）。

红线（契约 §3.8 / §3.9）：

1. 请求与响应**永不包含标准答案**；本模块只承载「问题 + 用户答案」；
2. 用户答案长度上限 500 字，题目至少 1 项、`question_id` 不得重复；
3. 答案原文只允许在内存中参与比对，随后立即以加盐哈希形式保存
   （`app.core.security.hash_answer`），不落库明文、不进日志。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

ANSWER_MAX_LENGTH = 500
QUESTION_ID_MAX = 64
MAX_ANSWERS_PER_SUBMIT = 10


class ClaimCreateRequest(BaseModel):
    """`POST /api/v1/claims` 请求体（契约 §3.8）：`{ "match_id": 3001 }`。"""

    model_config = ConfigDict(extra="ignore")

    match_id: int = Field(..., gt=0, description="匹配记录 ID")


class AnswerItem(BaseModel):
    """单题作答（契约 §3.9 的 `answers` 元素）。"""

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    question_id: str = Field(..., min_length=1, max_length=QUESTION_ID_MAX, description="题目 ID")
    answer: str = Field(..., min_length=1, max_length=ANSWER_MAX_LENGTH, description="作答内容")

    @field_validator("answer")
    @classmethod
    def _check_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("答案不能为空")
        if len(value) > ANSWER_MAX_LENGTH:
            raise ValueError(f"单题答案长度不能超过 {ANSWER_MAX_LENGTH} 字")
        return value


class ClaimVerifyRequest(BaseModel):
    """`POST /api/v1/claims/{claim_id}/verify` 请求体（契约 §3.9）。"""

    model_config = ConfigDict(extra="ignore")

    answers: list[AnswerItem] = Field(
        ..., min_length=1, max_length=MAX_ANSWERS_PER_SUBMIT, description="至少 1 项作答"
    )

    @field_validator("answers")
    @classmethod
    def _check_unique_question_ids(cls, value: list[AnswerItem]) -> list[AnswerItem]:
        if not value:
            raise ValueError("answers 至少需要 1 项")
        seen: set[str] = set()
        for item in value:
            if item.question_id in seen:
                raise ValueError(f"question_id 重复：{item.question_id}")
            seen.add(item.question_id)
        return value

    def answer_map(self) -> dict[str, str]:
        """`{question_id: answer}`（仅内存使用，**禁止写入日志**）。"""
        return {item.question_id: item.answer for item in self.answers}


class ClaimCreateData(BaseModel):
    """`POST /api/v1/claims` 的 201 响应 `data`（契约 §3.8，字段逐字对齐）。"""

    claim_id: int
    match_id: int
    status: str
    questions: list[dict] = Field(default_factory=list)
    max_attempts: int
    expires_at: str | None = None


class ClaimVerifyData(BaseModel):
    """`POST /api/v1/claims/{claim_id}/verify` 的 200 响应 `data`（契约 §3.9）。

    `credibility_score` 仅作辅助依据，必须带免责声明（契约 §3.9 红线 5）。
    """

    claim_id: int
    verification_score: float
    credibility_score: float
    risk_level: str
    status: str
    per_question: list[dict] = Field(default_factory=list)
    risk_tips: list[str] = Field(default_factory=list)
    attempts_left: int
    next_step: str
    disclaimer: str = "可信度分为算法辅助依据，最终归属由管理员审核确认。"


__all__ = [
    "ANSWER_MAX_LENGTH",
    "QUESTION_ID_MAX",
    "MAX_ANSWERS_PER_SUBMIT",
    "ClaimCreateRequest",
    "AnswerItem",
    "ClaimVerifyRequest",
    "ClaimCreateData",
    "ClaimVerifyData",
]
