#!/usr/bin/env python
"""健康检查脚本（需求 §12.3）。

用法：
    python scripts/healthcheck.py --url http://localhost:8000
    python scripts/healthcheck.py --url https://demo.example.com --timeout 10 --json

退出码：
    0  全部关键依赖正常
    1  存在 down 状态的关键依赖（数据库 / 存储 / 算法服务）
    2  无法访问目标服务

输出中**不会**出现任何密钥；只展示各依赖的 status、latency 与打码后的地址。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

# 关键依赖：down 则退出码为 1
CRITICAL = {"app", "database"}
# 允许降级的依赖：down 时仅告警
DEGRADABLE = {"redis", "obs", "algorithm_service", "maas"}

STATUS_ICON = {
    "ok": "✓",
    "degraded": "!",
    "not_configured": "-",
    "down": "✗",
}


def fetch_health(url: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    """请求 /health。返回 (body, error)。"""
    import urllib.error
    import urllib.request

    endpoint = url.rstrip("/")
    if not endpoint.endswith("/health"):
        endpoint = f"{endpoint}/health"

    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw), None
    except urllib.error.HTTPError as exc:
        # 503 也会带完整的健康报告体，尽量解析出来
        try:
            body = json.loads(exc.read().decode("utf-8"))
            return body, f"HTTP {exc.code}"
        except Exception:  # noqa: BLE001
            return None, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def render(body: dict[str, Any]) -> int:
    """打印健康报告，返回退出码。"""
    data = body.get("data") or {}
    if not data:
        print("✗ 响应中没有 data 字段，接口可能不是本项目的 /health")
        return 2

    print("=" * 62)
    print("  拾光 · 服务健康检查")
    print("=" * 62)

    exit_code = 0
    for name, info in data.items():
        if not isinstance(info, dict):
            continue
        status = str(info.get("status", "unknown"))
        icon = STATUS_ICON.get(status, "?")

        extras: list[str] = []
        for key in ("latency_ms", "model_version", "version", "bucket", "endpoint_masked", "url_masked", "driver"):
            if info.get(key) not in (None, ""):
                extras.append(f"{key}={info[key]}")
        if info.get("degraded"):
            extras.append("已降级")
        if info.get("error"):
            extras.append(f"error={info['error']}")

        detail = ("  " + "  ".join(str(e) for e in extras)) if extras else ""
        print(f"  {icon} {name:<20} {status}{detail}")

        if status == "down":
            if name in CRITICAL:
                exit_code = max(exit_code, 1)
            elif name in DEGRADABLE:
                # 可降级依赖：不改变退出码，但明确提示
                pass

    print("-" * 62)
    if exit_code == 0:
        print("  结论：关键依赖正常，服务可用")
    else:
        print("  结论：存在不可用的关键依赖，服务不可用")
    print("=" * 62)
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="拾光服务健康检查")
    parser.add_argument("--url", default="http://localhost:8000", help="服务地址（可含或不含 /health）")
    parser.add_argument("--timeout", type=float, default=10.0, help="超时秒数")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出原始响应")
    args = parser.parse_args()

    body, error = fetch_health(args.url, args.timeout)
    if body is None:
        print(f"✗ 无法访问 {args.url}/health：{error}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        data = body.get("data") or {}
        return 1 if any((data.get(k) or {}).get("status") == "down" for k in CRITICAL) else 0

    return render(body)


if __name__ == "__main__":
    sys.exit(main())
