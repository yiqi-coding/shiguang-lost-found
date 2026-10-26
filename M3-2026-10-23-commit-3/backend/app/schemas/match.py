"""匹配相关 Schema（契约 §3.6 / §3.7）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

# 契约 §3.6：top_k 默认 5、上限 20
TOP_K_DEFAULT = settings.MATCH_TOP_K
TOP_K_MAX = 20


class MatchComputeRequest(BaseModel):
    """`POST /api/v1/items/{item_id}/match` 请求体（契约 §3.7）。

    示例：`{ "force": true, "top_k": 5 }`
    """

    model_config = ConfigDict(extra="ignore")

    force: bool = Field(True, description="True 时忽略缓存强制重算")
    top_k: int = Field(TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=f"返回条数，上限 {TOP_K_MAX}")


class MatchQuery(BaseModel):
    """`GET /api/v1/items/{item_id}/matches` 查询参数（契约 §3.6）。"""

    model_config = ConfigDict(extra="ignore")  # 查询参数模型：忽略无关参数，避免误判 400

    top_k: int = Field(TOP_K_DEFAULT, ge=1, le=TOP_K_MAX, description=f"返回条数，上限 {TOP_K_MAX}")
    min_score: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="低于该分数的候选不返回；默认取配置 matching.min_display_score",
    )
    refresh: bool = Field(False, description="True 时忽略缓存重算（需鉴权）")

    @property
    def effective_min_score(self) -> float:
        """未显式传入时回落到配置的 `MATCH_MIN_DISPLAY_SCORE`。"""
        return settings.MATCH_MIN_DISPLAY_SCORE if self.min_score is None else self.min_score


class MatchComputeData(BaseModel):
    """`POST /api/v1/items/{item_id}/match` 响应 `data`。

    契约 §3.7 允许 202（异步）或 200（同步完成）。Baseline 为**同步计算**，
    因此返回 `status="completed"`，同时保留契约中的 `task_id` / `estimated_seconds` 字段，
    并补充 `computed` / `degraded` / `model_version` 便于前端展示与排障。
    """

    task_id: str
    status: str = "completed"
    estimated_seconds: int = 0
    query_item_id: int
    computed: int = Field(0, description="本次落库/更新的候选数量")
    degraded: bool = Field(False, description="True 表示算法服务不可用，已降级为本地规则匹配")
    model_version: str = "baseline-1.0"


class MatchScoreDetail(BaseModel):
    """六维分数（契约 §3.6 的 `score_detail`）。"""

    category_score: float
    attribute_score: float
    image_score: float
    time_score: float
    location_score: float
    brand_model_score: float


class MatchResultItem(BaseModel):
    """`results` 元素（契约 §3.6）。`candidate_item` 由序列化层输出，此处放宽为 dict。"""

    model_config = ConfigDict(extra="allow")

    match_id: int
    candidate_item: dict
    total_score: float
    score_detail: MatchScoreDetail
    evidence: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    risk_tips: list[str] = Field(default_factory=list)
    match_level: str


class MatchListData(BaseModel):
    """`GET /api/v1/items/{item_id}/matches` 响应 `data`（契约 §3.6）。"""

    query_item_id: int
    model_version: str = "baseline-1.0"
    computed_at: str | None = None
    cached: bool = False
    degraded: bool = False
    results: list[dict] = Field(default_factory=list)
    disclaimer: str = "候选匹配不等于确认归属，最终结果由管理员审核确认。"


__all__ = [
    "TOP_K_DEFAULT",
    "TOP_K_MAX",
    "MatchComputeRequest",
    "MatchQuery",
    "MatchComputeData",
    "MatchScoreDetail",
    "MatchResultItem",
    "MatchListData",
]
