"""统一响应封装（API 契约 §1.3 / §1.4）。

所有接口必须返回这里的 `ok()` / `fail()` 结构，**不允许**直接 return 裸 dict。
"""

from __future__ import annotations

import math
from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

T = TypeVar("T")


class Pagination(BaseModel):
    page: int = Field(..., ge=1, description="当前页，从 1 开始")
    page_size: int = Field(..., ge=1, description="每页条数")
    total: int = Field(..., ge=0, description="总条数")
    total_pages: int = Field(..., ge=0, description="总页数")

    @classmethod
    def build(cls, page: int, page_size: int, total: int) -> "Pagination":
        return cls(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=math.ceil(total / page_size) if page_size else 0,
        )


class PagedData(BaseModel, Generic[T]):
    list: list[T]
    pagination: Pagination


class SuccessResponse(BaseModel):
    code: str = "OK"
    message: str = "success"
    data: Any = None
    request_id: str = ""


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: Any = None
    request_id: str = ""


def ok(data: Any = None, message: str = "success", request_id: str = "") -> dict[str, Any]:
    """构造成功响应体。"""
    return {"code": "OK", "message": message, "data": data, "request_id": request_id}


def paged(
    items: list[Any],
    *,
    page: int,
    page_size: int,
    total: int,
    message: str = "success",
    request_id: str = "",
) -> dict[str, Any]:
    """构造分页成功响应体。"""
    return ok(
        data={
            "list": items,
            "pagination": Pagination.build(page, page_size, total).model_dump(),
        },
        message=message,
        request_id=request_id,
    )


def fail(
    code: str,
    message: str,
    *,
    detail: Any = None,
    request_id: str = "",
    http_status: int = 400,
) -> JSONResponse:
    """构造错误响应（返回 JSONResponse 以便自定义状态码）。"""
    return JSONResponse(
        status_code=http_status,
        content={
            "code": code,
            "message": message,
            "detail": detail,
            "request_id": request_id,
        },
    )
