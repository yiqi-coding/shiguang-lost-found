#!/usr/bin/env python
"""生成交付清单与校验和（打包前运行）。

用法：
    python scripts/make_manifest.py                     # 生成 MANIFEST.md 与 CHECKSUMS.sha256
    python scripts/make_manifest.py --check             # 校验现有 CHECKSUMS.sha256 是否仍然一致
    python scripts/make_manifest.py --label M0-2026-9-30-commit-1

输出：
    MANIFEST.md         —— 人可读的交付清单（文件数、分类、逐文件大小与 SHA-256 前 16 位）
    CHECKSUMS.sha256    —— 机可验证的校验和（sha256sum 兼容格式）

排除规则与 .gitignore 保持一致：不含 .env、缓存、虚拟环境、node_modules、构建产物。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 需要排除的目录名（任意层级）
EXCLUDE_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv", "env",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".npm-cache", ".pydeps", ".vendor", ".tmp", ".pipcache",
    "dist", "build", ".idea", ".vscode",
}
# 需要排除的文件名/后缀
#
# ⚠ `.zip` 必须排除：交付压缩包与清单都在仓库根目录生成，
#    若把 zip 本身写进校验和，解压后的目录里当然找不到它，
#    `--check` 会误报「缺失 1 个文件」。这是打包顺序导致的自引用问题。
EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo", ".tmpc", ".log", ".tmp", ".swp", ".zip", ".7z", ".tar.gz"}
EXCLUDE_FILE_NAMES = {".env", ".DS_Store", "Thumbs.db", "desktop.ini"}

# 敏感模式：出现即视为红线（用于打包前自检）
SENSITIVE_NAME_PATTERNS = (".env", "secret", "credential", ".pem", ".key", ".p12")

# 分类展示顺序
CATEGORY_ORDER = [
    ("(根目录)", "根目录"),
    ("docs", "文档"),
    ("backend", "后端"),
    ("frontend", "前端"),
    ("algorithm", "算法"),
    ("scripts", "脚本"),
    ("deploy", "部署"),
    ("data", "数据"),
]


def is_excluded(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
        return True
    # .env.example 允许；其他 .env* 一律排除
    if path.name.startswith(".env") and path.name != ".env.example":
        return True
    return False


def collect_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if is_excluded(rel):
            continue
        files.append(rel)
    # 排序：先根目录，再按目录名、文件名
    return sorted(files, key=lambda p: (len(p.parts) > 1, str(p).lower()))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def category_of(rel: Path) -> str:
    if len(rel.parts) == 1:
        return "(根目录)"
    return rel.parts[0]


def human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def count_code_lines(files: list[Path]) -> dict[str, tuple[int, int]]:
    """统计各类代码的行数与文件数。"""
    groups: dict[str, list[int]] = {}
    for rel in files:
        suffix = rel.suffix.lower()
        if suffix == ".py":
            key = "Python"
        elif suffix in {".ts", ".vue"}:
            key = "TypeScript / Vue"
        elif suffix in {".md"}:
            key = "Markdown"
        elif suffix in {".yaml", ".yml"}:
            key = "YAML 配置"
        elif suffix in {".sql"}:
            key = "SQL"
        elif suffix in {".json"}:
            key = "JSON 数据"
        else:
            key = "其他"
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
            lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        except Exception:  # noqa: BLE001
            lines = 0
        groups.setdefault(key, []).append(lines)
    return {
        key: (len(values), sum(values))
        for key, values in sorted(groups.items(), key=lambda kv: -sum(kv[1]))
    }


def build_manifest(label: str, files: list[Path], checksums: dict[str, str]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_bytes = sum((REPO_ROOT / rel).stat().st_size for rel in files)
    code_stats = count_code_lines(files)

    lines: list[str] = []
    lines.append(f"# 交付清单 · {label}")
    lines.append("")
    lines.append(f"> 生成时间：{now}（本地时区）")
    lines.append(f"> 文件总数：**{len(files)}**")
    lines.append(f"> 未压缩体积：**{human_size(total_bytes)}**")
    lines.append(f"> 校验方式：`sha256sum -c CHECKSUMS.sha256`（或 `python scripts/make_manifest.py --check`）")
    lines.append("")
    lines.append("本清单由 `scripts/make_manifest.py` 自动生成，**请勿手工编辑**。")
    lines.append("")

    # ---- 规模概览 ----
    lines.append("## 1. 规模概览")
    lines.append("")
    lines.append("| 类型 | 文件数 | 代码/内容行数 |")
    lines.append("|---|---:|---:|")
    for key, (count, total_lines) in code_stats.items():
        lines.append(f"| {key} | {count} | {total_lines:,} |")
    lines.append("")

    # ---- 分类统计 ----
    lines.append("## 2. 分类统计")
    lines.append("")
    lines.append("| 分类 | 文件数 | 体积 |")
    lines.append("|---|---:|---:|")
    for dir_name, cn_name in CATEGORY_ORDER:
        subset = [f for f in files if category_of(f) == dir_name]
        if not subset:
            continue
        size = sum((REPO_ROOT / f).stat().st_size for f in subset)
        lines.append(f"| {cn_name}（`{dir_name}`） | {len(subset)} | {human_size(size)} |")
    lines.append("")

    # ---- 完整文件列表 ----
    lines.append("## 3. 完整文件列表")
    lines.append("")
    for dir_name, cn_name in CATEGORY_ORDER:
        subset = [f for f in files if category_of(f) == dir_name]
        if not subset:
            continue
        lines.append(f"### {cn_name}（`{dir_name}`）")
        lines.append("")
        lines.append("| 文件 | 大小 | SHA-256（前 16 位） |")
        lines.append("|---|---:|---|")
        for rel in subset:
            path = REPO_ROOT / rel
            lines.append(
                f"| `{rel.as_posix()}` | {human_size(path.stat().st_size)} | `{checksums[rel.as_posix()][:16]}` |"
            )
        lines.append("")

    # ---- 排除说明 ----
    lines.append("## 4. 未包含的内容（有意排除）")
    lines.append("")
    lines.append("| 项 | 原因 |")
    lines.append("|---|---|")
    lines.append("| `.env` 及任何真实密钥 | **安全红线**：密钥只能通过部署平台注入，绝不进入仓库或压缩包 |")
    lines.append("| `__pycache__` / `node_modules` / 各类缓存 | 构建产物，可由源码重建 |")
    lines.append("| `frontend/package-lock.json` | 依赖锁文件需在真实 `npm install` 后生成，⏳ 待 M1 补齐 |")
    lines.append("| 演示图片原图（`data/images/*`） | 体积与版权考虑，改为独立交接（见 `data/images/README.md`） |")
    lines.append("| 演示视频 / 答辩 PPT | 属 M5 交付物，尚未产出 |")
    lines.append("| 云端资源凭证与实例信息 | 敏感，按 `docs/cloud-resources.md` 的安全渠道单独交接 |")
    lines.append("")

    # ---- 已知缺口 ----
    lines.append("## 5. 本版本已知缺口")
    lines.append("")
    lines.append("| # | 缺口 | 影响 | 计划 |")
    lines.append("|---|---|---|---|")
    lines.append("| 1 | 前端未执行 `npm install` / `vue-tsc` / `vite build` | 类型与构建未经真实编译验证 | M1 第 1 天 |")
    lines.append("| 2 | 后端测试运行在依赖替身（`tests/_stubs.py`）上 | 未与真实 fastapi/sqlalchemy 联调 | M1 第 1 天 |")
    lines.append("| 3 | `alembic upgrade head` 未实际执行 | 迁移未经真实数据库验证 | M1 第 1–2 天 |")
    lines.append("| 4 | 本机未安装 MySQL / Redis / Docker | 无法本地端到端演示 | M1 / M4 |")
    lines.append("| 5 | 华为云账号与代码仓库归属未定 | **阻塞 M4 部署** | M0 评审会 |")
    lines.append("| 6 | 无 `package-lock.json` | `frontend/Dockerfile` 的 `npm ci` 会失败，需改用 `npm install` | M1 |")
    lines.append("")
    lines.append("详见 [docs/test-report.md](docs/test-report.md) §0.1 与 [docs/M0-交付索引.md](docs/M0-交付索引.md) §5。")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成交付清单与校验和")
    parser.add_argument("--label", default="M0-2026-9-30-commit-1", help="交付标签（如 M0-2026-9-30-commit-1）")
    parser.add_argument("--check", action="store_true", help="校验现有 CHECKSUMS.sha256 是否一致")
    args = parser.parse_args()

    files = collect_files()
    checksums: dict[str, str] = {}
    for rel in files:
        checksums[rel.as_posix()] = sha256_of(REPO_ROOT / rel)

    if args.check:
        checksum_file = REPO_ROOT / "CHECKSUMS.sha256"
        if not checksum_file.is_file():
            print("✗ 未找到 CHECKSUMS.sha256，请先运行 `python scripts/make_manifest.py`")
            return 2
        recorded: dict[str, str] = {}
        for line in checksum_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, name = line.partition("  ")
            recorded[name.strip()] = digest.strip()

        missing = [name for name in recorded if name not in checksums]
        added = [name for name in checksums if name not in recorded]
        changed = [
            name for name in recorded
            if name in checksums and checksums[name] != recorded[name]
        ]

        print("=" * 62)
        print("  交付校验和验证")
        print("=" * 62)
        print(f"  记录文件数：{len(recorded)}")
        print(f"  当前文件数：{len(checksums)}")
        print(f"  新增：{len(added)}  缺失：{len(missing)}  变更：{len(changed)}")
        for name in changed:
            print(f"    ~ {name}")
        for name in missing:
            print(f"    - {name}（清单中有，磁盘上不存在）")
        for name in added[:20]:
            print(f"    + {name}（新增，未在清单中）")
        if added and len(added) > 20:
            print(f"    ... 另有 {len(added) - 20} 个新增文件")

        if changed:
            print("\n  结论：内容已变更（若为有意修改，请重新运行生成清单）")
            return 1
        if missing:
            print("\n  结论：有文件缺失")
            return 1
        print("\n  结论：清单与实际内容一致")
        return 0

    # ---- 生成 ----
    manifest_path = REPO_ROOT / "MANIFEST.md"
    checksum_path = REPO_ROOT / "CHECKSUMS.sha256"

    # 清单与校验和文件本身不参与校验和计算（避免自引用）
    self_names = {"MANIFEST.md", "CHECKSUMS.sha256"}
    files_for_checksum = [f for f in files if f.as_posix() not in self_names]

    checksum_lines = [
        "# 拾光 · 交付校验和",
        f"# 标签：{args.label}",
        f"# 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "# 校验：sha256sum -c CHECKSUMS.sha256",
        "# 说明：MANIFEST.md 与 CHECKSUMS.sha256 自身不参与校验（避免自引用）",
        "",
    ]
    for rel in files_for_checksum:
        checksum_lines.append(f"{checksums[rel.as_posix()]}  {rel.as_posix()}")
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    manifest_path.write_text(build_manifest(args.label, files, checksums), encoding="utf-8")

    print("=" * 62)
    print("  交付清单已生成")
    print("=" * 62)
    print(f"  标签：{args.label}")
    print(f"  文件数：{len(files)}（校验和覆盖 {len(files_for_checksum)} 个）")
    print(f"  ✓ {manifest_path.relative_to(REPO_ROOT)}")
    print(f"  ✓ {checksum_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
