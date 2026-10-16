"""管理员 Schema（契约 §3.11 / §3.12 / §3.13）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import AuditTargetType, ClaimStatus, ReviewAction, RiskLevel
from app.schemas.common import split_csv

COMMENT_MIN = 10
COMMENT_MAX = 500

ReviewActionLiteral = Literal["approve", "reject", "request_more_info"]


class ClaimReviewRequest(BaseModel):
    """`POST /api/v1/admin/claims/{claim_id}/review` 请求体（契约 §3.11）。

    | 字段 | 必填 | 规则 |
    |---|---|---|
    | `action` | 是 | `approve` / `reject` / `request_more_info` |
    | `comment` | 条件必填 | `reject` 与 `request_more_info` 时**必填**，10~500 字；`approve` 可选 |

    条件必填用 `model_validator(mode="after")` 实现，保证「跨字段」规则只有一处定义。
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    action: ReviewActionLiteral = Field(..., description="审核动作（契约 §2.7）")
    comment: str | None = Field(
        None, max_length=COMMENT_MAX, description=f"备注；reject / request_more_info 时必填且 ≥{COMMENT_MIN} 字"
    )

    @model_validator(mode="after")
    def _check_comment_required(self) -> "ClaimReviewRequest":
        """驳回与要求补充信息必须给出理由（否则用户无法知道如何整改）。"""
        if self.action in (ReviewAction.REJECT, ReviewAction.REQUEST_MORE_INFO):
            comment = (self.comment or "").strip()
            if not comment:
                raise ValueError("驳回或要求补充信息时，comment 为必填项")
            if len(comment) < COMMENT_MIN or len(comment) > COMMENT_MAX:
                raise ValueError(
                    f"comment 长度需在 {COMMENT_MIN}~{COMMENT_MAX} 字之间（当前 {len(comment)} 字）"
                )
            self.comment = comment
        elif self.comment is not None:
            comment = self.comment.strip()
            if len(comment) > COMMENT_MAX:
                raise ValueError(f"comment 长度不能超过 {COMMENT_MAX} 字")
            self.comment = comment or None
        return self


class AuditLogQuery(BaseModel):
    """`GET /api/v1/admin/audit-logs` 查询参数（契约 §3.13）。"""

    model_config = ConfigDict(extra="ignore")  # 查询参数模型：忽略无关参数

    target_type: Literal["item", "claim", "user", "config"] | None = Field(
        None, description="对象类型"
    )
    target_id: int | None = Field(None, ge=1, description="对象 ID")
    operator_id: int | None = Field(None, ge=1, description="操作人 ID")
    action: str | None = Field(None, description="动作，支持逗号分隔多值（契约 §2.8）")
    date_from: datetime | None = Field(None, description="起始时间（ISO 8601）")
    date_to: datetime | None = Field(None, description="结束时间（ISO 8601）")

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: str | None) -> str | None:
        if not value:
            return None
        return value.strip() or None


class ClaimReviewData(BaseModel):
    """`POST /api/v1/admin/claims/{claim_id}/review` 的响应 `data`（契约 §3.11，逐字对齐）。"""

    claim_id: int
    action: str
    claim_status: str
    item_id: int
    item_status: str
    reviewed_at: str | None = None
    audit_log_id: int | None = None


class ClaimDetailClaim(BaseModel):
    """审核详情视图中的 `claim` 块（契约 §3.12）。"""

    model_config = ConfigDict(extra="allow")

    id: int
    status: str
    verification_score: float | None = None
    credibility_score: float | None = None
    risk_level: str | None = None
    created_at: str | None = None


class ClaimDetailData(BaseModel):
    """`GET /api/v1/admin/claims/{claim_id}` 的响应 `data`（契约 §3.12）。

    红线：`data` 中**不得**包含标准答案原文，只含「是否命中 + 得分 + 理由」。
    """

    model_config = ConfigDict(extra="allow")

    claim: dict[str, Any]
    match: dict[str, Any]
    lost_item: dict[str, Any]
    found_item: dict[str, Any]
    verification: dict[str, Any]
    history: list[dict[str, Any]] = Field(default_factory=list)


# ----------------------------------------------------------------------
# 待审核认领列表（契约 §3.10.2）
# ----------------------------------------------------------------------
DEFAULT_PENDING_STATUSES: tuple[str, ...] = ("pending_review", "request_more_info")
VALID_CLAIM_STATUSES: tuple[str, ...] = tuple(item.value for item in ClaimStatus)
VALID_RISK_LEVELS: tuple[str, ...] = tuple(item.value for item in RiskLevel)
VALID_SORT_FIELDS: tuple[str, ...] = ("risk_level", "created_at", "verification_score")


class AdminClaimQuery(BaseModel):
    """`GET /api/v1/admin/claims` 查询参数（契约 §3.10.2，字段与默认值逐条对齐）。

    - `status`：逗号分隔多值，默认 `pending_review,request_more_info`；传 `all` 表示不过滤；
    - `risk_level`：逗号分隔多值；
    - `sort_by`：`risk_level`（默认，高风险优先）/ `created_at` / `verification_score`。
    """

    model_config = ConfigDict(extra="ignore")  # 查询参数模型：忽略无关参数

    status: str | None = Field(None, description="逗号分隔多值；all 表示不过滤")
    risk_level: str | None = Field(None, description="low / medium / high，逗号分隔多值")
    category: str | None = Field(None, description="物品类别精确匹配")
    keyword: str | None = Field(None, description="在物品标题中模糊匹配")
    sort_by: str = Field("risk_level", description="risk_level / created_at / verification_score")
    order: str = Field("desc", description="asc / desc")

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.strip().lower() == "all":
            return "all"
        invalid = [item for item in split_csv(value) if item not in VALID_CLAIM_STATUSES]
        if invalid:
            raise ValueError(
                f"status 取值不合法：{', '.join(invalid)}；可选 {' / '.join(VALID_CLAIM_STATUSES)} 或 all"
            )
        return value

    @field_validator("risk_level")
    @classmethod
    def _check_risk_level(cls, value: str | None) -> str | None:
        if not value:
            return None
        invalid = [item for item in split_csv(value) if item not in VALID_RISK_LEVELS]
        if invalid:
            raise ValueError(
                f"risk_level 取值不合法：{', '.join(invalid)}；可选 {' / '.join(VALID_RISK_LEVELS)}"
            )
        return value

    @field_validator("sort_by")
    @classmethod
    def _check_sort_by(cls, value: str) -> str:
        if value not in VALID_SORT_FIELDS:
            raise ValueError(f"sort_by 只能取：{' / '.join(VALID_SORT_FIELDS)}")
        return value

    @field_validator("order")
    @classmethod
    def _check_order(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in ("asc", "desc"):
            raise ValueError("order 只能是 asc 或 desc")
        return normalized

    # ---- 派生 ----
    @property
    def statuses(self) -> list[str]:
        """状态过滤列表：`[]` 表示不过滤（`status=all`）。"""
        if self.status is None:
            return list(DEFAULT_PENDING_STATUSES)
        if self.status == "all":
            return []
        return split_csv(self.status)

    @property
    def risk_levels(self) -> list[str]:
        """风险等级过滤列表：`[]` 表示不过滤。"""
        return split_csv(self.risk_level)

    @property
    def pending_only(self) -> bool:
        """是否处于「默认待办」视图（用于 `counts` 的口径说明）。"""
        return self.statuses == list(DEFAULT_PENDING_STATUSES)


class AdminClaimListItem(BaseModel):
    """待审核列表元素（契约 §3.10.2）。**不含隐藏特征与答案线索。**"""

    model_config = ConfigDict(extra="allow")

    claim_id: int
    status: str
    status_text: str | None = None
    risk_level: str | None = None
    risk_level_text: str | None = None
    verification_score: float | None = None
    credibility_score: float | None = None
    attempts_used: int = 0
    applicant: dict[str, Any] | None = None
    item: dict[str, Any] | None = None
    total_score: float | None = None
    match_level: str | None = None
    created_at: str | None = None


class AdminClaimCounts(BaseModel):
    """审核台徽标计数（契约 §3.10.2 的 `counts`）。"""

    pending_review: int = 0
    high_risk: int = 0
    request_more_info: int = 0


class ClaimDetailApplicant(BaseModel):
    """`GET /api/v1/claims/{claim_id}` 的响应 `data`（契约 §3.10.1）。

    红线：未提交验证前 `verification_score` / `credibility_score` / `risk_level`
    **不出现**（而非返回 null），因此这里全部为可选且默认不输出。
    """

    model_config = ConfigDict(extra="allow")

    claim_id: int
    match_id: int
    status: str
    status_text: str | None = None
    questions: list[dict[str, Any]] = Field(default_factory=list)
    attempts_used: int = 0
    max_attempts: int = 0
    attempts_left: int = 0
    created_at: str | None = None
    match_summary: dict[str, Any] = Field(default_factory=dict)
    can_submit: bool = False
    disclaimer: str = ""


__all__ = [
    "COMMENT_MIN",
    "COMMENT_MAX",
    "ReviewActionLiteral",
    "ClaimReviewRequest",
    "AuditLogQuery",
    "ClaimReviewData",
    "ClaimDetailClaim",
    "ClaimDetailData",
    "DEFAULT_PENDING_STATUSES",
    "VALID_CLAIM_STATUSES",
    "VALID_RISK_LEVELS",
    "VALID_SORT_FIELDS",
    "AdminClaimQuery",
    "AdminClaimListItem",
    "AdminClaimCounts",
    "ClaimDetailApplicant",
    "AuditTargetType",
]
