"""Redis 缓存层（API 契约 §5）。

设计原则：
1. **数据库是最终事实源**，缓存只是加速；任何缓存异常都必须降级为直连数据库，绝不让业务崩溃；
2. `REDIS_ENABLED=false` 或连接失败 → 进入 `degraded` 模式，所有读写静默跳过；
3. **不得**把隐藏答案明文长期放入 Redis（认领会话只放哈希与元数据）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

KEY_AI_ATTRIBUTES = "ai:attributes:item:{item_id}:{content_hash}"
KEY_MATCH = "match:item:{item_id}:{version}"
KEY_CLAIM_SESSION = "claim:session:{claim_id}"
KEY_RATE_LIMIT = "rate:{scope}:{identity}"


class CacheClient:
    """Redis 客户端封装，带自动降级。"""

    def __init__(self) -> None:
        self._client: Any = None
        self._available = False
        self._degraded = not settings.REDIS_ENABLED
        if settings.REDIS_ENABLED:
            self._connect()

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        try:
            import redis  # 延迟导入，未安装依赖时不影响启动

            self._client = redis.Redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
            )
            self._client.ping()
            self._available = True
            self._degraded = False
            logger.info("Redis 连接成功（缓存已启用）")
        except Exception as exc:  # noqa: BLE001 - 缓存失败必须降级而非崩溃
            self._available = False
            self._degraded = True
            logger.warning("Redis 不可用，已降级为直连数据库：%s", type(exc).__name__)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def degraded(self) -> bool:
        return self._degraded

    def reconnect(self) -> bool:
        """健康检查或运维手动重连。"""
        if not settings.REDIS_ENABLED:
            return False
        self._connect()
        return self._available

    # ------------------------------------------------------------------
    # 基础读写（全部吞异常）
    # ------------------------------------------------------------------
    def get_json(self, key: str) -> Any | None:
        if not self._available:
            return None
        try:
            raw = self._client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            self._soft_fail("get", key, exc)
            return None

    def set_json(self, key: str, value: Any, ttl: int) -> bool:
        if not self._available:
            return False
        try:
            self._client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
            return True
        except Exception as exc:  # noqa: BLE001
            self._soft_fail("set", key, exc)
            return False

    def delete(self, *keys: str) -> int:
        if not self._available or not keys:
            return 0
        try:
            return int(self._client.delete(*keys))
        except Exception as exc:  # noqa: BLE001
            self._soft_fail("delete", keys[0], exc)
            return 0

    def delete_pattern(self, pattern: str) -> int:
        """按前缀清理（用于物品更新后使缓存失效）。

        使用 SCAN 迭代而非 KEYS，避免阻塞 Redis。
        """
        if not self._available:
            return 0
        removed = 0
        try:
            for key in self._client.scan_iter(match=pattern, count=200):
                self._client.delete(key)
                removed += 1
        except Exception as exc:  # noqa: BLE001
            self._soft_fail("delete_pattern", pattern, exc)
        return removed

    def incr_with_ttl(self, key: str, ttl: int) -> int:
        """计数并设置过期（用于限流）。缓存不可用时返回 0，调用方据此跳过限流。"""
        if not self._available:
            return 0
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl)
            count, _ = pipe.execute()
            return int(count)
        except Exception as exc:  # noqa: BLE001
            self._soft_fail("incr", key, exc)
            return 0

    def _soft_fail(self, op: str, key: str, exc: Exception) -> None:
        self._degraded = True
        logger.warning("Redis %s 失败（已降级，键=%s）：%s", op, key, type(exc).__name__)

    # ------------------------------------------------------------------
    # 业务键
    # ------------------------------------------------------------------
    @staticmethod
    def ai_attributes_key(item_id: int, content_hash: str) -> str:
        return KEY_AI_ATTRIBUTES.format(item_id=item_id, content_hash=content_hash)

    @staticmethod
    def match_key(item_id: int, version: str = "v1") -> str:
        return KEY_MATCH.format(item_id=item_id, version=version)

    @staticmethod
    def claim_session_key(claim_id: int) -> str:
        return KEY_CLAIM_SESSION.format(claim_id=claim_id)

    @staticmethod
    def rate_limit_key(scope: str, identity: str) -> str:
        return KEY_RATE_LIMIT.format(scope=scope, identity=identity)

    # ------------------------------------------------------------------
    # 失效策略（物品描述/图片/状态变化时必须调用）
    # ------------------------------------------------------------------
    def invalidate_item(self, item_id: int) -> None:
        """物品变更后清理其 AI 属性缓存与全部匹配缓存。"""
        self.delete_pattern(f"match:item:{item_id}:*")
        self.delete_pattern(f"ai:attributes:item:{item_id}:*")

    def invalidate_claim(self, claim_id: int) -> None:
        self.delete(self.claim_session_key(claim_id))

    # ------------------------------------------------------------------
    def check_health(self) -> dict[str, Any]:
        import time

        if not settings.REDIS_ENABLED:
            return {"status": "not_configured", "degraded": True}
        start = time.perf_counter()
        try:
            if self._client is None:
                raise RuntimeError("client not initialized")
            self._client.ping()
            self._available = True
            return {
                "status": "ok",
                "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                "degraded": False,
            }
        except Exception as exc:  # noqa: BLE001
            self._available = False
            self._degraded = True
            return {"status": "down", "error": type(exc).__name__, "degraded": True}


# 单例
cache = CacheClient()
