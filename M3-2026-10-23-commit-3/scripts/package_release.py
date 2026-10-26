#!/usr/bin/env python
"""生成交付压缩包（可重复、可验证）。

用法：
    python scripts/package_release.py --label M0-2026-9-30-commit-1
    python scripts/package_release.py --label M1-2026-10-15-commit-2 --verify

行为：
    1. 先运行 `make_manifest.py` 重新生成 MANIFEST.md 与 CHECKSUMS.sha256
    2. 把整个工作区打包为 `<label>.zip`，包内顶层目录即 `<label>/`
    3. 排除缓存、密钥、构建产物、以及压缩包自身
    4. （`--verify`）解压到临时目录，跑校验和 + 产物自检，确认包可用

为什么不用 Compress-Archive：
    ① PowerShell 的 Compress-Archive 在本项目实测会把路径写成 `ICT\\docs\\...` 这种
       带父目录名的形式，解压后多一层嵌套；
    ② 它无法可靠排除正在生成的目标文件自身。
    因此改用 zipfile 逐条写入，路径分隔符统一为 `/`。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv", "env",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".npm-cache", ".pydeps", ".vendor", ".tmp", ".pipcache", "dist", "build",
    ".idea", ".vscode",
    # 本地存储后端（STORAGE_BACKEND=local）运行时写入的目录，不含交付物
    "storage",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tmpc", ".log", ".tmp", ".swp", ".zip", ".7z"}
EXCLUDE_NAMES = {".env", ".DS_Store", "Thumbs.db", "desktop.ini"}


def should_exclude(rel: Path, *, archive_name: str) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel.parts):
        return True
    # 打包/验证过程中自己创建的临时目录（见 main() 的 _TMP_PREFIX）
    if any(part.startswith(_TMP_PREFIX) for part in rel.parts):
        return True
    if rel.name in EXCLUDE_NAMES:
        return True
    if rel.name.startswith(".env") and rel.name != ".env.example":
        return True
    # 压缩包自身绝不入包；其他 .zip 由 EXCLUDE_SUFFIXES 处理
    if rel.name == archive_name:
        return True
    return False


# 验证用临时目录前缀。为什么放在仓库内而不是系统临时目录：
#   本机沙箱下 `%TEMP%`（C:\Users\...\Temp）不允许创建/清理目录，
#   解压会直接 PermissionError。放在工作区内即可正常工作，
#   并由 should_exclude() 保证它不会被打进包里。
_TMP_PREFIX = ".package-tmp-"


def build_archive(label: str, output: Path) -> tuple[int, int]:
    """写入压缩包，返回 (条目数, 总字节)。"""
    count = 0
    total_bytes = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO_ROOT)
            if should_exclude(rel, archive_name=output.name):
                continue
            arcname = f"{label}/{rel.as_posix()}"
            info = zipfile.ZipInfo.from_file(path, arcname)
            # 统一使用 deflate，并由 zipfile 记录时间戳（保持可复现性）
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
            count += 1
            total_bytes += path.stat().st_size
    return count, total_bytes


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成交付压缩包")
    parser.add_argument("--label", required=True, help="交付标签，同时作为包内顶层目录名")
    parser.add_argument("--verify", action="store_true", help="解压后跑校验和与产物自检")
    args = parser.parse_args()

    output = REPO_ROOT / f"{args.label}.zip"

    print("=" * 66)
    print(f"  交付打包：{args.label}")
    print("=" * 66)

    # ---- 1. 重新生成清单 ----
    print("\n[1/4] 生成交付清单与校验和…")
    result = run([sys.executable, "-X", "utf8", "scripts/make_manifest.py", "--label", args.label], REPO_ROOT)
    if result.returncode != 0:
        print("  ✗ 清单生成失败：")
        print((result.stdout or "") + (result.stderr or ""))
        return 1
    for line in (result.stdout or "").splitlines():
        if line.strip().startswith(("文件数", "✓", "标签")):
            print(f"  {line.strip()}")

    # ---- 2. 打包 ----
    print("\n[2/4] 写入压缩包…")
    if output.exists():
        output.unlink()
        print(f"  · 已删除旧包 {output.name}")
    count, total_bytes = build_archive(args.label, output)
    size_kb = output.stat().st_size / 1024
    print(f"  ✓ 条目 {count} 个，未压缩 {total_bytes / 1024:.1f} KB → 压缩后 {size_kb:.1f} KB")
    print(f"  ✓ {output}")

    # ---- 3. 安全检查 ----
    print("\n[3/4] 安全检查（包内不得有密钥/缓存）…")
    with zipfile.ZipFile(output) as zf:
        names = zf.namelist()
    bad = [
        n for n in names
        if any(part in EXCLUDE_DIRS for part in n.split("/"))
        or n.endswith(tuple(EXCLUDE_SUFFIXES))
        or n.rsplit("/", 1)[-1] == ".env"
    ]
    if bad:
        print("  ✗ 发现不应入包的条目：")
        for name in bad[:10]:
            print(f"    {name}")
        return 1
    print("  ✓ 无密钥、无缓存、无嵌套压缩包")

    # 顶层结构
    tops: dict[str, int] = {}
    for name in names:
        parts = name.split("/")
        key = parts[1] if len(parts) > 2 else "(根目录文件)"
        tops[key] = tops.get(key, 0) + 1
    print("  · 包内结构：")
    for key in sorted(tops, key=lambda k: (k != "(根目录文件)", k)):
        print(f"      {key:<18} {tops[key]:>3}")

    # ---- 4. 可选验证 ----
    exit_code = 0
    if args.verify:
        print("\n[4/4] 解压验证（校验和 + 产物自检）…")
        # ⚠ 不用系统临时目录：本机沙箱下 %TEMP% 无法创建/清理目录。
        #   改为仓库内临时目录，并在 should_exclude() 中排除，避免自包含。
        tmp_path = REPO_ROOT / f"{_TMP_PREFIX}{os.getpid()}"
        shutil.rmtree(tmp_path, ignore_errors=True)
        tmp_path.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(output) as zf:
                zf.extractall(tmp_path)
            root = tmp_path / args.label
            if not root.is_dir():
                print(f"  ✗ 解压后未找到顶层目录 {args.label}")
                return 1

            check = run([sys.executable, "-X", "utf8", "scripts/make_manifest.py", "--check"], root)
            for line in (check.stdout or "").splitlines():
                if "结论" in line or "变更" in line:
                    print(f"  {line.strip()}")
            if check.returncode != 0:
                print("  ✗ 校验和验证未通过")
                exit_code = 1

            verify = run([sys.executable, "-X", "utf8", "scripts/verify_m0.py"], root)
            for line in (verify.stdout or "").splitlines():
                if "通过" in line and "项" in line:
                    print(f"  {line.strip()}")
            if verify.returncode != 0:
                print("  ✗ 产物自检未通过")
                for line in (verify.stdout or "").splitlines():
                    if line.strip().startswith("·"):
                        print(f"    {line.strip()}")
                exit_code = 1

            if exit_code == 0:
                print("  ✓ 解压后验证全部通过")
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)
            if tmp_path.exists():
                print(f"  ! 临时目录未能清理，可手动删除：{tmp_path}")
    else:
        print("\n[4/4] 跳过解压验证（加 --verify 可启用）")

    print("\n" + "=" * 66)
    print(f"  完成：{output.name}")
    print("=" * 66)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
