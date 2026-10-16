"""算法服务客户端（API 契约 §4）。

**解耦红线**：后端不得依赖任何具体模型类名，只能通过本客户端走 HTTP 调用；
地址来自 `ALGORITHM_SERVICE_URL`。算法服务不可用时由调用方决定是否降级。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AlgorithmServiceError
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

MATCH_PATH = "/algorithm/v1/match"
EXTRACT_PATH = "/algorithm/v1/extract-attributes"
CREDIBILITY_PATH = "/algorithm/v1/claim-credibility"
# ⚠ 与 algorithm/app/main.py 暴露的路径保持一致（前缀为 /algorithm/v1/，不是 /algorithm/）
HEALTH_PATH = "/algorithm/v1/health"


class AlgorithmClient:
    """算法服务 HTTP 客户端。"""

    def __init__(self, base_url: str | None = None, timeout: int | None = None) -> None:
        self.base_url = (base_url or settings.ALGORITHM_SERVICE_URL).rstrip("/")
        self.timeout = timeout or settings.ALGORITHM_TIMEOUT

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Request-ID": get_request_id() or "req_internal",
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload, headers=self._headers())
        except httpx.TimeoutException as exc:
            logger.warning("算法服务超时：%s", path)
            raise AlgorithmServiceError("算法服务响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            logger.warning("算法服务不可达：%s（%s）", path, type(exc).__name__)
            raise AlgorithmServiceError("算法服务暂不可用") from exc

        if resp.status_code >= 500:
            logger.warning("算法服务返回 %s：%s", resp.status_code, path)
            raise AlgorithmServiceError("算法服务内部错误")
        if resp.status_code >= 400:
            # 4xx 通常是请求体不符合契约，属于集成缺陷，需要暴露出来
            logger.error("算法服务拒绝了请求（%s）：%s", resp.status_code, resp.text[:300])
            raise AlgorithmServiceError("算法服务拒绝了请求，请检查入参契约")
        try:
            return resp.json()
        except ValueError as exc:
            raise AlgorithmServiceError("算法服务返回了非 JSON 内容") from exc

    # ------------------------------------------------------------------
    # 三个业务接口
    # ------------------------------------------------------------------
    def match(self, query_item: dict[str, Any], candidate_items: list[dict[str, Any]]) -> dict[str, Any]:
        """调用匹配接口，返回 `{model_version, results: [...]}`。"""
        return self._post(MATCH_PATH, {"query_item": query_item, "candidate_items": candidate_items})

    def extract_attributes(
        self, *, image_url: str | None, description: str, category: str | None = None
    ) -> dict[str, Any]:
        """属性提取。返回结构见 API 契约 §4.3。"""
        return self._post(
            EXTRACT_PATH,
            {"image_url": image_url, "description": description, "category": category},
        )

    def claim_credibility(
        self,
        *,
        match_result: dict[str, Any],
        questions: list[dict[str, Any]],
        answer_summary: list[dict[str, Any]],
        completeness: float = 0.7,
        risk_rule_score: float = 0.8,
    ) -> dict[str, Any]:
        """可信度评估。返回结构见 API 契约 §3.9。"""
        return self._post(
            CREDIBILITY_PATH,
            {
                "match_result": match_result,
                "questions": questions,
                "answer_summary": answer_summary,
                "completeness": completeness,
                "risk_rule_score": risk_rule_score,
            },
        )

    # ------------------------------------------------------------------
    def check_health(self) -> dict[str, Any]:
        """健康检查：算法服务连通性。失败不抛异常，返回 down 状态。

        ⚠ 路径为 `/algorithm/v1/health`（与 algorithm/app/main.py 一致）。
        该端点返回 `model_version` / `image_backends` / `config_warnings`，
        其中 `config_warnings` 非空表示权重等配置回退到了内置默认值，需要关注。
        """
        url = f"{self.base_url}{HEALTH_PATH}"
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(url)
            if resp.status_code >= 300:
                return {"status": "down", "http_status": resp.status_code, "url_masked": _mask_url(self.base_url)}
            body = resp.json()
            result: dict[str, Any] = {
                "status": "ok",
                "model_version": body.get("model_version", "unknown"),
                "url_masked": _mask_url(self.base_url),
            }
            warnings = body.get("config_warnings") or []
            if warnings:
                # 配置回退到内置默认值：可用但需排查，标记为 degraded
                result["status"] = "degraded"
                result["config_warning_count"] = len(warnings)
            return result
        except Exception as exc:  # noqa: BLE001
            return {"status": "down", "error": type(exc).__name__, "url_masked": _mask_url(self.base_url)}


def _mask_url(url: str) -> str:
    """脱敏服务地址（隐藏内网 IP 细节）。"""
    try:
        scheme, rest = url.split("://", 1)
        host = rest.split("/", 1)[0]
        return f"{scheme}://{host}"
    except Exception:  # noqa: BLE001
        return "***"


_client: AlgorithmClient | None = None


def get_algorithm_client() -> AlgorithmClient:
    global _client
    if _client is None:
        _client = AlgorithmClient()
    return _client


def reset_algorithm_client() -> None:
    """测试用。"""
    global _client
    _client = None
