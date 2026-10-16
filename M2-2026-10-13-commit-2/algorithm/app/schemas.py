"""请求 / 响应 Schema。

优先使用 **Pydantic v2**；环境没有 pydantic（或版本不兼容）时自动退回到
**纯 dataclass 实现**，接口字段与校验语义保持一致，因此 ``main.py`` 之外的
所有模块都不会因为缺少第三方依赖而无法导入。

对外契约（不可更改）
--------------------
1. ``POST /algorithm/v1/match``
2. ``POST /algorithm/v1/extract-attributes``
3. ``POST /algorithm/v1/claim-credibility``
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .text_utils import clamp, safe_float

# ---------------------------------------------------------------------------
# 尝试导入 pydantic v2（失败则使用下面的 dataclass 版本）
# ---------------------------------------------------------------------------
PYDANTIC_AVAILABLE = False
try:  # pragma: no cover - 取决于运行环境
    from pydantic import BaseModel, ConfigDict, Field, field_validator  # type: ignore
    from pydantic import ValidationError  # type: ignore

    PYDANTIC_AVAILABLE = True
except Exception:  # noqa: BLE001 - 任何导入/版本问题都降级
    BaseModel = object  # type: ignore
    ValidationError = ValueError  # type: ignore


if PYDANTIC_AVAILABLE:

    class ItemPayload(BaseModel):
        """物品对象（请求体里的 query_item / candidate_items[i]）。"""

        model_config = ConfigDict(extra="allow", populate_by_name=True)

        id: Optional[Any] = None
        category: Optional[str] = None
        title: Optional[str] = None
        description: Optional[str] = None
        attributes: Dict[str, Any] = Field(default_factory=dict)
        location: Optional[str] = None
        event_time: Optional[Any] = None
        image_url: Optional[str] = None
        public_features: List[str] = Field(default_factory=list)
        hidden_features: List[str] = Field(default_factory=list)
        ai_attributes: Dict[str, Any] = Field(default_factory=dict)

    class MatchRequest(BaseModel):
        model_config = ConfigDict(extra="allow")

        query_item: Optional[Dict[str, Any]] = None
        candidate_items: List[Dict[str, Any]] = Field(default_factory=list)
        top_k: Optional[int] = None
        image_backend: Optional[str] = None

    class MatchScores(BaseModel):
        model_config = ConfigDict(extra="allow")

        image_score: float = 0.0
        text_score: float = 0.0
        category_score: float = 0.0
        time_score: float = 0.0
        location_score: float = 0.0
        brand_model_score: float = 0.0

    class MatchResultItem(BaseModel):
        model_config = ConfigDict(extra="allow")

        candidate_id: Optional[Any] = None
        total_score: float = 0.0
        scores: MatchScores = Field(default_factory=MatchScores)
        evidence: List[str] = Field(default_factory=list)
        uncertainties: List[str] = Field(default_factory=list)

    class MatchResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        model_version: str = "baseline-1.0"
        results: List[MatchResultItem] = Field(default_factory=list)
        warnings: List[str] = Field(default_factory=list)

    class ExtractAttributesRequest(BaseModel):
        model_config = ConfigDict(extra="allow")

        image_url: Optional[str] = None
        description: Optional[str] = None
        title: Optional[str] = None
        category: Optional[str] = None
        attributes: Dict[str, Any] = Field(default_factory=dict)
        item_id: Optional[Any] = None

    class ExtractAttributesResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        category: Optional[str] = None
        brand: Optional[str] = None
        model: Optional[str] = None
        color: Optional[str] = None
        material: Optional[str] = None
        visible_features: List[str] = Field(default_factory=list)
        accessories: List[str] = Field(default_factory=list)
        keywords: List[str] = Field(default_factory=list)
        uncertainties: List[str] = Field(default_factory=list)
        model_version: str = "baseline-1.0"

    class VerificationQuestion(BaseModel):
        model_config = ConfigDict(extra="allow")

        id: Optional[Any] = None
        question_id: Optional[Any] = None
        question: Optional[str] = None
        reference_answer: Optional[str] = None
        standard_answer: Optional[str] = None
        keywords: List[str] = Field(default_factory=list)
        match_mode: Optional[str] = None
        weight: float = 1.0

    class ClaimCredibilityRequest(BaseModel):
        model_config = ConfigDict(extra="allow")

        match_result: Optional[Dict[str, Any]] = None
        candidate_match: Optional[Dict[str, Any]] = None
        questions: List[Dict[str, Any]] = Field(default_factory=list)
        answers: List[Any] = Field(default_factory=list)
        user_answers: List[Any] = Field(default_factory=list)
        target_item: Optional[Dict[str, Any]] = None
        behavior_signals: Optional[Dict[str, Any]] = None
        claim_id: Optional[Any] = None
        user_id: Optional[Any] = None

    class PerQuestionResult(BaseModel):
        model_config = ConfigDict(extra="allow")

        question_id: str
        matched: bool = False
        score: float = 0.0
        reason: str = ""

    class ClaimCredibilityResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        credibility_score: float = 0.0
        risk_level: str = "high"
        per_question: List[PerQuestionResult] = Field(default_factory=list)
        risk_tips: List[str] = Field(default_factory=list)
        breakdown: Dict[str, float] = Field(default_factory=dict)
        model_version: str = "baseline-1.0"

else:  # ------------------------------------------------------------------
    # 纯 dataclass 兜底实现（无第三方依赖）
    # ------------------------------------------------------------------
    @dataclass
    class ItemPayload:  # type: ignore[no-redef]
        id: Optional[Any] = None
        category: Optional[str] = None
        title: Optional[str] = None
        description: Optional[str] = None
        attributes: Dict[str, Any] = field(default_factory=dict)
        location: Optional[str] = None
        event_time: Optional[Any] = None
        image_url: Optional[str] = None
        public_features: List[str] = field(default_factory=list)
        hidden_features: List[str] = field(default_factory=list)
        ai_attributes: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class MatchRequest:  # type: ignore[no-redef]
        query_item: Optional[Dict[str, Any]] = None
        candidate_items: List[Dict[str, Any]] = field(default_factory=list)
        top_k: Optional[int] = None
        image_backend: Optional[str] = None

    @dataclass
    class MatchScores:  # type: ignore[no-redef]
        image_score: float = 0.0
        text_score: float = 0.0
        category_score: float = 0.0
        time_score: float = 0.0
        location_score: float = 0.0
        brand_model_score: float = 0.0

    @dataclass
    class MatchResultItem:  # type: ignore[no-redef]
        candidate_id: Optional[Any] = None
        total_score: float = 0.0
        scores: MatchScores = field(default_factory=MatchScores)
        evidence: List[str] = field(default_factory=list)
        uncertainties: List[str] = field(default_factory=list)

    @dataclass
    class MatchResponse:  # type: ignore[no-redef]
        model_version: str = "baseline-1.0"
        results: List[MatchResultItem] = field(default_factory=list)
        warnings: List[str] = field(default_factory=list)

    @dataclass
    class ExtractAttributesRequest:  # type: ignore[no-redef]
        image_url: Optional[str] = None
        description: Optional[str] = None
        title: Optional[str] = None
        category: Optional[str] = None
        attributes: Dict[str, Any] = field(default_factory=dict)
        item_id: Optional[Any] = None

    @dataclass
    class ExtractAttributesResponse:  # type: ignore[no-redef]
        category: Optional[str] = None
        brand: Optional[str] = None
        model: Optional[str] = None
        color: Optional[str] = None
        material: Optional[str] = None
        visible_features: List[str] = field(default_factory=list)
        accessories: List[str] = field(default_factory=list)
        keywords: List[str] = field(default_factory=list)
        uncertainties: List[str] = field(default_factory=list)
        model_version: str = "baseline-1.0"

    @dataclass
    class VerificationQuestion:  # type: ignore[no-redef]
        id: Optional[Any] = None
        question_id: Optional[Any] = None
        question: Optional[str] = None
        reference_answer: Optional[str] = None
        standard_answer: Optional[str] = None
        keywords: List[str] = field(default_factory=list)
        match_mode: Optional[str] = None
        weight: float = 1.0

    @dataclass
    class ClaimCredibilityRequest:  # type: ignore[no-redef]
        match_result: Optional[Dict[str, Any]] = None
        candidate_match: Optional[Dict[str, Any]] = None
        questions: List[Dict[str, Any]] = field(default_factory=list)
        answers: List[Any] = field(default_factory=list)
        user_answers: List[Any] = field(default_factory=list)
        target_item: Optional[Dict[str, Any]] = None
        behavior_signals: Optional[Dict[str, Any]] = None
        claim_id: Optional[Any] = None
        user_id: Optional[Any] = None

    @dataclass
    class PerQuestionResult:  # type: ignore[no-redef]
        question_id: str
        matched: bool = False
        score: float = 0.0
        reason: str = ""

    @dataclass
    class ClaimCredibilityResponse:  # type: ignore[no-redef]
        credibility_score: float = 0.0
        risk_level: str = "high"
        per_question: List[PerQuestionResult] = field(default_factory=list)
        risk_tips: List[str] = field(default_factory=list)
        breakdown: Dict[str, float] = field(default_factory=dict)
        model_version: str = "baseline-1.0"


# ---------------------------------------------------------------------------
# 内部转换：dict ↔ 响应对象（两种实现都能用）
# ---------------------------------------------------------------------------
def _is_pydantic(model_cls: Any) -> bool:
    return PYDANTIC_AVAILABLE and isinstance(model_cls, type) and hasattr(model_cls, "model_validate")


def build_match_response(payload: Dict[str, Any]) -> Any:
    """把 :meth:`MatchEngine.match` 的结果转成响应对象。"""
    if _is_pydantic(MatchResponse):
        return MatchResponse.model_validate(payload)
    results = []
    for item in payload.get("results", []):
        scores = item.get("scores") or {}
        results.append(MatchResultItem(
            candidate_id=item.get("candidate_id"),
            total_score=clamp(safe_float(item.get("total_score"), 0.0)),
            scores=MatchScores(
                image_score=clamp(safe_float(scores.get("image_score"), 0.0)),
                text_score=clamp(safe_float(scores.get("text_score"), 0.0)),
                category_score=clamp(safe_float(scores.get("category_score"), 0.0)),
                time_score=clamp(safe_float(scores.get("time_score"), 0.0)),
                location_score=clamp(safe_float(scores.get("location_score"), 0.0)),
                brand_model_score=clamp(safe_float(scores.get("brand_model_score"), 0.0)),
            ),
            evidence=list(item.get("evidence") or []),
            uncertainties=list(item.get("uncertainties") or []),
        ))
    return MatchResponse(
        model_version=str(payload.get("model_version") or "baseline-1.0"),
        results=results,
        warnings=list(payload.get("warnings") or []),
    )


def build_extract_response(payload: Dict[str, Any]) -> Any:
    if _is_pydantic(ExtractAttributesResponse):
        return ExtractAttributesResponse.model_validate(payload)
    return ExtractAttributesResponse(**{k: v for k, v in payload.items()
                                        if k in ExtractAttributesResponse.__dataclass_fields__})


def build_credibility_response(payload: Dict[str, Any]) -> Any:
    if _is_pydantic(ClaimCredibilityResponse):
        return ClaimCredibilityResponse.model_validate(payload)
    return ClaimCredibilityResponse(
        credibility_score=clamp(safe_float(payload.get("credibility_score"), 0.0)),
        risk_level=str(payload.get("risk_level") or "high"),
        per_question=[PerQuestionResult(**item) for item in payload.get("per_question") or []],
        risk_tips=list(payload.get("risk_tips") or []),
        breakdown={k: safe_float(v, 0.0) for k, v in (payload.get("breakdown") or {}).items()},
    )


def to_plain_dict(model: Any) -> Dict[str, Any]:
    """响应对象 → 普通 dict（Pydantic v2 / dataclass 都支持）。"""
    if model is None:
        return {}
    if hasattr(model, "model_dump"):
        return model.model_dump()
    if hasattr(model, "__dataclass_fields__"):
        return asdict(model)
    if isinstance(model, dict):
        return dict(model)
    return {"value": model}


def validation_errors(exc: Exception) -> List[str]:
    """把校验异常转成可读的错误列表。"""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            return [f"{'.'.join(str(x) for x in item.get('loc', ()))}: {item.get('msg', '')}"
                    for item in errors()]
        except Exception:  # noqa: BLE001
            return [str(exc)]
    return [str(exc)]


# match 接口里维度分 → 响应字段名的映射（保持契约字段名不变）
SCORE_FIELD_MAP = {
    "image_score": "image",
    "text_score": "text",
    "category_score": "category",
    "time_score": "time",
    "location_score": "location",
    "brand_model_score": "brand_model",
}

__all__ = [
    "PYDANTIC_AVAILABLE",
    "ItemPayload",
    "MatchRequest",
    "MatchScores",
    "MatchResultItem",
    "MatchResponse",
    "ExtractAttributesRequest",
    "ExtractAttributesResponse",
    "VerificationQuestion",
    "ClaimCredibilityRequest",
    "PerQuestionResult",
    "ClaimCredibilityResponse",
    "SCORE_FIELD_MAP",
    "build_match_response",
    "build_extract_response",
    "build_credibility_response",
    "to_plain_dict",
    "validation_errors",
]
