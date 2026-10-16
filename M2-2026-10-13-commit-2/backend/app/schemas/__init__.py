"""Pydantic Schema 包（对外契约的校验层）。

分层约定：

- `schemas` 只负责**请求/响应的结构校验**与基础清洗（含联系方式脱敏）；
- 字段级权限控制**只能**在 `app/services/serializers.py` 实现，本包不得复制权限逻辑；
- 错误信息一律中文，便于前端直接展示。
"""

from __future__ import annotations

from app.schemas.admin import (
    AdminClaimCounts,
    AdminClaimListItem,
    AdminClaimQuery,
    AuditLogQuery,
    ClaimDetailApplicant,
    ClaimDetailData,
    ClaimReviewData,
    ClaimReviewRequest,
)
from app.schemas.auth import DemoLoginRequest, LoginData, LoginRequest, UserBrief
from app.schemas.claim import (
    AnswerItem,
    ClaimCreateData,
    ClaimCreateRequest,
    ClaimVerifyData,
    ClaimVerifyRequest,
)
from app.schemas.common import FieldError, PageParams, iso_utc, parse_json_text, split_csv, to_utc_naive
from app.schemas.health import AppHealth, DependencyHealth, HealthData, LivenessData, ReadinessData
from app.schemas.item import (
    ExtractAttributesData,
    ItemCreate,
    ItemPublishData,
    ItemPublishImage,
)
from app.schemas.match import (
    MatchComputeData,
    MatchComputeRequest,
    MatchListData,
    MatchQuery,
    MatchScoreDetail,
)

__all__ = [
    # common
    "PageParams",
    "FieldError",
    "split_csv",
    "parse_json_text",
    "to_utc_naive",
    "iso_utc",
    # auth
    "LoginRequest",
    "DemoLoginRequest",
    "UserBrief",
    "LoginData",
    # item
    "ItemCreate",
    "ItemPublishImage",
    "ItemPublishData",
    "ExtractAttributesData",
    # match
    "MatchComputeRequest",
    "MatchQuery",
    "MatchComputeData",
    "MatchScoreDetail",
    "MatchListData",
    # claim
    "ClaimCreateRequest",
    "AnswerItem",
    "ClaimVerifyRequest",
    "ClaimCreateData",
    "ClaimVerifyData",
    # admin
    "ClaimReviewRequest",
    "ClaimReviewData",
    "ClaimDetailData",
    "ClaimDetailApplicant",
    "AuditLogQuery",
    "AdminClaimQuery",
    "AdminClaimListItem",
    "AdminClaimCounts",
    # health
    "AppHealth",
    "DependencyHealth",
    "HealthData",
    "LivenessData",
    "ReadinessData",
]
