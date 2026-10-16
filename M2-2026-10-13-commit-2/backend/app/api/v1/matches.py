"""匹配路由（契约 §3.6 / §3.7）。

| 接口 | 鉴权 | 说明 |
|---|---|---|
| `GET  /api/v1/items/{item_id}/matches` | 公开（脱敏） | 读取匹配结果，支持 `top_k` / `min_score` / `refresh` |
| `POST /api/v1/items/{item_id}/match` | 登录（本人或管理员） | 重新计算匹配并落库 |

副作用（契约 §3.7 强制）：写 `matches` 表 UPSERT + 写 Redis `match:item:{item_id}:{version}`
（TTL 10min）+ 写 `audit_logs(action=match.compute)`。

降级：算法服务不可用且 `ALGORITHM_FALLBACK_ENABLED=true` 时返回 `200 + degraded=true`
（本地规则打分）；否则返回 `502 ALGORITHM_SERVICE_ERROR`。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import (
    CurrentUser,
    DbSession,
    OptionalUser,
    current_request_id,
    rate_limit,
)
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.response import ok
from app.schemas.match import MatchComputeData, MatchComputeRequest, MatchListData, MatchQuery
from app.services import item_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["匹配"])

MODEL_VERSION_HEADER = "X-Model-Version"


@router.get(
    "/items/{item_id}/matches",
    summary="获取匹配结果",
    description="公开接口（按角色脱敏）；refresh=true 时忽略缓存重算，需要登录",
)
def get_matches(
    request: Request,
    response: Response,
    db: DbSession,
    viewer: OptionalUser,
    item_id: int,
    top_k: Annotated[int, Query(ge=1, le=20, description="默认 5，上限 20")] = 5,
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0, description="低于该分数的候选不返回")] = None,
    refresh: Annotated[bool, Query(description="true 时忽略缓存重算（需鉴权）")] = False,
) -> dict:
    """获取某物品的匹配结果（契约 §3.6）。

    - `degraded=true` 表示算法服务不可用、已降级为规则匹配，前端必须显式标注「降级结果」；
    - `low` 等级不得展示为「高匹配」（等级由 `scoring.match_level_for()` 统一判定）。
    """
    if refresh and viewer is None:
        raise UnauthorizedError("刷新匹配结果需要登录")

    item = item_service.get_item_or_404(db, item_id)
    query = MatchQuery(top_k=top_k, min_score=min_score, refresh=refresh)
    outcome = item_service.get_matches(
        db,
        item=item,
        top_k=query.top_k,
        min_score=query.effective_min_score,
        refresh=query.refresh,
    )

    results = [
        item_service.serialize_match_result(match, candidate=candidate, viewer=viewer)
        for match, candidate in outcome.results
    ]
    data = MatchListData(
        query_item_id=outcome.query_item_id,
        model_version=outcome.model_version,
        computed_at=outcome.computed_at.isoformat() if outcome.computed_at else None,
        cached=outcome.cached,
        degraded=outcome.degraded,
        results=results,
    )
    # 契约 §6：匹配相关接口返回算法版本，便于复现
    response.headers[MODEL_VERSION_HEADER] = outcome.model_version
    return ok(data.model_dump(), request_id=current_request_id(request))


@router.post(
    "/items/{item_id}/match",
    summary="重新计算匹配",
    description="仅物品发布者本人或管理员；同步完成并写库、写缓存、写审计",
)
def compute_matches(
    request: Request,
    response: Response,
    db: DbSession,
    current_user: CurrentUser,
    item_id: int,
    payload: MatchComputeRequest | None = None,
    _limit: Annotated[None, Depends(rate_limit("match_compute", 60, 3600))] = None,
) -> dict:
    """重新计算匹配（登录 + 本人/管理员；契约 §3.7）。

    Baseline 采用**同步计算**：返回 200 + `status="completed"`，
    同时保留契约中的 `task_id` / `estimated_seconds` 字段以兼容异步契约。
    """
    item = item_service.get_item_or_404(db, item_id)
    if not current_user.is_admin and item.owner_user_id != current_user.id:
        raise ForbiddenError("只能对自己发布的物品重算匹配")

    body = payload or MatchComputeRequest()
    outcome = item_service.recompute_matches(
        db, item=item, actor=current_user, force=body.force, top_k=body.top_k
    )

    data = MatchComputeData(
        task_id=outcome.task_id,
        status="completed",
        estimated_seconds=0,
        query_item_id=outcome.query_item_id,
        computed=outcome.computed,
        degraded=outcome.degraded,
        model_version=outcome.model_version,
    )
    message = "匹配任务已完成（算法服务不可用，已降级为规则匹配）" if outcome.degraded else "匹配任务已完成"
    response.headers[MODEL_VERSION_HEADER] = outcome.model_version
    return ok(data.model_dump(), message=message, request_id=current_request_id(request))


__all__ = ["router"]
