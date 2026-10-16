"""MaaS / 大模型服务客户端（需求 §3.3）。

红线：
- API Key **只从环境变量读取**，绝不入库、绝不写日志、绝不出现在响应里；
- 超时默认 30 秒，超时/失败必须**允许降级**（返回 None），由调用方决定业务行为；
- **不得**把模型调用写死在业务路由中——所有调用都经由本客户端。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import settings
from app.core.errors import AIServiceError
from app.core.request_id import get_request_id

logger = logging.getLogger(__name__)

# 提示词版本：变更提示词时必须同步升级版本号，并写入 items.ai_prompt_version
PROMPT_VERSION_ATTRIBUTES = "v1.2"
PROMPT_VERSION_QUESTIONS = "v1.0"


class MaasClient:
    """MaaS 客户端。`MAAS_ENABLED=false` 时所有方法返回 None（调用方走规则降级）。"""

    def __init__(self) -> None:
        self.enabled = bool(settings.MAAS_ENABLED and settings.MAAS_ENDPOINT and settings.MAAS_API_KEY)
        self.endpoint = settings.MAAS_ENDPOINT.rstrip("/") if settings.MAAS_ENDPOINT else ""
        self.model = settings.MAAS_MODEL
        self.timeout = settings.MAAS_TIMEOUT

    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.MAAS_API_KEY}",
            "X-Request-ID": get_request_id() or "req_internal",
        }

    def _chat(self, system_prompt: str, user_prompt: str, *, max_tokens: int = 1024) -> str | None:
        """调用对话补全，返回文本内容。失败返回 None（**不抛异常**，由调用方降级）。"""
        if not self.enabled:
            logger.info("MaaS 未启用，跳过模型调用（将使用规则降级）")
            return None

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,   # 低温度提高可复现性（答辩要求数值稳定）
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.endpoint}/v1/chat/completions", json=payload, headers=self._headers()
                )
            if resp.status_code >= 400:
                logger.warning("MaaS 返回 %s（将降级）", resp.status_code)
                return None
            body = resp.json()
            return body["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 - AI 失败绝不能让发布流程失败
            logger.warning("MaaS 调用失败（将降级）：%s", type(exc).__name__)
            return None

    # ------------------------------------------------------------------
    def extract_attributes(self, *, description: str, category: str | None, image_url: str | None) -> dict[str, Any] | None:
        """属性提取。返回结构需经 JSON Schema 校验；模型未启用/失败返回 None。"""
        system_prompt = (
            "你是校园失物招领平台的物品信息提取助手。"
            "请从用户描述中提取结构化属性，只输出 JSON，不要输出解释文字。\n"
            "JSON 字段：category, brand, model, color, material, visible_features[], "
            "accessories[], keywords[], uncertainties[]。\n"
            "要求：\n"
            "1. 无法确定的字段填 null，不要编造；\n"
            "2. 只能描述可见/可推断的特征，不得表述为确定事实；\n"
            "3. 不确定的地方写入 uncertainties；\n"
            "4. 不得提取或推测任何个人身份信息。"
        )
        user_prompt = json.dumps(
            {
                "category_hint": category,
                "has_image": bool(image_url),
                "description": description[:2000],
            },
            ensure_ascii=False,
        )
        raw = self._chat(system_prompt, user_prompt, max_tokens=800)
        if raw is None:
            return None
        return _parse_json_safely(raw)

    def generate_verification_questions(
        self, *, hidden_features: list[Any], category: str, count: int = 3
    ) -> list[dict[str, Any]] | None:
        """生成隐藏特征验证问题。

        ⚠ 生成结果**必须经过程序校验**（见 `app/services/claim_service.py::validate_questions`），
        校验不通过则回退到模板问题库。
        ⚠ 提示词中明确要求「不得在问题中泄露答案」。
        """
        system_prompt = (
            "你是失物认领验证的问题设计助手。请根据物品的隐藏特征，生成用于验证认领人身份的提问。\n"
            "只输出 JSON 数组，每项形如 {\"question_id\":\"q1\",\"question_text\":\"...\",\"feature_ref\":0}。\n"
            "严格要求：\n"
            "1. 问题必须是**开放式提问**，不得包含答案本身或答案的任何片段；\n"
            "2. 不得出现「是不是……」「有没有……（是/否）」这类可猜的问题；\n"
            "3. 不得在问题中复述隐藏特征的具体内容；\n"
            "4. 每问对应一个不同的隐藏特征；\n"
            "5. 不得询问任何个人身份信息。"
        )
        user_prompt = json.dumps(
            {
                "category": category,
                "hidden_features": [str(f) for f in hidden_features][:5],
                "question_count": count,
            },
            ensure_ascii=False,
        )
        raw = self._chat(system_prompt, user_prompt, max_tokens=600)
        if raw is None:
            return None
        parsed = _parse_json_safely(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("questions")
        if not isinstance(parsed, list):
            return None
        return [item for item in parsed if isinstance(item, dict)]

    def explain_match(
        self, *, query_summary: str, candidate_summary: str, scores: dict[str, float]
    ) -> list[str] | None:
        """生成匹配解释文案（可选能力；失败则使用规则模板生成的 evidence）。"""
        system_prompt = (
            "你是失物匹配结果的解释助手。请用简洁中文说明两条记录为何可能相关。"
            "只输出 JSON 字符串数组，不要输出其他内容。"
            "禁止使用「确认」「就是」「所有权」等确定性措辞。"
        )
        user_prompt = json.dumps(
            {"query": query_summary, "candidate": candidate_summary, "scores": scores},
            ensure_ascii=False,
        )
        raw = self._chat(system_prompt, user_prompt, max_tokens=400)
        if raw is None:
            return None
        parsed = _parse_json_safely(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed][:6]
        return None

    # ------------------------------------------------------------------
    def check_health(self) -> dict[str, Any]:
        """只报告配置状态，**不发真实请求**，避免健康检查消耗额度。"""
        return {
            "status": "ok" if self.enabled else "not_configured",
            "configured": self.enabled,
            "model": self.model if self.enabled else None,
            "prompt_version": PROMPT_VERSION_ATTRIBUTES,
        }


def _parse_json_safely(raw: str) -> Any:
    """从模型输出中提取 JSON（容忍 ```json 代码块包裹与前后缀文本）。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 尝试截取第一个 { 或 [ 到最后一个 } 或 ]
        for start_ch, end_ch in (("{", "}"), ("[", "]")):
            start, end = text.find(start_ch), text.rfind(end_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    logger.warning("模型返回内容无法解析为 JSON（已降级）")
    return None


_maas_client: MaasClient | None = None


def get_maas_client() -> MaasClient:
    global _maas_client
    if _maas_client is None:
        _maas_client = MaasClient()
    return _maas_client


def reset_maas_client() -> None:
    """测试用。"""
    global _maas_client
    _maas_client = None


__all__ = [
    "MaasClient",
    "get_maas_client",
    "reset_maas_client",
    "AIServiceError",
    "PROMPT_VERSION_ATTRIBUTES",
    "PROMPT_VERSION_QUESTIONS",
]
