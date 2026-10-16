#!/usr/bin/env python
"""带重试的 wheel 下载器（规避本机网络对 pip 单次长连接不稳定的问题）。

用法：
    python scripts/fetch_wheels.py --dest .wheels -r backend/requirements.txt
    python scripts/fetch_wheels.py --dest .wheels -r algorithm/requirements-service.txt

设计：
    * 用标准库 urllib 直连 pypi.org JSON API（分页小请求，避免一次性拉全量索引）；
    * 每个文件独立下载，失败重试 N 次，成功一个写一个；
    * 只挑 py3-none-any 与 cp311 win_amd64 的 wheel（本机 Python 3.11 x64）；
    * 递归解析 METADATA 的 Requires-Dist，补齐传递依赖；
    * 最终把下载结果写进 wheels/FETCH_REPORT.json，便于核对。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_API = "https://pypi.org/pypi/{name}/{version}/json"
PY_TAG = "cp311"
PLATFORM_TAG = "win_amd64"

# 默认不下载的平台专用依赖（--package 显式指定时仍会下载）
SKIP_NAMES = {
    "uvloop", "httptools", "watchfiles", "websockets",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def http_get_json(url: str, retries: int = 6) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "shiguang-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2 ** attempt, 20)
            print(f"    重试 {attempt + 1}/{retries}（{type(exc).__name__}），等待 {wait}s …", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"无法获取 {url}：{last}")


def http_download(url: str, dest: Path, retries: int = 6) -> None:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "shiguang-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            dest.write_bytes(data)
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = min(2 ** attempt, 20)
            print(f"    下载重试 {attempt + 1}/{retries}（{type(exc).__name__}），等待 {wait}s …", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"无法下载 {url}：{last}")


def pick_file(files: list[dict[str, Any]], want_version: str) -> str | None:
    """挑选本机可用的 wheel。

    优先级（cp311 二进制 > cp3x abi3 稳定 ABI > py3-none-any 纯 Python）：
      1. cp311-cp311-win_amd64        （本机精确匹配）
      2. cp39-abi3-win_amd64 / cp37-abi3-win_amd64（abi3 稳定 ABI，向前兼容 3.11）
      3. py3-none-any                 （纯 Python）
    """
    candidates = [f for f in files if f["filename"].endswith(".whl")]
    preferred_tags = (
        f"{PY_TAG}-{PY_TAG}-{PLATFORM_TAG}",
        "cp39-abi3-win_amd64",
        "cp37-abi3-win_amd64",
        "py3-none-any",
    )
    for tag in preferred_tags:
        for f in candidates:
            if tag in f["filename"]:
                return f["url"]
    return None


def parse_requirement(line: str) -> tuple[str, str] | None:
    """解析 requirements 行 → (规范名, 版本约束)。跳过注释/选项行。"""
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith("-"):
        return None
    # 去掉环境标记
    if ";" in line:
        line = line.split(";", 1)[0].strip()
    m = re.match(r"^([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*(.*)$", line)
    if not m:
        return None
    return normalize(m.group(1)), (m.group(2).strip() or "")


def version_satisfies(version: str, constraint: str) -> bool:
    """极简版本约束判断：支持 ==、>=、<=、>、<、~= 与逗号组合，以及 ==1.* 这类前缀通配。"""
    if not constraint:
        return True

    # ==1.* / 1.* 形式：只比较主版本号
    wildcard = re.match(r"^(==)?\s*(\d+)\.\*$", constraint.strip())
    if wildcard:
        major = int(wildcard.group(2))
        return int(re.findall(r"\d+", version)[0]) == major

    def parse(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r"\d+", v)[:3])

    for part in constraint.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(==|>=|<=|~=|>|<)\s*(.+)$", part)
        if not m:
            continue
        op, want = m.group(1), m.group(2).strip()
        a, b = parse(version), parse(want)
        if op == "==" and a != b:
            return False
        if op == ">=" and a < b:
            return False
        if op == "<=" and a > b:
            return False
        if op == ">" and a <= b:
            return False
        if op == "<" and a >= b:
            return False
        if op == "~=" and a < b:
            return False
    return True


def requires_from_metadata(wheel: Path) -> list[str]:
    """读取 wheel 内 METADATA 的 Requires-Dist。"""
    reqs: list[str] = []
    try:
        with zipfile.ZipFile(wheel) as zf:
            meta_name = next(
                (n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None
            )
            if not meta_name:
                return reqs
            text = zf.read(meta_name).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return reqs
    for line in text.splitlines():
        if line.startswith("Requires-Dist:"):
            value = line.split(":", 1)[1].strip()
            if "extra ==" in value:
                continue
            reqs.append(value)
    return reqs


def main() -> int:
    parser = argparse.ArgumentParser(description="带重试的 wheel 下载器")
    parser.add_argument("--dest", default=".wheels", help="wheel 输出目录")
    parser.add_argument("-r", "--requirements", action="append", help="requirements 文件")
    parser.add_argument("-p", "--package", action="append",
                        help="直接指定包（格式 name 或 name==version），可重复")
    parser.add_argument("--max-depth", type=int, default=6)
    args = parser.parse_args()

    if not args.requirements and not args.package:
        parser.error("必须指定 -r/--requirements 或 -p/--package")

    dest = Path(args.dest)
    if not dest.is_absolute():
        dest = REPO_ROOT / dest
    dest.mkdir(parents=True, exist_ok=True)

    # 1) 收集顶层需求
    queue: list[tuple[str, str]] = []
    explicit: set[str] = set()
    for req_file in (args.requirements or []):
        path = Path(req_file)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_file():
            print(f"✗ requirements 不存在：{path}", flush=True)
            return 2
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = parse_requirement(line)
            if parsed:
                queue.append(parsed)

    for spec in (args.package or []):
        parsed = parse_requirement(spec)
        if parsed:
            queue.append(parsed)
            explicit.add(parsed[0])

    # 显式用 --package 指定的包，即使是"默认跳过"的也要下载
    global SKIP_NAMES
    SKIP_NAMES = SKIP_NAMES - explicit

    resolved: dict[str, str] = {}
    report: list[dict[str, Any]] = []
    depth = 0

    while queue and depth < args.max_depth:
        next_queue: list[tuple[str, str]] = []
        progress = False
        for name, constraint in queue:
            if name in resolved or name in SKIP_NAMES:
                continue
            resolved[name] = constraint
            progress = True
            print(f"→ {name} {constraint or '(latest)'}", flush=True)
            try:
                data = http_get_json(JSON_API.format(name=name, version=""))
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ 查询失败：{exc}", flush=True)
                report.append({"name": name, "constraint": constraint, "status": "query_failed", "error": str(exc)})
                continue

            version = data["info"]["version"]
            if not version_satisfies(version, constraint):
                print(f"  ! 最新版 {version} 不满足 {constraint}，尝试其它版本", flush=True)
                # 简化处理：找满足约束的最新版本
                ok_version = None
                for v in sorted(data["releases"].keys(), reverse=True):
                    if version_satisfies(v, constraint):
                        ok_version = v
                        break
                if not ok_version:
                    report.append({"name": name, "status": "no_matching_version"})
                    continue
                version = ok_version
                data = http_get_json(JSON_API.format(name=name, version=version))

            url = pick_file(data["urls"], version)
            if not url:
                report.append({"name": name, "version": version, "status": "no_wheel"})
                print(f"  ✗ 没有可用的 wheel（{version}）", flush=True)
                continue

            wheel_path = dest / url.rsplit("/", 1)[-1]
            if wheel_path.is_file() and wheel_path.stat().st_size > 0:
                print(f"  = 已存在 {wheel_path.name}", flush=True)
            else:
                print(f"  ↓ 下载 {wheel_path.name}", flush=True)
                try:
                    http_download(url, wheel_path)
                except Exception as exc:  # noqa: BLE001
                    report.append({"name": name, "status": "download_failed", "error": str(exc)})
                    continue

            report.append({
                "name": name, "version": version, "status": "ok",
                "file": wheel_path.name, "size": wheel_path.stat().st_size,
            })

            for req in requires_from_metadata(wheel_path):
                parsed = parse_requirement(req)
                if parsed and parsed[0] not in SKIP_NAMES:
                    next_queue.append(parsed)

        queue = next_queue
        depth += 1
        if not progress:
            break

    (dest / "FETCH_REPORT.json").write_text(
        json.dumps({"fetched": report}, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ok = sum(1 for r in report if r.get("status") == "ok")
    bad = [r for r in report if r.get("status") not in (None, "ok")]
    print("=" * 60, flush=True)
    print(f"完成：成功 {ok} 个，问题 {len(bad)} 个", flush=True)
    for r in bad:
        print(f"  ✗ {r.get('name')}: {r.get('status')} {r.get('error', '')}", flush=True)
    print(f"报告：{dest / 'FETCH_REPORT.json'}", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
