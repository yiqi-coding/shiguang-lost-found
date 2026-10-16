"""第三方依赖打桩（**仅在依赖缺失时安装**）。

背景：本次交付环境未安装 `fastapi / sqlalchemy / pydantic-settings / PyJWT / passlib / redis / alembic`
（也无网络）。为了让 `python -m unittest discover -s tests -v` 能跑通纯逻辑测试，
这里为**缺失**的依赖注入最小可用替身：

| 替身 | 用途 | 说明 |
|---|---|---|
| `pydantic_settings` | `BaseSettings` / `SettingsConfigDict` | 从环境变量读取配置，行为与真实实现一致 |
| `sqlalchemy` | 声明式模型、`select()` 等 | 只保证「模型类可定义、对象可实例化」，**不连接任何数据库** |
| `fastapi` / `starlette` | 路由注册 | 记录路由表，用于断言「路径与方法逐字对齐契约」 |
| `jwt` | HS256 签发/校验 | 使用 `hmac`+`hashlib` 实现，语义与 PyJWT 对齐 |
| `passlib` | `CryptContext(schemes=["bcrypt"])` | 用 PBKDF2-HMAC-SHA256 模拟（真实环境仍走 bcrypt） |

⚠ 若本机已 `pip install -r requirements.txt`，本模块**什么也不做**，测试跑在真实依赖上。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import secrets
import sys
import time
import types
from typing import Any

INSTALLED_STUBS: list[str] = []


# ----------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------
def _is_missing(module_name: str) -> bool:
    """判断模块是否不可导入。"""
    try:
        importlib.import_module(module_name)
        return False
    except Exception:  # noqa: BLE001 - 任何导入失败都视为缺失
        return True


def _new_module(name: str, **attrs: Any) -> types.ModuleType:
    """创建并注册一个模块（父包必须已存在）。"""
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child, module)
    return module


def _ensure_package(name: str) -> types.ModuleType:
    """确保包（含中间层级）存在。"""
    if name in sys.modules:
        return sys.modules[name]
    if "." in name:
        parent_name, _, child = name.rpartition(".")
        parent = _ensure_package(parent_name)
        module = _new_module(name)
        setattr(parent, child, module)
        return module
    return _new_module(name)


# ----------------------------------------------------------------------
# 1) pydantic_settings
# ----------------------------------------------------------------------
def _install_pydantic_settings() -> None:
    from pydantic import BaseModel

    module = _new_module("pydantic_settings")

    class SettingsConfigDict(dict):  # type: ignore[type-arg]
        """真实实现是一个 TypedDict；这里退化为普通 dict（Pydantic 会忽略未知键）。"""

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)

    class BaseSettings(BaseModel):
        """最小实现：字段值优先取环境变量（大小写不敏感），其次取传入值/默认值。"""

        def __init__(self, **data: Any) -> None:
            values: dict[str, Any] = {}
            environ = {key.upper(): value for key, value in os.environ.items()}
            for name in self.__class__.model_fields:
                if name.upper() in environ:
                    values[name] = environ[name.upper()]
            values.update(data)
            super().__init__(**values)

    module.SettingsConfigDict = SettingsConfigDict
    module.BaseSettings = BaseSettings


# ----------------------------------------------------------------------
# 2) sqlalchemy（仅支持「定义模型 + 构造对象」，不做任何 SQL 执行）
# ----------------------------------------------------------------------
class _Expr:
    """SQL 表达式占位对象（支持链式调用，便于 `select(X).where(...)` 之类代码原样执行）。

    注意：构造参数**全部**走 `*args`，避免与调用方传入的 `name=` 关键字冲突
    （如 `ForeignKey("users.id", name="fk_x")`）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    @property
    def label(self) -> str:
        return str(self.args[0]) if self.args else "expr"

    def __getattr__(self, item: str) -> Any:
        if item.startswith("_"):
            raise AttributeError(item)

        def _chain(*args: Any, **kwargs: Any) -> "_Expr":
            return _Expr(f"{self.label}.{item}", *args, **kwargs)

        return _chain

    def __call__(self, *args: Any, **kwargs: Any) -> "_Expr":
        return _Expr(self.label, *args, **kwargs)

    def __bool__(self) -> bool:  # 避免误用
        return True

    def __repr__(self) -> str:
        return f"<Expr {self.label}>"


class _Func:
    """`sqlalchemy.func` 替身：`func.count()` / `func.current_timestamp()` 等均返回占位表达式。"""

    def __getattr__(self, item: str) -> Any:
        def _call(*args: Any, **kwargs: Any) -> _Expr:
            return _Expr(f"func.{item}", *args, **kwargs)

        return _call


class _ColumnStub:
    """`mapped_column()` 的返回值：未显式赋值的实例属性回落到列默认值。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.name: str | None = kwargs.get("name")

    def __set_name__(self, owner: type, name: str) -> None:
        if not self.name:
            self.name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Any:
        if obj is None:
            return self
        # 数据描述符优先于实例字典，因此必须显式检查实例字典（模型赋值后的值优先）
        if self.name and self.name in obj.__dict__:
            return obj.__dict__[self.name]
        default = self.kwargs.get("default")
        if callable(default):
            try:
                return default()
            except Exception:  # noqa: BLE001
                return None
        return default

    def __set__(self, obj: Any, value: Any) -> None:
        obj.__dict__[self.name or "value"] = value


class _TypeStub:
    """列类型占位（`String(64)` / `TINYINT(1)` / `DECIMAL(6,5)` …）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.args}>"


class _Metadata:
    """`Base.metadata` 替身。"""

    def __init__(self) -> None:
        self.tables: dict[str, Any] = {}

    def create_all(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - 测试不建库
        return None

    def drop_all(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        return None


class Mapped:  # noqa: N801 - 与 SQLAlchemy 同名
    """`Mapped[int]` 只需可下标化，注解本身不参与运行。"""

    def __class_getitem__(cls, item: Any) -> Any:  # noqa: N805
        return item


class DeclarativeBase:
    """声明式基类替身：`Model(**kwargs)` 直接设置实例属性。"""

    metadata = _Metadata()

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} id={getattr(self, 'id', None)}>"


class Session:  # noqa: N801 - 与 SQLAlchemy 同名
    """会话替身：仅用于类型标注，调用即报错（测试不允许连库）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._args = args

    def __getattr__(self, item: str) -> Any:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("测试环境不允许访问数据库（Session 为打桩实现）")

        return _blocked


def _install_sqlalchemy() -> None:
    sa = _ensure_package("sqlalchemy")
    orm = _ensure_package("sqlalchemy.orm")
    engine = _ensure_package("sqlalchemy.engine")
    dialects = _ensure_package("sqlalchemy.dialects")
    mysql = _ensure_package("sqlalchemy.dialects.mysql")

    # ---- sqlalchemy 顶层 ----
    for name in (
        "BigInteger",
        "Integer",
        "SmallInteger",
        "String",
        "Text",
        "DateTime",
        "Date",
        "JSON",
        "Numeric",
        "Float",
        "Boolean",
        "LargeBinary",
        "Enum",
        "Interval",
    ):
        setattr(sa, name, type(name, (_TypeStub,), {}))

    sa.Column = lambda *args, **kwargs: _ColumnStub(*args, **kwargs)
    sa.ForeignKey = lambda *args, **kwargs: _Expr("ForeignKey", *args, **kwargs)
    sa.Index = lambda *args, **kwargs: _Expr("Index", *args, **kwargs)
    sa.UniqueConstraint = lambda *args, **kwargs: _Expr("UniqueConstraint", *args, **kwargs)
    sa.CheckConstraint = lambda *args, **kwargs: _Expr("CheckConstraint", *args, **kwargs)
    sa.PrimaryKeyConstraint = lambda *args, **kwargs: _Expr("PrimaryKeyConstraint", *args, **kwargs)
    sa.ForeignKeyConstraint = lambda *args, **kwargs: _Expr("ForeignKeyConstraint", *args, **kwargs)
    sa.Table = lambda *args, **kwargs: _Expr("Table", *args, **kwargs)
    sa.MetaData = _Metadata
    sa.func = _Func()

    def _expr_factory(name: str):
        def _factory(*args: Any, **kwargs: Any) -> _Expr:
            return _Expr(name, *args, **kwargs)

        return _factory

    for api in ("select", "and_", "or_", "not_", "cast", "desc", "asc", "text", "literal", "case", "union"):
        setattr(sa, api, _expr_factory(api))

    sa.create_engine = _expr_factory("create_engine")
    sa.inspect = _expr_factory("inspect")
    sa.engine_from_config = _expr_factory("engine_from_config")

    # 类型注解用得到的类（仅作为类型名存在）
    for name in ("Select", "Insert", "Update", "Delete", "Column", "Table", "MetaData", "Index", "Engine"):
        if not hasattr(sa, name):
            setattr(sa, name, type(name, (_Expr,), {}))

    # ---- sqlalchemy.orm ----
    orm.DeclarativeBase = DeclarativeBase
    orm.Mapped = Mapped
    orm.mapped_column = lambda *args, **kwargs: _ColumnStub(*args, **kwargs)
    orm.relationship = lambda *args, **kwargs: None
    orm.Session = Session
    orm.sessionmaker = _expr_factory("sessionmaker")
    for loader in ("selectinload", "joinedload", "subqueryload", "lazyload"):
        setattr(orm, loader, _expr_factory(loader))
    orm.aliased = _expr_factory("aliased")

    # ---- sqlalchemy.engine ----
    engine.Engine = type("Engine", (), {})
    engine.Connection = type("Connection", (), {})

    # ---- sqlalchemy.dialects.mysql ----
    mysql.TINYINT = type("TINYINT", (_TypeStub,), {})
    mysql.BIGINT = type("BIGINT", (_TypeStub,), {})
    mysql.DECIMAL = type("DECIMAL", (_TypeStub,), {})
    mysql.INTEGER = type("INTEGER", (_TypeStub,), {})
    mysql.VARCHAR = type("VARCHAR", (_TypeStub,), {})
    dialects.mysql = mysql


# ----------------------------------------------------------------------
# 3) jwt（HS256，语义与 PyJWT 对齐）
# ----------------------------------------------------------------------
def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class _JwtError(Exception):
    """PyJWT 异常基类替身。"""


class _ExpiredSignatureError(_JwtError):
    """Token 过期。"""


def _install_jwt() -> None:
    module = _new_module("jwt")

    def encode(payload: dict[str, Any], key: str, algorithm: str = "HS256") -> str:
        header = {"alg": algorithm, "typ": "JWT"}
        segments = [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")),
        ]
        signing_input = ".".join(segments).encode("ascii")
        signature = hmac.new(str(key).encode("utf-8"), signing_input, hashlib.sha256).digest()
        segments.append(_b64url_encode(signature))
        return ".".join(segments)

    def decode(token: str, key: str, algorithms: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        try:
            header_segment, payload_segment, signature_segment = token.split(".")
        except ValueError as exc:
            raise _JwtError("Not enough segments") from exc
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        expected = hmac.new(str(key).encode("utf-8"), signing_input, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(signature_segment)):
            raise _JwtError("Signature verification failed")
        payload = json.loads(_b64url_decode(payload_segment))
        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise _ExpiredSignatureError("Signature has expired")
        return payload

    module.encode = encode
    module.decode = decode
    module.PyJWTError = _JwtError
    module.InvalidTokenError = _JwtError
    module.ExpiredSignatureError = _ExpiredSignatureError
    module.__version__ = "stub-2.10.1"


# ----------------------------------------------------------------------
# 4) passlib（bcrypt 语义的最小替身：PBKDF2-HMAC-SHA256，带随机盐）
# ----------------------------------------------------------------------
def _install_passlib() -> None:
    _ensure_package("passlib")
    context_module = _ensure_package("passlib.context")

    class _CryptContext:
        """`CryptContext(schemes=["bcrypt"])` 替身。

        真实环境使用 passlib+bcrypt；此处用 PBKDF2-HMAC-SHA256（加随机盐）模拟
        「同一密码两次哈希不同、验证可还原」的语义，保证安全相关测试有意义。
        """

        def __init__(self, schemes: list[str] | None = None, deprecated: Any = "auto", **kwargs: Any) -> None:
            self.schemes = schemes or ["bcrypt"]
            self.deprecated = deprecated
            self.rounds = 120_000

        def hash(self, secret: str, **kwargs: Any) -> str:
            salt = secrets.token_hex(16)
            digest = hashlib.pbkdf2_hmac(
                "sha256", str(secret).encode("utf-8"), salt.encode("utf-8"), self.rounds
            ).hex()
            return f"$pbkdf2-sha256${self.rounds}${salt}${digest}"

        def verify(self, secret: str, hashed: str, **kwargs: Any) -> bool:
            try:
                _, algorithm, rounds, salt, digest = str(hashed).split("$")
                if algorithm != "pbkdf2-sha256":
                    raise ValueError("unsupported scheme")
                candidate = hashlib.pbkdf2_hmac(
                    "sha256", str(secret).encode("utf-8"), salt.encode("utf-8"), int(rounds)
                ).hex()
                return hmac.compare_digest(candidate, digest)
            except Exception:  # noqa: BLE001 - 哈希格式异常一律视为验证失败
                raise ValueError("invalid hash format") from None

        def needs_update(self, hashed: str) -> bool:  # pragma: no cover - 兼容接口
            return False

    context_module.CryptContext = _CryptContext


# ----------------------------------------------------------------------
# 5) starlette + fastapi（仅注册路由，不做真实请求处理）
# ----------------------------------------------------------------------
class _Route:
    """路由记录（字段与 Starlette 的 `Route` 对齐，便于同一套断言兼容真实依赖）。"""

    def __init__(
        self,
        path: str,
        methods: set[str],
        endpoint: Any,
        *,
        name: str | None = None,
        status_code: int | None = None,
    ) -> None:
        self.path = path
        self.methods = set(methods)
        self.endpoint = endpoint
        self.name = name or getattr(endpoint, "__name__", "endpoint")
        self.status_code = status_code

    def __repr__(self) -> str:
        return f"<Route {sorted(self.methods)} {self.path}>"


class _Mount:
    """静态资源挂载记录。"""

    def __init__(self, path: str, app: Any = None, name: str | None = None) -> None:
        self.path = path
        self.app = app
        self.name = name

    def __repr__(self) -> str:
        return f"<Mount {self.path}>"


class _Depends:
    """`Depends(x)` 替身：保留被依赖对象，供断言使用。"""

    def __init__(self, dependency: Any = None, use_cache: bool = True) -> None:
        self.dependency = dependency
        self.use_cache = use_cache

    def __repr__(self) -> str:
        return f"<Depends {getattr(self.dependency, '__name__', self.dependency)}>"


def _param(default: Any = ..., **kwargs: Any) -> Any:
    """`Query/Form/File/Path/Header` 的公共替身：有默认值就返回默认值，否则返回 None。"""
    return None if default is ... else default


class APIRouter:
    """`fastapi.APIRouter` 替身：把路由记录进 `.routes`（前缀在注册时拼接）。"""

    def __init__(self, prefix: str = "", tags: list[str] | None = None, **kwargs: Any) -> None:
        self.prefix = prefix or ""
        self.tags = tags or []
        self.routes: list[Any] = []

    # -- 注册 --
    def _register(self, path: str, methods: set[str], endpoint: Any, **kwargs: Any) -> Any:
        route = _Route(
            f"{self.prefix}{path}",
            methods,
            endpoint,
            name=kwargs.get("name"),
            status_code=kwargs.get("status_code"),
        )
        self.routes.append(route)
        return endpoint

    def _decorator(self, path: str, methods: set[str], **kwargs: Any):
        def _wrap(endpoint: Any) -> Any:
            return self._register(path, methods, endpoint, **kwargs)

        return _wrap

    def get(self, path: str, **kwargs: Any):
        return self._decorator(path, {"GET"}, **kwargs)

    def post(self, path: str, **kwargs: Any):
        return self._decorator(path, {"POST"}, **kwargs)

    def put(self, path: str, **kwargs: Any):
        return self._decorator(path, {"PUT"}, **kwargs)

    def patch(self, path: str, **kwargs: Any):
        return self._decorator(path, {"PATCH"}, **kwargs)

    def delete(self, path: str, **kwargs: Any):
        return self._decorator(path, {"DELETE"}, **kwargs)

    # -- 组合 --
    def include_router(self, router: "APIRouter", prefix: str = "", **kwargs: Any) -> None:
        base = f"{self.prefix}{prefix}"
        for route in router.routes:
            if isinstance(route, _Mount):
                self.routes.append(route)
            else:
                self.routes.append(
                    _Route(
                        f"{base}{route.path}",
                        route.methods,
                        route.endpoint,
                        name=route.name,
                        status_code=route.status_code,
                    )
                )


class FastAPI:
    """`fastapi.FastAPI` 替身。"""

    def __init__(
        self,
        title: str | None = None,
        version: str | None = None,
        description: str | None = None,
        lifespan: Any = None,
        docs_url: str | None = "/docs",
        redoc_url: str | None = "/redoc",
        openapi_url: str | None = "/openapi.json",
        **kwargs: Any,
    ) -> None:
        self.title = title
        self.version = version
        self.description = description
        self.lifespan = lifespan
        self.docs_url = docs_url
        self.redoc_url = redoc_url
        self.openapi_url = openapi_url
        self.router = APIRouter()
        self.routes: list[Any] = self.router.routes
        self.user_middleware: list[tuple[Any, dict[str, Any]]] = []
        self.exception_handlers: dict[Any, Any] = {}
        self.state = types.SimpleNamespace()

    def add_middleware(self, middleware_class: Any, **options: Any) -> None:
        self.user_middleware.append((middleware_class, options))

    def exception_handler(self, exc_class: Any):
        def _wrap(handler: Any) -> Any:
            self.exception_handlers[exc_class] = handler
            return handler

        return _wrap

    def include_router(self, router: APIRouter, prefix: str = "", **kwargs: Any) -> None:
        self.router.include_router(router, prefix=prefix, **kwargs)

    def add_api_route(self, path: str, endpoint: Any, methods: list[str] | None = None, **kwargs: Any) -> None:
        self.router._register(path, set(methods or ["GET"]), endpoint, **kwargs)

    def mount(self, path: str, app: Any = None, name: str | None = None) -> None:
        self.routes.append(_Mount(path, app, name))


class Request:
    """`starlette.requests.Request` 替身。"""

    def __init__(self, headers: dict[str, str] | None = None, client: Any = None, path: str = "/") -> None:
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.client = client or types.SimpleNamespace(host="127.0.0.1")
        self.state = types.SimpleNamespace()
        self.url = types.SimpleNamespace(path=path)
        self.method = "GET"


class Response:
    """`starlette.responses.Response` 替身。"""

    def __init__(self, content: Any = None, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = dict(headers or {})
        self.content = content


class JSONResponse(Response):
    """`JSONResponse` 替身：保存 `content` 与状态码，便于断言响应结构。"""

    media_type = "application/json"

    def __init__(self, content: Any = None, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        super().__init__(content=content, status_code=status_code, headers=headers)

    @property
    def body(self) -> bytes:
        return json.dumps(self.content, ensure_ascii=False, default=str).encode("utf-8")


class UploadFile:
    """`fastapi.UploadFile` 替身（测试构造用）。"""

    def __init__(self, filename: str = "a.jpg", content_type: str = "image/jpeg", data: bytes = b"") -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self) -> bytes:
        return self._data


class HTTPException(Exception):
    """`starlette.exceptions.HTTPException` 替身。"""

    def __init__(self, status_code: int, detail: Any = None, headers: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


class RequestValidationError(Exception):
    """`fastapi.exceptions.RequestValidationError` 替身。"""

    def __init__(self, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__("request validation error")
        self._errors = errors or []

    def errors(self) -> list[dict[str, Any]]:
        return self._errors


class HTTPAuthorizationCredentials:
    """`fastapi.security.HTTPAuthorizationCredentials` 替身。"""

    def __init__(self, scheme: str = "Bearer", credentials: str = "") -> None:
        self.scheme = scheme
        self.credentials = credentials


class HTTPBearer:
    """`HTTPBearer` 替身。"""

    def __init__(self, auto_error: bool = True, **kwargs: Any) -> None:
        self.auto_error = auto_error
        self.scheme_name = "HTTPBearer"

    def __call__(self, request: Any = None) -> Any:  # pragma: no cover - 测试不真正鉴权
        return None


class CORSMiddleware:
    """`CORSMiddleware` 替身。"""

    def __init__(self, app: Any = None, **options: Any) -> None:
        self.app = app
        self.options = options


class BaseHTTPMiddleware:
    """`BaseHTTPMiddleware` 替身。"""

    def __init__(self, app: Any = None, dispatch: Any = None) -> None:
        self.app = app
        self.dispatch_func = dispatch


class StaticFiles:
    """`StaticFiles` 替身。"""

    def __init__(self, directory: str | None = None, **kwargs: Any) -> None:
        self.directory = directory


def _install_web_stubs() -> None:
    # ---- starlette ----
    starlette = _ensure_package("starlette")
    middleware = _ensure_package("starlette.middleware")
    middleware_base = _ensure_package("starlette.middleware.base")
    requests_module = _ensure_package("starlette.requests")
    responses_module = _ensure_package("starlette.responses")
    exceptions_module = _ensure_package("starlette.exceptions")

    middleware_base.BaseHTTPMiddleware = BaseHTTPMiddleware
    requests_module.Request = Request
    responses_module.Response = Response
    responses_module.JSONResponse = JSONResponse
    exceptions_module.HTTPException = HTTPException
    starlette.middleware = middleware

    # ---- fastapi ----
    fastapi = _ensure_package("fastapi")
    fastapi_responses = _ensure_package("fastapi.responses")
    fastapi_exceptions = _ensure_package("fastapi.exceptions")
    fastapi_middleware = _ensure_package("fastapi.middleware")
    fastapi_cors = _ensure_package("fastapi.middleware.cors")
    fastapi_security = _ensure_package("fastapi.security")
    fastapi_staticfiles = _ensure_package("fastapi.staticfiles")

    fastapi.FastAPI = FastAPI
    fastapi.APIRouter = APIRouter
    fastapi.Depends = lambda dependency=None, use_cache=True: _Depends(dependency, use_cache)
    fastapi.Query = _param
    fastapi.Form = _param
    fastapi.File = _param
    fastapi.Path = _param
    fastapi.Header = _param
    fastapi.Body = _param
    fastapi.Request = Request
    fastapi.Response = Response
    fastapi.UploadFile = UploadFile
    fastapi.HTTPException = HTTPException

    fastapi_responses.JSONResponse = JSONResponse
    fastapi_responses.Response = Response
    fastapi_exceptions.RequestValidationError = RequestValidationError
    fastapi_exceptions.HTTPException = HTTPException
    fastapi_cors.CORSMiddleware = CORSMiddleware
    fastapi_security.HTTPBearer = HTTPBearer
    fastapi_security.HTTPAuthorizationCredentials = HTTPAuthorizationCredentials
    fastapi_staticfiles.StaticFiles = StaticFiles
    fastapi_middleware.cors = fastapi_cors


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def install_stubs() -> list[str]:
    """安装缺失依赖的替身，返回被替换的模块名列表（已安装的依赖不会被替换）。"""
    if INSTALLED_STUBS:
        return INSTALLED_STUBS

    if _is_missing("pydantic_settings"):
        _install_pydantic_settings()
        INSTALLED_STUBS.append("pydantic_settings")

    if _is_missing("sqlalchemy"):
        _install_sqlalchemy()
        INSTALLED_STUBS.append("sqlalchemy")

    if _is_missing("jwt"):
        _install_jwt()
        INSTALLED_STUBS.append("jwt")

    if _is_missing("passlib"):
        _install_passlib()
        INSTALLED_STUBS.append("passlib")

    # Web 框架：仅当 fastapi 与 starlette 都缺失时安装替身（避免半真半假的混合环境）
    if _is_missing("fastapi") and _is_missing("starlette"):
        _install_web_stubs()
        INSTALLED_STUBS.extend(["fastapi", "starlette"])

    return INSTALLED_STUBS


__all__ = ["install_stubs", "INSTALLED_STUBS"]
