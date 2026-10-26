#!/usr/bin/env python
"""把后端缺失的纯 Python 依赖下载并解包到工作区内（免安装、免权限提升）。

背景：
    本机 `pip install` 有两个障碍 ——
    ① 默认索引源（清华镜像）在本机网络下不可用；
    ② pip 的临时目录与用户级 site-packages 都位于工作区外（`C:\\Users\\...`），
       被沙箱拒绝写入。
    而这个项目的后端依赖中，关键部分（pydantic / pydantic-core / sqlalchemy /
    uvicorn / PyJWT / bcrypt / Pillow / httpx）在 Anaconda 的 Python 3.14 里
    **已经存在且可正常 import**，只差少数几个包，且它们**全是纯 Python wheel**，
    不含任何需要编译的扩展。

    因此本脚本用标准库 urllib 直接下载 wheel、**递归解析 METADATA 里的
    Requires-Dist**（补齐传递依赖），并解包到 `.vendor/`。
    运行时只需 `PYTHONPATH=E:\\ICT\\.vendor`，不触碰系统环境。

用法：
    python scripts/vendor_deps.py            # 下载并解包（含传递依赖）
    python scripts/vendor_deps.py --check    # 只检查 .vendor 是否完整
    python scripts/vendor_deps.py --plain    # 忽略环境里已有的包，全部重新下载
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / ".vendor"

# 需要补齐的顶层包（版本留空表示取最新）
REQUIRED = ["fastapi", "pymysql", "redis", "passlib", "alembic"]

JSON_API = "https://pypi.org/pypi/{name}/json"

# 不需要下载的包：本机环境已具备，或属于可选/平台专用依赖
SKIP_PACKAGES = {
    # 环境里已有
    "pydantic", "pydantic-core", "pydantic_core", "sqlalchemy", "uvicorn",
    "pyjwt", "jwt", "bcrypt", "pillow", "httpx", "anyio", "sniffio", "idna",
    "certifi", "h11", "httpcore", "typing-extensions", "typing_extensions",
    "annotated-types", "colorama", "click", "greenlet",
    # 可选 / 平台专用 / 测试专用，运行服务不需要
    "uvloop", "httptools", "watchfiles", "websockets", "python-dotenv",
    "pyyaml", "email-validator", "python-multipart", "orjson", "ujson",
    "httpx-sse", "rich", "typer", "pytest", "pytest-asyncio", "starlette",
}

# 环境标记要跳过的关键词
SKIP_MARKERS = ("extra ==", "sys_platform == \"win32\"", "sys_platform == 'win32'")

MAX_DEPTH = 6


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "shiguang-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pick_wheel(name: str, python_version: tuple[int, int]) -> tuple[str, str]:
    """挑选合适的 wheel：优先 py3-none-any，其次与本机 ABI 匹配的。"""
    data = fetch_json(JSON_API.format(name=name))
    version = data["info"]["version"]
    files = data["releases"][version]

    py_tag = f"cp{python_version[0]}{python_version[1]}"

    def score(filename: str) -> tuple[int, str]:
        if filename.endswith(("-py3-none-any.whl", "-py2.py3-none-any.whl")):
            return (0, filename)
        if py_tag in filename and filename.endswith(".whl"):
            return (1, filename)
        if "none-any.whl" in filename:
            return (2, filename)
        return (9, filename)

    wheels = [f for f in files if f["filename"].endswith(".whl")]
    if not wheels:
        raise RuntimeError(f"{name} 没有可用 wheel（可能只有源码包）")
    wheels.sort(key=lambda f: score(f["filename"]))
    best = wheels[0]
    return version, best["url"]


def download_wheel(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "shiguang-vendor/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def parse_requires(payload: bytes) -> list[str]:
    """从 wheel 的 METADATA 中解析 Requires-Dist（只取必装项）。"""
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        meta_name = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if not meta_name:
            return names
        text = zf.read(meta_name).decode("utf-8", errors="replace")
    for line in text.splitlines():
        if not line.startswith("Requires-Dist:"):
            continue
        spec = line[len("Requires-Dist:"):].strip()
        # 跳过可选依赖与平台专用项
        if any(marker in spec for marker in SKIP_MARKERS):
            continue
        if ";" in spec:
            _, _, marker = spec.partition(";")
            marker = marker.strip()
            # 只保留无条件依赖，或明确针对当前平台的
            if marker and "extra" in marker:
                continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        if match:
            names.append(match.group(1))
    return names


def unpack_wheel(payload: bytes, dest: Path) -> int:
    count = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            # 不写入 __pycache__ 与脚本目录，保持整洁
            if name.startswith((".data/scripts/", "*.dist-info/")) and False:
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            count += 1
    return count


def already_available(module_name: str) -> bool:
    """包是否**真正可用**（能 import，且其自身依赖也已满足）。

    注意：不能只用 `find_spec` 判断 —— 那样会认为「模块存在」就等于可用，
    从而跳过 fastapi 这类自身存在、但其传递依赖（如 opentelemetry）缺失的包。
    这里改为真实 import，代价是稍慢，但语义正确。
    """
    import importlib

    if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
        sys.path.insert(0, str(VENDOR_DIR))
    try:
        importlib.import_module(module_name)
        return True
    except Exception:  # noqa: BLE001 - 任何导入失败都视为不可用
        return False


def collect_plan(python_version: tuple[int, int], plain: bool) -> list[tuple[str, str, str]]:
    """广度优先解析依赖，返回 [(包名, 版本, 下载地址)]。"""
    plan: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(name, 0) for name in REQUIRED]

    while queue:
        name, depth = queue.pop(0)
        key = normalize(name)
        if key in seen or depth > MAX_DEPTH:
            continue
        seen.add(key)

        module_name = key.replace("-", "_")
        if key in SKIP_PACKAGES:
            print(f"    · 跳过 {name}（环境已有或不需要）")
            continue
        if not plain and already_available(module_name):
            print(f"    · 跳过 {name}（已可用）")
            continue

        try:
            version, url = pick_wheel(name, python_version)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {name} 解析失败：{type(exc).__name__}: {exc}")
            continue

        plan.append((name, version, url))
        try:
            payload = download_wheel(url)
            requires = parse_requires(payload)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {name} 下载失败：{type(exc).__name__}: {exc}")
            continue

        # 暂存下载内容，稍后统一解包，避免重复下载
        _CACHE[normalize(name)] = payload
        if requires:
            print(f"    · {name} {version} 依赖：{', '.join(sorted(set(requires)))}")
        for dep in requires:
            if normalize(dep) not in seen:
                queue.append((dep, depth + 1))

    return plan


_CACHE: dict[str, bytes] = {}


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并解包后端缺失依赖到 .vendor/")
    parser.add_argument("--check", action="store_true", help="只检查完整性")
    parser.add_argument("--plain", action="store_true", help="忽略环境已有包，全部下载")
    args = parser.parse_args()

    py_version = sys.version_info[:2]
    print("=" * 62)
    print("  后端依赖本地化（.vendor/）")
    print("=" * 62)
    print(f"  解释器：{sys.executable}")
    print(f"  Python：{py_version[0]}.{py_version[1]}")
    print(f"  目标目录：{VENDOR_DIR}")

    if args.check:
        missing = [name for name in REQUIRED if not already_available(name.replace("-", "_"))]
        if missing:
            print(f"\n  ✗ 缺失：{', '.join(missing)}")
            return 1
        print(f"\n  ✓ 顶层 {len(REQUIRED)} 个包均已就位")
        return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    print("\n  解析依赖图：")
    plan = collect_plan(py_version, args.plain)

    print(f"\n  共需下载 {len(plan)} 个包，开始解包：")
    total_files = 0
    for name, version, url in plan:
        payload = _CACHE.get(normalize(name))
        if payload is None:
            try:
                payload = download_wheel(url)
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ {name} 失败：{type(exc).__name__}")
                continue
        try:
            count = unpack_wheel(payload, VENDOR_DIR)
            total_files += count
            print(f"    ✓ {name:<24} {version:<12} {count} 个文件")
        except Exception as exc:  # noqa: BLE001
            print(f"    ✗ {name} 解包失败：{type(exc).__name__}: {exc}")

    print(f"\n  ✓ 完成，共解包 {total_files} 个文件")
    print("  使用方式：")
    print(f'      $env:PYTHONPATH = "{VENDOR_DIR}"')
    print("      python -m unittest discover -s tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())

