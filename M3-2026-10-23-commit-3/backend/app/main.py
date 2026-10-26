"""应用入口（FastAPI）。

启动装配顺序（**顺序即安全边界**）：

1. `setup_logging()`：先让日志脱敏过滤器生效，后续任何日志都不会泄露密钥/答案；
2. 生产环境配置校验：`settings.missing_required_in_production()` 非空则**拒绝启动**
   （只打印缺失的**变量名**，绝不打印值）；
3. 注册中间件：`RequestIDMiddleware`（最外层，保证所有响应都带 `X-Request-ID`）
   → CORS（`settings.cors_origin_list`）；
4. 挂载 `/static`（仅 `STORAGE_BACKEND=local` 时，指向 `settings.LOCAL_STORAGE_DIR`）；
5. 注册全局异常处理器：`AppError` → 对应状态码；`RequestValidationError` → 400；
   未捕获异常 → 500（响应不含堆栈，堆栈只进日志）。

**红线**：参数错误一律 4xx，绝不返回 500；错误响应结构固定为契约 §1.4。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import current_request_id, validation_detail_from_pydantic
from app.api.v1 import api_router, health_router
from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.logging import setup_logging
from app.core.request_id import REQUEST_ID_HEADER, RequestIDMiddleware
from app.core.response import fail

logger = logging.getLogger(__name__)

APP_TITLE = "拾光 · 校园多模态失物检索与可信认领平台 API"
APP_DESCRIPTION = (
    "「拾光」后端 API（Baseline v1.0）。\n\n"
    "- 统一响应：`{code, message, data|detail, request_id}`；\n"
    "- 认证：`Authorization: Bearer <JWT>`（HS256，2 小时）；\n"
    "- 字段级权限由服务端序列化层实施，前端隐藏字段**不属于**安全措施。"
)

# HTTP 状态码 → 契约错误码（FastAPI/Starlette 内置异常兜底映射）
_STATUS_TO_CODE: dict[int, str] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.VALIDATION_ERROR,  # 错误码表无通用 404 码，保持 4xx 红线并沿用校验码
    405: ErrorCode.VALIDATION_ERROR,
    409: ErrorCode.STATE_CONFLICT,
    413: ErrorCode.VALIDATION_ERROR,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭事件。

    - 启动：初始化日志 → 生产环境配置校验（缺失即**拒绝启动**）；
    - 关闭：仅记录日志（连接池由 SQLAlchemy 自行回收）。
    """
    setup_logging()
    logger.info("正在启动「拾光」后端 env=%s version=%s", settings.APP_ENV, settings.APP_VERSION)

    if settings.is_production:
        missing = settings.missing_required_in_production()
        if missing:
            # ⚠ 只打印变量名，绝不打印值（值可能包含密钥）
            logger.critical("生产环境缺少必填配置项，拒绝启动：%s", ", ".join(missing))
            raise RuntimeError(
                "生产环境缺少必填配置项，已拒绝启动（请通过环境变量注入）：" + ", ".join(missing)
            )

    logger.info(
        "启动完成 storage_backend=%s redis_enabled=%s demo_login=%s",
        settings.STORAGE_BACKEND,
        settings.REDIS_ENABLED,
        settings.demo_login_enabled,
    )
    yield
    logger.info("「拾光」后端已停止")


def _register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（契约 §1.4 / §1.5）。"""

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        """业务异常 → 契约错误码 + 对应 HTTP 状态码。"""
        if exc.http_status >= 500:
            logger.error("业务异常(%s)：%s", exc.code, exc.message)
        else:
            logger.info("业务异常(%s)：%s", exc.code, exc.message)
        return fail(
            exc.code,
            exc.message,
            detail=exc.detail,
            request_id=current_request_id(request),
            http_status=exc.http_status,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        """请求体/查询参数校验失败 → **400** + `VALIDATION_ERROR` + 字段级 detail（绝不 500）。"""
        detail = validation_detail_from_pydantic(exc)
        logger.info("参数校验失败 path=%s detail=%s", request.url.path, detail[:3])
        return fail(
            ErrorCode.VALIDATION_ERROR,
            "参数校验失败",
            detail=detail,
            request_id=current_request_id(request),
            http_status=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """框架内置 HTTP 异常（404 路由不存在、405 方法不允许等）→ 统一响应结构。"""
        status_code = int(exc.status_code)
        code = _STATUS_TO_CODE.get(status_code, ErrorCode.INTERNAL_ERROR)
        message = str(exc.detail) if exc.detail else "请求无法处理"
        return fail(
            code,
            message,
            detail=None,
            request_id=current_request_id(request),
            http_status=status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """未捕获异常 → 500 + `INTERNAL_ERROR`；**堆栈只进日志，绝不进响应**。"""
        logger.exception("未处理异常 path=%s type=%s", request.url.path, type(exc).__name__)
        return fail(
            ErrorCode.INTERNAL_ERROR,
            "服务内部错误，请稍后重试或联系管理员",
            detail=None,
            request_id=current_request_id(request),
            http_status=500,
        )


def _mount_static(app: FastAPI) -> None:
    """挂载本地静态资源（仅 `STORAGE_BACKEND=local`）。

    `LocalStorageBackend.public_prefix` 为 `/static/objects`，
    因此主挂载点必须是 `/static/objects`（指向 `settings.LOCAL_STORAGE_DIR`），
    再额外把 `/static` 也指向同一目录，兼容直接用 `/static/<key>` 访问的调试场景。
    """
    if settings.STORAGE_BACKEND != "local":
        return
    storage_dir = os.path.abspath(settings.LOCAL_STORAGE_DIR)
    os.makedirs(storage_dir, exist_ok=True)
    app.mount("/static/objects", StaticFiles(directory=storage_dir), name="static-objects")
    app.mount("/static", StaticFiles(directory=storage_dir), name="static")


def _root_handler(request: Request) -> dict[str, Any]:
    """根路径：仅返回服务标识，便于人工确认服务已启动（不含任何敏感信息）。"""
    from app.core.response import ok

    return ok(
        {
            "service": settings.APP_NAME,
            "title": APP_TITLE,
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/health",
        },
        request_id=current_request_id(request),
    )


def create_app() -> FastAPI:
    """创建并装配 FastAPI 应用（工厂函数，便于测试与多环境部署）。"""
    app = FastAPI(
        title=APP_TITLE,
        version=settings.APP_VERSION,
        description=APP_DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # ---- 中间件：RequestID 必须在最外层，保证任何响应（含 CORS 预检失败）都带追踪 ID ----
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-Model-Version"],
    )
    app.add_middleware(RequestIDMiddleware)  # 后添加者更靠外层 → RequestID → CORS → 路由

    _register_exception_handlers(app)
    _mount_static(app)

    app.include_router(health_router)  # /health、/health/live、/health/ready
    app.include_router(api_router)  # /api/v1/...
    app.add_api_route("/", _root_handler, include_in_schema=False)

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - 本地手工启动
    import uvicorn

    uvicorn.run("app.main:app", host=settings.APP_HOST, port=settings.APP_PORT, reload=False)
