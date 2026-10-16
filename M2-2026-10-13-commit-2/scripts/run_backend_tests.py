#!/usr/bin/env python
"""一键运行后端测试（自动处理依赖路径）。

为什么需要这个脚本：
    本机 `pip install` 受网络与沙箱限制（见 docs/troubleshooting.md），
    后端依赖改为解包到 `.vendor/` 后由 `PYTHONPATH` 引用；
    而 Anaconda 的 Python 3.14 已自带 pydantic / sqlalchemy / uvicorn 等，
    因此运行测试需要同时指定「解释器 + PYTHONPATH」，手工拼很容易出错。

用法：
    python scripts/run_backend_tests.py                 # 运行全部测试
    python scripts/run_backend_tests.py -v              # 详细输出
    python scripts/run_backend_tests.py -k test_scoring # 只跑匹配的用例
    python scripts/run_backend_tests.py --interpreter "C:\\Python311\\python.exe"

退出码与 unittest 一致（0 = 全部通过）。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
VENDOR_DIR = REPO_ROOT / ".vendor"

# 候选解释器：优先能同时满足「有 pydantic/sqlalchemy」与「可使用 .vendor」的
CANDIDATE_INTERPRETERS = [
    Path(r"F:\anacoda\python.exe"),
    Path(sys.executable),
    Path(r"F:\python311\python.exe"),
]


def pick_interpreter(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"✗ 指定的解释器不存在：{path}")
        return path

    for candidate in CANDIDATE_INTERPRETERS:
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "✗ 未找到可用的 Python 解释器。请用 --interpreter 指定，例如：\n"
        '    python scripts/run_backend_tests.py --interpreter "C:\\Python311\\python.exe"'
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行后端测试（自动设置依赖路径）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出（unittest -v）")
    parser.add_argument("-k", "--pattern", default=None, help="只运行匹配的测试（unittest -k）")
    parser.add_argument("--interpreter", default=None, help="指定 Python 解释器路径")
    parser.add_argument("--no-vendor", action="store_true", help="不使用 .vendor（依赖已装在解释器里时）")
    args = parser.parse_args()

    interpreter = pick_interpreter(args.interpreter)
    print("=" * 66)
    print("  运行后端测试")
    print("=" * 66)
    print(f"  解释器：{interpreter}")

    if not BACKEND_DIR.is_dir():
        raise SystemExit(f"✗ 未找到后端目录：{BACKEND_DIR}")

    # ---- 组装 PYTHONPATH ----
    paths: list[str] = []
    if not args.no_vendor:
        if VENDOR_DIR.is_dir():
            paths.append(str(VENDOR_DIR))
            print(f"  依赖目录：{VENDOR_DIR}（{len(list(VENDOR_DIR.rglob('*.py')))} 个 .py）")
        else:
            print("  ! 未找到 .vendor —— 若测试报缺少依赖，请先运行：")
            print("      python scripts/vendor_deps.py")
    paths.append(str(BACKEND_DIR))

    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(paths + ([existing] if existing else []))

    # ---- 组装命令 ----
    command = [str(interpreter), "-X", "utf8", "-m", "unittest", "discover", "-s", "tests"]
    if args.verbose:
        command.append("-v")
    if args.pattern:
        command += ["-k", args.pattern]

    print(f"  工作目录：{BACKEND_DIR}")
    print("-" * 66)
    result = subprocess.run(command, cwd=str(BACKEND_DIR), env=env)
    print("-" * 66)
    print("  结果：" + ("全部通过 ✓" if result.returncode == 0 else f"存在失败（返回码 {result.returncode}）"))
    print("=" * 66)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
