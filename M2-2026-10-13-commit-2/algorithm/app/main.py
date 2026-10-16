"""FastAPI 应用：暴露 3 个契约端点。

端点（契约不可更改）
--------------------
1. ``POST /algorithm/v1/match``                —— 失物/拾物候选匹配
2. ``POST /algorithm/v1/extract-attributes``   —— 图片 + 描述 → 结构化属性
3. ``POST /algorithm/v1/claim-credibility``    —— 认领可信度评估

降级设计（重要）
----------------
* **fastapi 只在本模块导入**。``app.matcher`` / ``app.extractor`` / ``app.credibility``
  / ``app.mock_data`` 等核心模块全部只用标准库，即使没有安装 fastapi 也能导入与单测。
* 没有 fastapi 时，``app`` 为 ``None``，并可通过 :func:`run_stdlib_server`
  用标准库 ``http.server`` 起一个行为等价的 HTTP 服务（本地联调 / 演示用）。
* 没有 uvicorn 时，``run()`` 会退回到标准库服务器。

启动方式::

    python -m app.main                     # 有 uvicorn 用 uvicorn，否则用标准库 http.server
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

# 允许 `python app/main.py` 直接运行（把 algorithm/ 加入 sys.path）
if __package__ in (None, ""):  # pragma: no cover - 直接执行脚本时
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import credibility as credibility_module  # type: ignore
    from app import extractor as extractor_module  # type: ignore
    from app import matcher as matcher_module  # type: ignore
    from app import schemas as schemas_module  # type: ignore
    from app import mock_data as mock_data_module  # type: ignore
    from app import config_loader  # type: ignore
else:
    from . import credibility as credibility_module
    from . import extractor as extractor_module
    from . import matcher as matcher_module
    from . import mock_data as mock_data_module
    from . import schemas as schemas_module
    from . import config_loader

# ---------------------------------------------------------------------------
# 单例：核心逻辑与 Web 框架完全解耦
# ---------------------------------------------------------------------------
CONFIG = config_loader.load_config()
EXTRACTOR = extractor_module.AttributeExtractor(CONFIG)
ENGINE = matcher_module.MatchEngine(CONFIG, extractor=EXTRACTOR)
SCORER = credibility_module.CredibilityScorer(CONFIG)

try:  # pragma: no cover - 取决于运行环境
    from fastapi import Body, FastAPI, Request  # type: ignore
    from fastapi.responses import JSONResponse  # type: ignore

    FASTAPI_AVAILABLE = True
except Exception:  # noqa: BLE001 - 没有 fastapi 也要能 import 本模块
    Body = None  # type: ignore
    FastAPI = None  # type: ignore
    Request = None  # type: ignore
    JSONResponse = None  # type: ignore
    FASTAPI_AVAILABLE = False


def _scores_for_response(outcome: matcher_module.MatchOutcome) -> Dict[str, float]:
    """内部维度名 → 契约字段名（image → image_score …）。"""
    return {
        "image_score": round(outcome.scores.get("image", 0.0), 4),
        "text_score": round(outcome.scores.get("text", 0.0), 4),
        "category_score": round(outcome.scores.get("category", 0.0), 4),
        "time_score": round(outcome.scores.get("time", 0.0), 4),
        "location_score": round(outcome.scores.get("location", 0.0), 4),
        "brand_model_score": round(outcome.scores.get("brand_model", 0.0), 4),
    }


# ---------------------------------------------------------------------------
# 三个端点的纯逻辑实现（不依赖任何 Web 框架，便于单测与复用）
# ---------------------------------------------------------------------------
def handle_match(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``POST /algorithm/v1/match`` 的业务实现。"""
    payload = payload if isinstance(payload, dict) else {}
    query_item = payload.get("query_item")
    candidate_items = payload.get("candidate_items") or []
    top_k = payload.get("top_k")
    if not isinstance(candidate_items, list):
        candidate_items = []
    result = ENGINE.match(query_item, candidate_items, top_k=top_k)
    # 维度名 → 契约字段名
    results: List[Dict[str, Any]] = []
    for outcome in result.get("results", []):
        raw_scores = outcome.get("scores") or {}
        results.append({
            "candidate_id": outcome.get("candidate_id"),
            "total_score": outcome.get("total_score", 0.0),
            "scores": {field: round(float(raw_scores.get(dim, 0.0)), 4)
                       for field, dim in schemas_module.SCORE_FIELD_MAP.items()},
            "evidence": list(outcome.get("evidence") or []),
            "uncertainties": list(outcome.get("uncertainties") or []),
        })
    response: Dict[str, Any] = {
        "model_version": result.get("model_version", CONFIG.model_version),
        "results": results,
    }
    if result.get("warnings"):
        response["warnings"] = list(result["warnings"])
    if CONFIG.warnings:
        response["config_warnings"] = list(CONFIG.warnings)
    return response


def handle_extract(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``POST /algorithm/v1/extract-attributes`` 的业务实现（永不对空输入报错）。"""
    payload = payload if isinstance(payload, dict) else {}
    attributes = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else None
    result = EXTRACTOR.extract(
        description=payload.get("description"),
        title=payload.get("title"),
        category=payload.get("category"),
        image_url=payload.get("image_url") or payload.get("image"),
        image_filename=payload.get("image_filename"),
        attributes=attributes,
    )
    response = result.to_dict()
    response["model_version"] = CONFIG.model_version
    if payload.get("item_id") is not None:
        response["item_id"] = payload.get("item_id")
    return response


def handle_claim_credibility(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``POST /algorithm/v1/claim-credibility`` 的业务实现。

    永远不会把标准答案回显到响应里（理由文本由模板生成）。
    """
    payload = payload if isinstance(payload, dict) else {}
    candidate_match = payload.get("candidate_match") or payload.get("match_result")
    answers = payload.get("answers")
    if answers is None:
        answers = payload.get("user_answers")
    result = SCORER.score(
        questions=payload.get("questions") or [],
        answers=answers or [],
        candidate_match=candidate_match if isinstance(candidate_match, dict) else None,
        target_item=payload.get("target_item") if isinstance(payload.get("target_item"), dict) else None,
        behavior_signals=payload.get("behavior_signals") if isinstance(payload.get("behavior_signals"), dict) else None,
    )
    response = result.to_dict()
    response["model_version"] = CONFIG.model_version
    return response


ROUTE_TABLE = {
    ("POST", "/algorithm/v1/match"): handle_match,
    ("POST", "/algorithm/v1/extract-attributes"): handle_extract,
    ("POST", "/algorithm/v1/claim-credibility"): handle_claim_credibility,
}


# ---------------------------------------------------------------------------
# FastAPI 应用（仅在安装了 fastapi 时创建）
# ---------------------------------------------------------------------------
def create_app() -> Any:
    """创建 FastAPI 实例；未安装 fastapi 时抛出带安装提示的 ImportError。"""
    if not FASTAPI_AVAILABLE:
        raise ImportError(
            "未检测到 fastapi。核心算法不依赖 fastapi，可直接调用 "
            "app.main.handle_match / handle_extract / handle_claim_credibility，"
            "或运行 `python -m app.main` 使用标准库 HTTP 服务；"
            "如需 FastAPI 版服务请执行 `pip install -r requirements.txt`。"
        )
    application = FastAPI(
        title="拾光 · 算法服务",
        version=CONFIG.model_version,
        description="校园多模态失物检索与可信认领平台 · 算法侧基线实现（可降级 Mock）",
    )

    @application.get("/algorithm/v1/health")
    def health() -> Dict[str, Any]:  # pragma: no cover - 简单探针
        return {
            "status": "ok",
            "model_version": CONFIG.model_version,
            "fastapi": FASTAPI_AVAILABLE,
            "image_backends": matcher_module.available_image_backends(),
            "config_sources": CONFIG.sources,
            "config_warnings": CONFIG.warnings,
        }

    @application.post("/algorithm/v1/match")
    def match_endpoint(payload: Dict[str, Any] = Body(default_factory=dict)) -> Any:
        return handle_match(payload)

    @application.post("/algorithm/v1/extract-attributes")
    def extract_endpoint(payload: Dict[str, Any] = Body(default_factory=dict)) -> Any:
        return handle_extract(payload)

    @application.post("/algorithm/v1/claim-credibility")
    def claim_endpoint(payload: Dict[str, Any] = Body(default_factory=dict)) -> Any:
        return handle_claim_credibility(payload)

    return application


app = create_app() if FASTAPI_AVAILABLE else None


# ---------------------------------------------------------------------------
# 标准库 HTTP 降级服务（无 fastapi 时使用，路由与返回结构与 FastAPI 版一致）
# ---------------------------------------------------------------------------
def run_stdlib_server(host: str = "127.0.0.1", port: int = 8000) -> int:  # pragma: no cover
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        server_version = "ShiguangAlgorithm/1.0"

        def _send(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
            if self.path.rstrip("/") in ("/algorithm/v1/health", ""):
                self._send(200, {
                    "status": "ok",
                    "model_version": CONFIG.model_version,
                    "fastapi": FASTAPI_AVAILABLE,
                    "server": "stdlib-http.server",
                })
            else:
                self._send(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 接口
            handler = ROUTE_TABLE.get(("POST", self.path.rstrip("/")))
            if handler is None:
                self._send(404, {"detail": f"未注册的端点: {self.path}"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send(400, {"detail": f"请求体不是合法 JSON：{exc}"})
                return
            try:
                self._send(200, handler(payload))
            except Exception as exc:  # noqa: BLE001 - 兜底成 500 而不是进程退出
                self._send(500, {"detail": f"内部错误：{exc.__class__.__name__}: {exc}"})

        def log_message(self, fmt: str, *args: Any) -> None:  # 保持控制台干净
            if os.environ.get("SHIGUANG_VERBOSE"):
                super().log_message(fmt, *args)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"[拾光算法] 标准库 HTTP 服务已启动: http://{host}:{port}")
    print("  端点: POST /algorithm/v1/match | /extract-attributes | /claim-credibility")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[拾光算法] 已停止")
    finally:
        server.server_close()
    return 0


def run(host: str = "127.0.0.1", port: int = 8000) -> int:  # pragma: no cover
    """优先用 uvicorn 启动 FastAPI；缺失则退回标准库服务器。"""
    if FASTAPI_AVAILABLE:
        try:
            import uvicorn  # type: ignore
        except ImportError:
            print("[拾光算法] 未安装 uvicorn，改用标准库 HTTP 服务")
        else:
            uvicorn.run(app, host=host, port=port)
            return 0
    else:
        print("[拾光算法] 未安装 fastapi，改用标准库 HTTP 服务（核心算法不受影响）")
    return run_stdlib_server(host, port)


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover
    argv = list(sys.argv[1:] if argv is None else argv)
    host = os.environ.get("SHIGUANG_HOST", "127.0.0.1")
    port = int(os.environ.get("SHIGUANG_PORT", "8000"))
    if "--host" in argv:
        host = argv[argv.index("--host") + 1]
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    return run(host, port)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
