"""路由契约对齐测试（契约附录 A）。

断言后端实际注册的**全部路由**与 `docs/api-contract.md` 附录 A 逐字一致：
路径、HTTP 方法、创建类接口的状态码（201）。

这样「接口清单」不再依赖人工比对——契约改动而代码没跟上时，测试会立刻失败。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.main import app

# 契约附录 A 中由后端实现的接口（路径, 方法, 期望状态码）
EXPECTED_ROUTES: tuple[tuple[str, str, int | None], ...] = (
    # 健康检查（3.1 / 部署探针）
    ("/health", "GET", None),
    ("/health/live", "GET", None),
    ("/health/ready", "GET", None),
    # 认证（3.2）
    ("/api/v1/auth/login", "POST", None),
    ("/api/v1/auth/me", "GET", None),
    ("/api/v1/auth/demo-login", "POST", None),
    # 物品（3.3 / 3.4 / 3.5 / 3.14）
    ("/api/v1/items", "POST", 201),
    ("/api/v1/items", "GET", None),
    ("/api/v1/items/{item_id}", "GET", None),
    ("/api/v1/items/{item_id}/extract-attributes", "POST", None),
    # 匹配（3.6 / 3.7）
    ("/api/v1/items/{item_id}/matches", "GET", None),
    ("/api/v1/items/{item_id}/match", "POST", None),
    # 认领（3.8 / 3.9 / 3.10.1）
    ("/api/v1/claims", "POST", 201),
    ("/api/v1/claims/{claim_id}/verify", "POST", None),
    ("/api/v1/claims/{claim_id}", "GET", None),
    # 我的记录（3.10）
    ("/api/v1/me/items", "GET", None),
    ("/api/v1/me/claims", "GET", None),
    # 管理员（3.10.2 / 3.11 / 3.12 / 3.13）
    ("/api/v1/admin/claims", "GET", None),
    ("/api/v1/admin/claims/{claim_id}", "GET", None),
    ("/api/v1/admin/claims/{claim_id}/review", "POST", None),
    ("/api/v1/admin/audit-logs", "GET", None),
)


def _route_table() -> set[tuple[str, str]]:
    """收集 (路径, 方法)；跳过静态资源挂载等无方法的条目。"""
    table: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            table.add((path, method.upper()))
    return table


def _find_route(path: str, method: str):
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if getattr(route, "path", None) == path and method in {item.upper() for item in methods}:
            return route
    return None


class TestRouteTable(unittest.TestCase):
    """附录 A 全量接口必须存在。"""

    def test_all_contract_routes_registered(self) -> None:
        table = _route_table()
        missing = [
            (path, method)
            for path, method, _status in EXPECTED_ROUTES
            if (path, method) not in table
        ]
        self.assertEqual(missing, [], msg=f"以下契约接口未注册：{missing}")

    def test_no_unexpected_api_routes(self) -> None:
        """反向检查：不得出现契约外的 `/api/v1` 路由（防止悄悄加接口）。"""
        expected_pairs = {(path, method) for path, method, _status in EXPECTED_ROUTES}
        actual = {
            (path, method)
            for path, method in _route_table()
            if path.startswith("/api/v1")
        }
        self.assertEqual(
            sorted(actual - expected_pairs), [], msg="存在契约未定义的 /api/v1 路由"
        )

    def test_create_endpoints_return_201(self) -> None:
        """发布物品与发起认领必须返回 201（契约 §1.2）。"""
        for path, method, status in EXPECTED_ROUTES:
            if status is None:
                continue
            with self.subTest(path=path, method=method):
                route = _find_route(path, method)
                self.assertIsNotNone(route)
                self.assertEqual(getattr(route, "status_code", None), status)

    def test_health_routes_are_outside_api_prefix(self) -> None:
        """健康检查挂在根路径（K8s 探针直接访问 /health）。"""
        table = _route_table()
        for path in ("/health", "/health/live", "/health/ready"):
            self.assertIn((path, "GET"), table)


class TestAppConfiguration(unittest.TestCase):
    """应用装配（标题 / 版本 / 中间件 / 异常处理器 / 静态资源）。"""

    def test_app_title_and_version(self) -> None:
        self.assertEqual(app.title, "拾光 · 校园多模态失物检索与可信认领平台 API")
        from app.core.config import settings

        self.assertEqual(app.version, settings.APP_VERSION)

    def test_middleware_order_request_id_then_cors(self) -> None:
        """契约要求：RequestIDMiddleware 在最外层，其后是 CORS。"""
        from app.core.request_id import RequestIDMiddleware
        from fastapi.middleware.cors import CORSMiddleware

        classes = [middleware for middleware, _options in app.user_middleware]
        self.assertIn(RequestIDMiddleware, classes)
        self.assertIn(CORSMiddleware, classes)
        # 后注册者更靠外层 → RequestID 必须在 CORS 之后注册
        self.assertGreater(classes.index(RequestIDMiddleware), classes.index(CORSMiddleware))

    def test_exception_handlers_registered(self) -> None:
        """四类异常处理器必须齐备（参数错误绝不返回 500）。"""
        from fastapi.exceptions import RequestValidationError
        from starlette.exceptions import HTTPException as StarletteHTTPException

        from app.core.errors import AppError

        handlers = app.exception_handlers
        for exc_class in (AppError, RequestValidationError, StarletteHTTPException, Exception):
            self.assertIn(exc_class, handlers, msg=f"缺少 {exc_class} 的异常处理器")

    def test_static_mounted_for_local_storage(self) -> None:
        """STORAGE_BACKEND=local 时必须挂载 /static/objects（签名 URL 的实际前缀）。"""
        from app.core.config import settings

        if settings.STORAGE_BACKEND != "local":
            self.skipTest("当前配置不是本地存储")
        mounts = {
            getattr(route, "path", None)
            for route in app.routes
            if not getattr(route, "methods", None)
        }
        self.assertIn("/static/objects", mounts)

    def test_cors_origins_from_settings(self) -> None:
        from app.core.config import settings
        from fastapi.middleware.cors import CORSMiddleware

        options = next(
            options for middleware, options in app.user_middleware if middleware is CORSMiddleware
        )
        self.assertEqual(options["allow_origins"], settings.cors_origin_list)


class TestProductionGuards(unittest.TestCase):
    """生产环境启动红线（契约 §3.2.3 + 需求 §13.5）。"""

    def test_demo_login_only_in_non_production(self) -> None:
        """`demo-login` 仅在非生产环境可用（生产环境接口返回 404）。"""
        from app.core.config import Settings

        self.assertFalse(Settings(APP_ENV="production").demo_login_enabled)
        self.assertTrue(Settings(APP_ENV="demo").demo_login_enabled)
        self.assertTrue(Settings(APP_ENV="development").demo_login_enabled)
        self.assertTrue(Settings(APP_ENV="production").is_production)

    def test_missing_required_reports_names_only(self) -> None:
        """缺失必填项只返回**变量名**，绝不回显密钥/连接串的值。"""
        from app.core.config import Settings

        prod = Settings(APP_ENV="production")
        missing = prod.missing_required_in_production()
        self.assertIn("OBS_ENDPOINT", missing)
        self.assertIn("OBS_BUCKET", missing)
        self.assertIn("OBS_ACCESS_KEY", missing)
        self.assertIn("OBS_SECRET_KEY", missing)

        joined = ",".join(missing)
        self.assertNotIn("mysql", joined)  # 不得回显数据库连接串
        self.assertNotIn("root", joined)  # 不得回显账号/密码
        for name in missing:
            self.assertRegex(name, r"^[A-Z_]+(\(长度需≥32\))?$")

    def test_short_secret_key_flagged(self) -> None:
        from app.core.config import Settings

        prod = Settings(APP_ENV="production", APP_SECRET_KEY="short")
        self.assertTrue(any("APP_SECRET_KEY" in name for name in prod.missing_required_in_production()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
