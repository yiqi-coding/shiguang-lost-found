"""请求追踪 ID：每个请求生成/透传 X-Request-ID，贯穿响应头、日志与审计记录。"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    """生成形如 req_7f3a9c21b0d4 的追踪 ID。"""
    return f"req_{uuid.uuid4().hex[:12]}"


def get_request_id() -> str:
    """获取当前请求的追踪 ID（无上下文时返回空串）。"""
    return _request_id_ctx.get()


def set_request_id(value: str) -> None:
    _request_id_ctx.set(value)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求注入追踪 ID。

    - 客户端传入合法 `X-Request-ID` 时透传（便于前端与后端串联排障）；
    - 否则服务端生成；
    - 无论成功失败，响应头都带上该 ID，并写入响应体的 request_id 字段（由各路由读取）。
    """

    MAX_LEN = 64

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        if incoming and len(incoming) <= self.MAX_LEN and incoming.isprintable():
            request_id = incoming
        else:
            request_id = new_request_id()

        set_request_id(request_id)
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
