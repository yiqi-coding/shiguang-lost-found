"""API v1 路由汇总（契约附录 A 的 21 个接口中，后端实现 18 个）。

- `api_router`：前缀 `/api/v1`，包含认证、物品、匹配、认领、我的记录、管理员；
- `health_router`：挂在根路径（`/health`、`/health/live`、`/health/ready`）。

红线：路由层只做参数校验与响应组装，敏感字段一律经 `app/services/serializers.py` 输出。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import admin, auth, claims, health, items, matches, me

# 业务路由（/api/v1/...）
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(items.router)
api_router.include_router(matches.router)
api_router.include_router(claims.router)
api_router.include_router(me.router)
api_router.include_router(admin.router)

# 健康检查（根路径，无需鉴权）
health_router = APIRouter()
health_router.include_router(health.router)

__all__ = ["api_router", "health_router"]
