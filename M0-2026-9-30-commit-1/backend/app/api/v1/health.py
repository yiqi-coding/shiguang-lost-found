"""健康检查路由（契约 §3.1）。

| 接口 | 用途 | 语义 |
|---|---|---|
| `GET /health` | 综合体检 | 任一依赖 `down` 时整体 HTTP **503**（进程仍返回 `app.status="ok"`） |
| `GET /health/live` | 存活探针 | 仅证明进程存活，永远 200 |
| `GET /health/ready` | 就绪探针 | 数据库（必需）与缓存（可选）就绪才 200 |

红线：**不得返回任何密钥、Token、密码、完整连接串**；
主机名 / 桶名 / Endpoint / 服务地址一律打码（各依赖的 `check_*` 已内置脱敏）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import current_request_id
from app.clients.algorithm_client import get_algorithm_client
from app.clients.maas_client import get_maas_client
from app.core.config import settings
from app.core.response import ok
from app.db.session import check_database
from app.services.cache import cache
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])

# 进程启动时间（用于 uptime_seconds）
_STARTED_AT = datetime.now(timezone.utc)


def uptime_seconds() -> int:
    """进程运行时长（秒）。"""
    return int((datetime.now(timezone.utc) - _STARTED_AT).total_seconds())


def _safe(check, *, default: dict[str, Any]) -> dict[str, Any]:
    """执行依赖检查：异常一律转成 `down`，绝不让健康检查本身 500。"""
    try:
        result = check()
        return result if isinstance(result, dict) else default
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查项失败：%s", type(exc).__name__)
        return {"status": "down", "error": type(exc).__name__}


def collect_health() -> dict[str, Any]:
    """收集全部依赖状态（非敏感）。"""
    database = _safe(check_database, default={"status": "down"})
    redis_state = _safe(cache.check_health, default={"status": "down"})
    storage_state = _safe(get_storage().check_health, default={"status": "down"})
    algorithm_state = _safe(get_algorithm_client().check_health, default={"status": "down"})
    maas_state = _safe(get_maas_client().check_health, default={"status": "not_configured"})

    obs_block: dict[str, Any]
    if settings.STORAGE_BACKEND == "local":
        obs_block = {"status": storage_state.get("status", "ok"), "backend": "local", "bucket": None}
        obs_block.update({k: v for k, v in storage_state.items() if k not in obs_block})
    else:
        obs_block = storage_state

    return {
        "app": {
            "status": "ok",  # 进程存活即 ok（依赖状态由下方各块体现）
            "env": settings.APP_ENV,
            "version": settings.APP_VERSION,
            "uptime_seconds": uptime_seconds(),
        },
        "database": database,
        "redis": redis_state,
        "obs": obs_block,
        "algorithm_service": algorithm_state,
        "maas": maas_state,
    }


def _any_down(payload: dict[str, Any]) -> bool:
    for key in ("database", "redis", "obs", "algorithm_service"):
        block = payload.get(key) or {}
        if block.get("status") == "down":
            return True
    maas = payload.get("maas") or {}
    return maas.get("status") == "down"


@router.get("/health", summary="综合健康检查", description="无需鉴权；任一依赖 down 时返回 HTTP 503")
def health(request: Request) -> JSONResponse:
    """综合健康检查（契约 §3.1）。

    任一依赖 `down` 时 HTTP 返回 **503**，但响应体 `data.app.status` 仍为 `ok`
    （进程存活），便于探针区分「进程挂了」与「依赖挂了」。
    """
    payload = collect_health()
    status_code = 503 if _any_down(payload) else 200
    body = ok(payload, request_id=current_request_id(request))
    return JSONResponse(status_code=status_code, content=body)


@router.get("/health/live", summary="存活探针", description="仅测进程，永远 200")
def liveness(request: Request) -> dict:
    """存活探针：只要进程能响应即视为存活。"""
    return ok({"status": "ok", "uptime_seconds": uptime_seconds()}, request_id=current_request_id(request))


@router.get("/health/ready", summary="就绪探针", description="数据库必需，缓存可选")
def readiness(request: Request) -> JSONResponse:
    """就绪探针：数据库连通才 200；Redis 不可用视为 `degraded`（业务可降级运行）。"""
    started = time.perf_counter()
    database = _safe(check_database, default={"status": "down"})
    redis_state = _safe(cache.check_health, default={"status": "down"})

    database_ok = database.get("status") == "ok"
    redis_ok = redis_state.get("status") == "ok"
    if not database_ok:
        overall = "down"
    elif not redis_ok:
        overall = "degraded"
    else:
        overall = "ok"

    body = ok(
        {
            "status": overall,
            "checks": {"database": database, "redis": redis_state},
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        },
        request_id=current_request_id(request),
    )
    return JSONResponse(status_code=200 if database_ok else 503, content=body)


__all__ = ["router", "collect_health", "uptime_seconds"]
