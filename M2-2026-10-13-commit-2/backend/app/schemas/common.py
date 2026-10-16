"""通用 Schema 与工具函数。

本模块只承载「跨模块复用」的东西：

- `PageParams`：分页参数（契约 §1.1：`page` 从 1 开始、`page_size` 默认 20、**上限 50**）；
- `FieldError`：字段级错误条目（契约 §1.4：`[{"field": "title", "reason": "..."}]`）；
- JSON 文本解析 / 时间归一化等被多个 Schema 复用的纯函数。

红线：错误信息一律**中文可读**（前端可直接 `ElMessage.error(message)`）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, Query
from pydantic import BaseModel, ConfigDict, Field

# 分页默认值与上限（契约 §1.1 与 settings.PAGE_SIZE_MAX 保持一致）
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

# 返回给前端的时间统一使用东八区展示（入库仍为 UTC，见 docs/database-design.md §1.4）
DISPLAY_TIMEZONE = timezone(timedelta(hours=8))


class PageParams(BaseModel):
    """分页查询参数（契约 §1.1：`page` 从 1 开始、`page_size` 默认 20、上限 50）。

    路由签名请使用下面的 `PaginationParams` 依赖别名，
    不要直接写 `Annotated[PageParams, Query()]`：FastAPI 只在「该模型是
    唯一 query 参数」时才把它展开成扁平字段，否则 `params` 会变成一个必填的
    同名 query 字段并直接 400（详见 `pagination_params()` 的说明）。

    校验失败由全局 `RequestValidationError` 处理器转成
    `400 VALIDATION_ERROR` + 字段级 detail，绝不返回 500。
    """

    model_config = ConfigDict(extra="ignore")
    # ⚠ extra="ignore"：查询参数模型可能接收整个查询串（含其它接口自己的参数、
    #   前端缓存击穿参数等），绝不能让无关参数导致 400。

    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(
        DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="每页条数，默认 20，上限 50"
    )

    @property
    def offset(self) -> int:
        """SQL OFFSET。"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """SQL LIMIT。"""
        return self.page_size


def pagination_params(
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="每页条数，默认 20，上限 50")
    ] = DEFAULT_PAGE_SIZE,
) -> PageParams:
    """分页查询参数（FastAPI 依赖注入形式）。

    ⚠ 为什么不用 `Annotated[PageParams, Query()]`：
    FastAPI 对「Pydantic 模型作为查询参数」的字段展开有明确限制——
    `fastapi/dependencies/utils.py: request_params_to_args()` 中的展开条件为
    `len(fields) == 1`，即**该模型必须是端点函数里唯一进入 `query_params` 的参数**。
    实际路由里 `page`/`page_size` 之外还有 `post_type` / `status` / `keyword` 等
    平铺查询参数，`len(fields) > 1` 导致条件不成立，`params` 会被当成一个
    名为 `params` 的**必填 query 字段**，请求报
    `400 VALIDATION_ERROR {"field": "params", "reason": "Field required"}`，
    且 OpenAPI 里也不会暴露 `page` / `page_size`。

    改用 `Depends` 后，每个字段各自成为独立的 query 参数（带 `ge`/`le` 校验），
    行为与 FastAPI 文档推荐的写法一致，也不受同函数其它查询参数的影响。
    """
    return PageParams(page=page, page_size=page_size)


#: 分页参数依赖别名（路由签名统一使用它，保持 `params.offset` / `params.limit` 用法不变）。
PaginationParams = Annotated[PageParams, Depends(pagination_params)]


class FieldError(BaseModel):
    """字段级错误（契约 §1.4 的 detail 元素）。"""

    field: str = Field(..., description="出错字段名")
    reason: str = Field(..., description="中文错误原因")


def split_csv(value: str | None) -> list[str]:
    """把 `published,matched` 这类逗号分隔串解析为列表（去空白、去空项、保持顺序）。"""
    if not value:
        return []
    result: list[str] = []
    for chunk in str(value).split(","):
        item = chunk.strip()
        if item and item not in result:
            result.append(item)
    return result


def parse_json_text(
    raw: Any,
    *,
    field: str,
    expect: str = "array",
    allow_none: bool = True,
) -> Any:
    """解析「字符串形式的 JSON」表单字段。

    契约 §3.3 规定 `hidden_features` / `public_features` 在 multipart 表单里是
    **字符串形式的 JSON**，因此需要在这里解析成 list（或 dict）。

    :param expect: `array` / `array_or_object`，用于给出精确的中文错误。
    :param allow_none: 空值时返回 None（可选字段）还是报错（必填字段）。
    :raises ValueError: 解析失败或类型不符，错误信息为中文（由 Pydantic 包装为 400）。
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        if allow_none:
            return None
        raise ValueError(f"{field} 不能为空，请提供 JSON 数组")

    # 已经是解析好的对象（例如内部调用或测试直接传 list）
    if isinstance(raw, (list, dict)):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{field} 必须是合法的 JSON（当前无法解析，请检查引号、逗号与括号是否配对）"
            ) from exc
    else:
        raise ValueError(f"{field} 格式不正确，应为 JSON 字符串")

    if expect == "array":
        if not isinstance(parsed, list):
            raise ValueError(f"{field} 必须是 JSON 数组，例如 [\"特征一\", \"特征二\"]")
    elif expect == "array_or_object":
        if not isinstance(parsed, (list, dict)):
            raise ValueError(f"{field} 必须是 JSON 数组或对象")
    return parsed


def to_utc_naive(value: datetime) -> datetime:
    """把时间统一转成「不带时区的 UTC」，与 MySQL DATETIME 存储约定一致。

    - 带时区的时间 → 转换到 UTC 后去掉 tzinfo；
    - 不带时区的时间 → **按 UTC 解释**（契约 §1.1 要求客户端传带时区的 ISO 8601，
      未带时区属于客户端缺陷，此处按 UTC 处理以保证服务端行为确定）。
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    """UTC 时间 → ISO 8601 带时区字符串（响应体使用）。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TIMEZONE).isoformat()


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "DISPLAY_TIMEZONE",
    "PageParams",
    "FieldError",
    "split_csv",
    "parse_json_text",
    "to_utc_naive",
    "iso_utc",
]
