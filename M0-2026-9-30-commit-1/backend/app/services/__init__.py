"""业务服务包。

分层约定（自下而上）：

1. `app.core` 基础设施（配置 / 错误 / 响应 / 安全 / 日志）；
2. `app.models` + `app.db` 持久化；
3. `app.clients` 外部依赖（算法服务 / MaaS）；
4. `app.services` 业务编排（状态机、评分、序列化、存储、审计、缓存 + 四个业务服务）；
5. `app.api` 路由层，**只做参数校验与响应组装**。

红线：字段级权限只在 `serializers.py` 实现；状态变更只在 `state_machine.py` 实现。
"""

from __future__ import annotations

__all__ = [
    "admin_service",
    "attribute_service",
    "audit_service",
    "cache",
    "claim_service",
    "file_service",
    "item_service",
    "scoring",
    "serializers",
    "state_machine",
    "storage",
]
