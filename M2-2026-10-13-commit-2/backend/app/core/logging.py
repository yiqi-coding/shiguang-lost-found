"""日志配置与脱敏过滤器。

红线（需求 §10）：
- 日志**不得**记录完整隐藏答案、密码、Secret、Authorization 头、OBS/DB 连接串；
- 通过 `SensitiveFilter` 在输出前统一正则脱敏，避免漏改某处 logger 造成泄露。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.core.config import settings
from app.core.request_id import get_request_id

# 需要脱敏的键名（大小写不敏感）
SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "passwd",
    "secret",
    "secret_key",
    "app_secret_key",
    "access_key",
    "obs_secret_key",
    "maas_api_key",
    "api_key",
    "authorization",
    "token",
    "access_token",
    "refresh_token",
    "hidden_features",
    "answers",
    "answer",
    "answers_plain",
    "standard_answer",
    "standard_answers",
    "credential",
}

# 常见敏感值的形态（连接串、Bearer、长十六进制密钥）
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*"), r"\1 ***"),
    (re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?key)=([^&\s\"']+)"), r"\1=***"),
    (re.compile(r"(?i)(mysql|redis|postgresql)(\+pymysql)?://([^:]+):([^@]+)@"), r"\1\2://\3:***@"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "***HEX_SECRET***"),
]

REDACTED = "***REDACTED***"


class SensitiveFilter(logging.Filter):
    """在日志落盘前统一脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, dict):
                record.msg = _redact_mapping(record.msg)
            elif isinstance(record.msg, str):
                record.msg = _redact_text(record.msg)

            if record.args:
                if isinstance(record.args, dict):
                    record.args = _redact_mapping(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        _redact_text(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:  # noqa: BLE001 - 日志脱敏失败绝不能影响业务
            record.msg = "[log redaction failed]"
            record.args = ()
        return True


def _redact_mapping(data: dict[Any, Any]) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key, value in data.items():
        if str(key).lower() in SENSITIVE_KEYS:
            result[key] = REDACTED
        elif isinstance(value, dict):
            result[key] = _redact_mapping(value)
        elif isinstance(value, str):
            result[key] = _redact_text(value)
        else:
            result[key] = value
    return result


def _redact_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RequestIDFilter(logging.Filter):
    """为每条日志注入 request_id 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """结构化 JSON 日志，便于云端日志服务采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for extra_key in ("user_id", "path", "status", "duration_ms", "target_type", "target_id"):
            if hasattr(record, extra_key):
                payload[extra_key] = getattr(record, extra_key)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    """初始化根日志配置。应用启动时调用一次。"""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveFilter())
    handler.addFilter(RequestIDFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL)

    # 降低第三方库噪音
    for noisy in ("uvicorn.access", "sqlalchemy.engine", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
