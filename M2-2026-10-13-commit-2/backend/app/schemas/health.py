"""健康检查 Schema（契约 §3.1）。

红线：**不得返回任何密钥、Token、密码、完整连接串**；主机名/桶名/Endpoint 一律打码。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DependencyState = Literal["ok", "degraded", "down", "not_configured"]


class AppHealth(BaseModel):
    """应用自身状态。"""

    model_config = ConfigDict(extra="allow")

    status: DependencyState = "ok"
    env: str = Field(..., description="APP_ENV")
    version: str = Field(..., description="APP_VERSION")
    uptime_seconds: int = Field(0, ge=0, description="进程运行时长（秒）")


class DependencyHealth(BaseModel):
    """单个依赖的状态块（允许携带打码后的附加字段）。"""

    model_config = ConfigDict(extra="allow")

    status: DependencyState
    latency_ms: float | None = None
    degraded: bool | None = None


class HealthData(BaseModel):
    """`GET /health` 的 `data` 结构（契约 §3.1）。"""

    model_config = ConfigDict(extra="allow")

    app: AppHealth
    database: DependencyHealth
    redis: DependencyHealth
    obs: DependencyHealth
    algorithm_service: DependencyHealth
    maas: DependencyHealth


class LivenessData(BaseModel):
    """`GET /health/live`：仅证明进程存活。"""

    status: Literal["ok"] = "ok"
    uptime_seconds: int = 0


class ReadinessData(BaseModel):
    """`GET /health/ready`：核心依赖（数据库 / 缓存）就绪才返回 200。"""

    status: Literal["ok", "degraded", "down"]
    checks: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "DependencyState",
    "AppHealth",
    "DependencyHealth",
    "HealthData",
    "LivenessData",
    "ReadinessData",
]
