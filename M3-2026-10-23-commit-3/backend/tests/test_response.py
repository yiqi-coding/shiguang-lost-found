"""统一响应封装测试（契约 §1.3 / §1.4）。

覆盖：

- `ok()` / `paged()` / `fail()` 的结构与契约一致；
- `Pagination.build` 的 `total_pages` 向上取整正确（含边界）。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.errors import ErrorCode, ValidationError
from app.core.response import ErrorResponse, Pagination, SuccessResponse, fail, ok, paged


class TestOk(unittest.TestCase):
    """成功响应结构。"""

    def test_structure(self) -> None:
        body = ok({"id": 1}, message="发布成功", request_id="req_abc")
        self.assertEqual(set(body.keys()), {"code", "message", "data", "request_id"})
        self.assertEqual(body["code"], "OK")
        self.assertEqual(body["message"], "发布成功")
        self.assertEqual(body["data"], {"id": 1})
        self.assertEqual(body["request_id"], "req_abc")

    def test_defaults(self) -> None:
        body = ok()
        self.assertEqual(body["message"], "success")
        self.assertIsNone(body["data"])
        self.assertEqual(body["request_id"], "")

    def test_matches_success_model(self) -> None:
        body = ok({"a": 1}, request_id="req_x")
        model = SuccessResponse(**body)
        self.assertEqual(model.code, "OK")


class TestPaged(unittest.TestCase):
    """分页响应结构。"""

    def test_structure(self) -> None:
        body = paged([1, 2, 3], page=1, page_size=20, total=137, request_id="req_1")
        self.assertEqual(body["code"], "OK")
        self.assertEqual(set(body["data"].keys()), {"list", "pagination"})
        self.assertEqual(body["data"]["pagination"], {"page": 1, "page_size": 20, "total": 137, "total_pages": 7})
        self.assertEqual(body["request_id"], "req_1")

    def test_empty_list(self) -> None:
        body = paged([], page=2, page_size=10, total=0)
        self.assertEqual(body["data"]["list"], [])
        self.assertEqual(body["data"]["pagination"]["total_pages"], 0)


class TestPagination(unittest.TestCase):
    """`Pagination.build` 的 total_pages 向上取整。"""

    def test_ceil(self) -> None:
        cases = [
            (1, 20, 137, 7),
            (1, 20, 140, 7),
            (1, 20, 141, 8),
            (1, 20, 1, 1),
            (1, 20, 0, 0),
            (3, 50, 101, 3),
            (1, 20, 20, 1),
            (1, 20, 21, 2),
        ]
        for page, page_size, total, expected in cases:
            with self.subTest(page=page, page_size=page_size, total=total):
                self.assertEqual(Pagination.build(page, page_size, total).total_pages, expected)

    def test_zero_page_size_is_rejected_by_model(self) -> None:
        """契约 §1.1 规定 `page_size ≥ 1`；模型层直接拒绝 0，从而杜绝除零。"""
        from pydantic import ValidationError as PydanticValidationError

        with self.assertRaises(PydanticValidationError):
            Pagination.build(1, 0, 10)

    def test_fields_are_preserved(self) -> None:
        pagination = Pagination.build(4, 15, 46)
        self.assertEqual((pagination.page, pagination.page_size, pagination.total), (4, 15, 46))


def _json_body(response) -> dict:
    """读取 `JSONResponse` 的响应体，兼容不同 Starlette 版本。

    背景：
        - 旧版 Starlette 的 `Response.content` 直接是解析后的 dict；
        - Starlette 1.x 移除了 `content`，只有 `body`（bytes）。
        测试原先写死 `response.content[...]`，在真实新版 Starlette 下必然
        AttributeError —— 属于「替身跑得通、真依赖跑不通」的典型问题。
    """
    if hasattr(response, "content") and isinstance(response.content, dict):
        return response.content
    import json

    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return json.loads(body.decode("utf-8"))
    return json.loads(str(body))


class TestFail(unittest.TestCase):
    """错误响应结构（返回 JSONResponse 以便自定义状态码）。"""

    def test_default_status_is_400(self) -> None:
        response = fail(ErrorCode.VALIDATION_ERROR, "参数校验失败", request_id="req_err")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            _json_body(response),
            {
                "code": "VALIDATION_ERROR",
                "message": "参数校验失败",
                "detail": None,
                "request_id": "req_err",
            },
        )

    def test_field_level_detail(self) -> None:
        detail = [{"field": "title", "reason": "标题长度需在 5~80 字之间"}]
        response = fail(ErrorCode.VALIDATION_ERROR, "参数校验失败", detail=detail, http_status=422)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(_json_body(response)["detail"], detail)

    def test_all_contract_error_codes_are_supported(self) -> None:
        for code in (
            ErrorCode.VALIDATION_ERROR,
            ErrorCode.UNAUTHORIZED,
            ErrorCode.FORBIDDEN,
            ErrorCode.ITEM_NOT_FOUND,
            ErrorCode.CLAIM_NOT_FOUND,
            ErrorCode.MATCH_NOT_FOUND,
            ErrorCode.STATE_CONFLICT,
            ErrorCode.ALGORITHM_SERVICE_ERROR,
            ErrorCode.STORAGE_SERVICE_ERROR,
            ErrorCode.AI_SERVICE_ERROR,
            ErrorCode.RATE_LIMITED,
            ErrorCode.INTERNAL_ERROR,
        ):
            with self.subTest(code=code):
                response = fail(code, "错误")
                self.assertEqual(_json_body(response)["code"], code)

    def test_matches_error_model(self) -> None:
        response = fail(ErrorCode.ITEM_NOT_FOUND, "物品记录不存在", request_id="req_404")
        model = ErrorResponse(**_json_body(response))
        self.assertEqual(model.code, "ITEM_NOT_FOUND")
        self.assertEqual(model.request_id, "req_404")

    def test_app_error_maps_to_http_status(self) -> None:
        """业务异常自带 HTTP 状态码，异常处理器直接使用。"""
        error = ValidationError("参数校验失败", detail=[{"field": "x", "reason": "y"}])
        self.assertEqual(error.code, "VALIDATION_ERROR")
        self.assertEqual(error.http_status, 400)
        response = fail(error.code, error.message, detail=error.detail, http_status=error.http_status)
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
