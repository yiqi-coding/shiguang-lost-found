"""业务错误码与统一异常体系（API 契约 §1.5）。

约定：
- 所有可预期错误抛 `AppError` 子类，由全局异常处理器统一转成规范错误响应；
- **参数错误一律 4xx，绝不返回 500**；
- 异常消息面向用户，不含堆栈与内部细节（堆栈只写日志）。
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """错误码常量（与 docs/api-contract.md §1.5 逐字对应）。"""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
    CLAIM_NOT_FOUND = "CLAIM_NOT_FOUND"
    MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    ALGORITHM_SERVICE_ERROR = "ALGORITHM_SERVICE_ERROR"
    STORAGE_SERVICE_ERROR = "STORAGE_SERVICE_ERROR"
    AI_SERVICE_ERROR = "AI_SERVICE_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 错误码 → HTTP 状态码
ERROR_HTTP_STATUS: dict[str, int] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.ITEM_NOT_FOUND: 404,
    ErrorCode.CLAIM_NOT_FOUND: 404,
    ErrorCode.MATCH_NOT_FOUND: 404,
    ErrorCode.STATE_CONFLICT: 409,
    ErrorCode.ALGORITHM_SERVICE_ERROR: 502,
    ErrorCode.STORAGE_SERVICE_ERROR: 502,
    ErrorCode.AI_SERVICE_ERROR: 502,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """业务异常基类。"""

    code: str = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    default_message: str = "服务内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        detail: Any = None,
        code: str | None = None,
        http_status: int | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.detail = detail
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        else:
            self.http_status = ERROR_HTTP_STATUS.get(self.code, self.http_status)
        super().__init__(self.message)


class ValidationError(AppError):
    code = ErrorCode.VALIDATION_ERROR
    http_status = 400
    default_message = "参数校验失败"


class UnauthorizedError(AppError):
    code = ErrorCode.UNAUTHORIZED
    http_status = 401
    default_message = "未登录或登录已过期"


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN
    http_status = 403
    default_message = "无权访问该资源"


class ItemNotFoundError(AppError):
    code = ErrorCode.ITEM_NOT_FOUND
    http_status = 404
    default_message = "物品记录不存在"


class ClaimNotFoundError(AppError):
    code = ErrorCode.CLAIM_NOT_FOUND
    http_status = 404
    default_message = "认领记录不存在"


class MatchNotFoundError(AppError):
    code = ErrorCode.MATCH_NOT_FOUND
    http_status = 404
    default_message = "匹配记录不存在，请先计算匹配"


class StateConflictError(AppError):
    code = ErrorCode.STATE_CONFLICT
    http_status = 409
    default_message = "当前状态不允许该操作"


class AlgorithmServiceError(AppError):
    code = ErrorCode.ALGORITHM_SERVICE_ERROR
    http_status = 502
    default_message = "算法服务暂不可用"


class StorageServiceError(AppError):
    code = ErrorCode.STORAGE_SERVICE_ERROR
    http_status = 502
    default_message = "图片存储服务暂不可用"


class AIServiceError(AppError):
    code = ErrorCode.AI_SERVICE_ERROR
    http_status = 502
    default_message = "AI 分析服务暂不可用"


class RateLimitedError(AppError):
    code = ErrorCode.RATE_LIMITED
    http_status = 429
    default_message = "操作过于频繁，请稍后重试"


class InternalError(AppError):
    code = ErrorCode.INTERNAL_ERROR
    http_status = 500
    default_message = "服务内部错误，请稍后重试或联系管理员"
