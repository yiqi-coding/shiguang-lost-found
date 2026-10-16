"""API 依赖：认证、权限、客户端 IP、限流。

统一在此处实现「谁能访问」与「访问多快」，路由只声明依赖，不重复写校验逻辑：

- `get_current_user`：解析 `Authorization: Bearer <JWT>` → 查库校验用户存在且 `status=active`；
- `get_current_user_optional`：游客可访问的接口（列表、详情、匹配结果）使用；
- `require_admin`：审核台接口使用；
- `get_client_ip`：优先 `X-Forwarded-For` 首个 IP（反向代理场景），其次 `request.client.host`；
- `rate_limit(scope, limit, window_seconds)`：基于 Redis 的固定窗口计数限流，
  **缓存不可用时直接放行**（`incr_with_ttl` 返回 0 表示跳过），保证限流绝不成为单点故障。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any, NoReturn

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, RateLimitedError, UnauthorizedError, ValidationError
from app.core.request_id import get_request_id
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.cache import cache

logger = logging.getLogger(__name__)

# auto_error=False：缺少 Authorization 头时由我们自己抛 UnauthorizedError，
# 保证错误响应结构与契约 §1.4 完全一致（FastAPI 默认会抛 403 + 非标准结构）。
bearer_scheme = HTTPBearer(auto_error=False, description="JWT 访问令牌（HS256，有效期 2h）")

DbSession = Annotated[Session, Depends(get_db)]


# ----------------------------------------------------------------------
# 请求上下文
# ----------------------------------------------------------------------
def current_request_id(request: Request) -> str:
    """当前请求的追踪 ID（由 `RequestIDMiddleware` 写入 `request.state`）。

    所有对外响应都必须带上它（契约 §1.3 / §1.4），便于与审计日志、服务端日志串联排障。
    """
    return getattr(request.state, "request_id", "") or get_request_id()


def get_client_ip(request: Request) -> str:
    """获取客户端 IP。

    经过 Nginx/负载均衡时 `request.client.host` 是代理地址，因此优先取
    `X-Forwarded-For` 的**第一个** IP（最靠近客户端的一跳）。
    """
    forwarded = request.headers.get("X-Forwarded-For") or ""
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    real_ip = (request.headers.get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip
    client = request.client
    return client.host if client and client.host else "unknown"


# ----------------------------------------------------------------------
# 认证
# ----------------------------------------------------------------------
def _resolve_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    """从 `Authorization` 头中取出 Bearer Token，缺失即 401。"""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("未登录或缺少访问令牌")
    if (credentials.scheme or "").lower() != "bearer":
        raise UnauthorizedError("认证方式不正确，请使用 Bearer Token")
    return credentials.credentials


def _load_active_user(db: Session, token: str) -> User:
    """解码 JWT 并加载用户，校验「存在 + 未被禁用」。"""
    payload = decode_access_token(token)  # 失败时内部已抛 UnauthorizedError
    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError) as exc:
        raise UnauthorizedError("登录凭证无效") from exc

    user = db.get(User, user_id)
    if user is None:
        raise UnauthorizedError("账号不存在或已被删除")
    if not user.is_active:
        # 禁用账号即使持有未过期 Token 也不得访问
        raise ForbiddenError("账号已被禁用，请联系管理员")
    return user


def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    """强制登录依赖：校验失败抛 `401 UNAUTHORIZED`。"""
    token = _resolve_token(credentials)
    return _load_active_user(db, token)


def get_current_user_optional(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User | None:
    """游客可访问接口使用：无 Token 返回 None；**带了非法 Token 仍抛 401**。

    这样「游客看脱敏视图、登录用户看自己视角」可以共用同一个序列化入口
    （`serializers.serialize_item(viewer=...)`）。
    """
    if credentials is None or not credentials.credentials:
        return None
    token = _resolve_token(credentials)
    return _load_active_user(db, token)


def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """管理员专用依赖：非 admin 抛 `403 FORBIDDEN`（不重试、不泄露对象是否存在）。"""
    if not user.is_admin:
        raise ForbiddenError("该操作仅管理员可用")
    return user


# 便捷类型别名（路由签名更短）
CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]
AdminUser = Annotated[User, Depends(require_admin)]


# ----------------------------------------------------------------------
# 限流
# ----------------------------------------------------------------------
RateLimitDependency = Callable[..., None]


def rate_limit(scope: str, limit: int, window_seconds: int) -> RateLimitDependency:
    """生成限流依赖（契约 §1.1：登录 5 次/分/IP、认领发起 10 次/时/用户、AI 调用 30 次/时/用户）。

    :param scope: 限流维度标识，参与 Redis Key（如 `login` / `claim_create` / `ai_extract`）
    :param limit: 窗口内允许的最大次数
    :param window_seconds: 窗口长度（秒）

    降级约定（**强制**）：`cache.incr_with_ttl()` 返回 0 表示缓存不可用，
    此时**直接放行**——Redis 故障不得导致用户无法登录或发布。
    命中的限流信息写入 `request.state.rate_limit`，供响应头/排障使用。
    本函数为同步实现，由 FastAPI 放入线程池执行，避免阻塞事件循环。
    """

    def _dependency(
        request: Request,
        user: OptionalUser = None,
    ) -> None:
        identity = f"user:{user.id}" if user else f"ip:{get_client_ip(request)}"
        key = cache.rate_limit_key(scope, identity)
        count = cache.incr_with_ttl(key, window_seconds)

        if count == 0:
            # 缓存未启用或已降级 → 跳过限流（业务连续性优先）
            request.state.rate_limit = None
            return

        request.state.rate_limit = {
            "limit": limit,
            "remaining": max(0, limit - count),
            "window_seconds": window_seconds,
            "scope": scope,
        }
        logger.debug("限流计数 scope=%s identity=%s count=%s/%s", scope, identity, count, limit)

        if count > limit:
            raise RateLimitedError(
                f"操作过于频繁（{window_seconds // 60 or 1} 分钟内最多 {limit} 次），请稍后重试"
            )

    return _dependency


def rate_limit_headers(request: Request) -> dict[str, str]:
    """把限流信息转成响应头（契约 §6：X-RateLimit-*）。无记录时返回空字典。"""
    info = getattr(request.state, "rate_limit", None)
    if not info:
        return {}
    return {
        "X-RateLimit-Limit": str(info.get("limit", "")),
        "X-RateLimit-Remaining": str(info.get("remaining", "")),
        "X-RateLimit-Reset": str(info.get("window_seconds", "")),
    }


# ----------------------------------------------------------------------
# 校验错误转换
# ----------------------------------------------------------------------
def validation_detail_from_pydantic(exc: Any) -> list[dict[str, str]]:
    """把 Pydantic 的 `ValidationError` 转成契约 §1.4 的字段级 detail。

    输出形如 `[{"field": "title", "reason": "标题长度需在 5~80 字之间"}]`，
    中文原因由各 Schema 的校验器提供，英文前缀 `Value error, ` 会被剥离。
    """
    detail: list[dict[str, str]] = []
    for error in getattr(exc, "errors", lambda: [])():
        location = [str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")]
        field = ".".join(location) or "request"
        reason = str(error.get("msg", "参数不合法"))
        for prefix in ("Value error, ", "Assertion failed, "):
            if reason.startswith(prefix):
                reason = reason[len(prefix) :]
        detail.append({"field": field, "reason": reason})
    return detail or [{"field": "request", "reason": "参数校验失败"}]


def raise_validation_error(exc: Any, *, message: str = "参数校验失败") -> NoReturn:
    """把 Pydantic 校验异常转成业务异常（400 + `VALIDATION_ERROR` + 字段级 detail）。"""
    raise ValidationError(message, detail=validation_detail_from_pydantic(exc))


__all__ = [
    "DbSession",
    "CurrentUser",
    "OptionalUser",
    "AdminUser",
    "bearer_scheme",
    "current_request_id",
    "get_client_ip",
    "get_current_user",
    "get_current_user_optional",
    "require_admin",
    "rate_limit",
    "rate_limit_headers",
    "validation_detail_from_pydantic",
    "raise_validation_error",
]
