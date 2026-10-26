#!/usr/bin/env python
"""云部署前置自检（静态检查，不需要 Docker / 云资源）。

用途：
    在实际执行 `docker compose build` 与云端部署**之前**，确认所有前置条件就绪。
    本机未安装 Docker 时，这是唯一能做的有效验证 —— 它能抓出绝大多数
    "构建到一半才失败"的问题（缺文件、COPY 路径错、端口冲突、密钥未替换等）。

用法：
    python scripts/preflight_cloud.py
    python scripts/preflight_cloud.py --verbose

退出码：
    0  可以进入云端部署
    1  存在阻塞项（BLOCKER）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

BLOCKERS: list[str] = []
WARNINGS: list[str] = []
PASSED: list[str] = []


def ok(msg: str) -> None:
    PASSED.append(msg)


def blocker(msg: str) -> None:
    BLOCKERS.append(msg)


def warn(msg: str) -> None:
    WARNINGS.append(msg)


def check(condition: bool, good: str, bad: str) -> bool:
    if condition:
        ok(good)
    else:
        blocker(bad)
    return condition


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 1. Docker 构建上下文与 COPY 路径
# ---------------------------------------------------------------------------
# 三个 Dockerfile 的构建上下文都是仓库根目录，逐个核对其 COPY 源是否存在。
REQUIRED_BUILD_SOURCES = {
    "Dockerfile": [
        "backend/requirements.txt",
        "backend/app/main.py",
        "backend/alembic/env.py",
        "backend/alembic/versions/0001_initial_schema.py",
        "algorithm/config/matching.yaml",
        "scripts/seed_demo_data.py",
        "scripts/init_db.sql",
    ],
    "algorithm/Dockerfile": [
        "algorithm/requirements.txt",
        "algorithm/app/main.py",
        "algorithm/config/matching.yaml",
        "algorithm/config/locations.yaml",
        "algorithm/config/questions.yaml",
        "algorithm/data/demo_items.json",
    ],
    "frontend/Dockerfile": [
        "frontend/package.json",
        "frontend/package-lock.json",  # ⚠ npm ci 必需：缺它构建直接失败
        "frontend/index.html",
        "frontend/vite.config.ts",
        "frontend/tsconfig.json",
        "frontend/src/main.ts",
        "frontend/src/router/index.ts",
        "deploy/nginx.conf",
    ],
}


def check_build_sources(verbose: bool) -> None:
    print("\n[1] Docker 构建上下文与 COPY 源文件")
    print("-" * 66)
    for dockerfile, sources in REQUIRED_BUILD_SOURCES.items():
        missing = [s for s in sources if not (REPO_ROOT / s).is_file()]
        if missing:
            blocker(f"{dockerfile} 的 COPY 源缺失：{missing}")
        else:
            ok(f"{dockerfile} 引用的 {len(sources)} 个构建源文件均存在")

    # Dockerfile 里出现的 COPY 指令，逐条核对源路径真实存在。
    # ⚠ 多阶段构建的 `COPY --from=<stage> <src> <dst>` 中，src 是**上一阶段镜像内**的路径，
    #   不是仓库路径（如 /install、/app/dist），必须跳过，否则全是误报。
    for dockerfile in REQUIRED_BUILD_SOURCES:
        text = read(REPO_ROOT / dockerfile)
        for match in re.finditer(r"^COPY\s+((?:--\S+\s+)*)(\S+)\s+(\S+)", text, re.MULTILINE):
            flags, src = match.group(1), match.group(2)
            if "--from=" in flags:
                continue  # 来自构建阶段，非仓库路径
            if src.startswith(("/", "$")) or ".." in src:
                continue
            if src in {"backend/", "frontend/", "algorithm/", "scripts/", "."}:
                continue
            if any(ch in src for ch in "*?["):
                continue
            if not (REPO_ROOT / src).exists():
                warn(f"{dockerfile}: COPY 源 `{src}` 在仓库中不存在")


# ---------------------------------------------------------------------------
# 2. 环境变量与密钥占位符
# ---------------------------------------------------------------------------
def check_env(verbose: bool) -> None:
    print("\n[2] 环境变量模板")
    print("-" * 66)
    env_example = read(REPO_ROOT / ".env.example")
    if not env_example:
        blocker(".env.example 缺失或不可读")
        return

    required_vars = [
        "APP_ENV", "APP_SECRET_KEY", "DATABASE_URL", "REDIS_URL",
        "OBS_ENDPOINT", "OBS_BUCKET", "OBS_ACCESS_KEY", "OBS_SECRET_KEY",
        "MAAS_ENDPOINT", "MAAS_API_KEY", "MAAS_ENABLED",
        "ALGORITHM_SERVICE_URL", "STORAGE_BACKEND",
        "MYSQL_ROOT_PASSWORD", "MYSQL_PASSWORD", "REDIS_PASSWORD",
    ]
    missing = [v for v in required_vars if f"{v}=" not in env_example]
    check(not missing, f".env.example 含全部 {len(required_vars)} 个必需变量", f".env.example 缺少：{missing}")

    # 只把「真正的密钥」纳入检查 —— 早先的版本用白名单排除配置项，
    # 结果把 PAGE_SIZE_MAX=50、DB_POOL_SIZE=10 这类数值配置全报成"疑似真实值"，
    # 属于典型的误报。改为**按密钥关键词识别**：名字里含这些词，值就必须是占位符。
    SECRET_NAME_HINTS = (
        "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "TOKEN", "CREDENTIAL",
    )
    PLACEHOLDER_HINTS = ("replace_me", "dev_only", "example", "changeme", "xxx", "your_")
    # OBS_ENDPOINT / MAAS_ENDPOINT 是地址不是密钥，但也不应是真实内网地址
    ENDPOINT_VARS = {"OBS_ENDPOINT", "MAAS_ENDPOINT"}

    suspicious: list[str] = []
    for line in env_example.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("#")[0].strip()
        if not value:
            continue

        upper = key.upper()
        looks_secret = any(hint in upper for hint in SECRET_NAME_HINTS)
        if looks_secret and not any(hint in value.lower() for hint in PLACEHOLDER_HINTS):
            suspicious.append(key)
            continue

        if key in ENDPOINT_VARS and not any(hint in value.lower() for hint in PLACEHOLDER_HINTS):
            # 示例地址允许保留，但真实 Endpoint 不应写死在模板里
            if "example" not in value.lower() and "replace_me" not in value.lower():
                suspicious.append(f"{key}(示例地址应使用 replace_me 占位)")

    check(not suspicious, "敏感变量在 .env.example 中均为占位符", f".env.example 疑似含真实值：{suspicious}")

    # 生产环境必填项清单（与后端强校验保持一致）
    ok("生产环境强校验项（后端启动时会拒绝缺失）：APP_SECRET_KEY / DATABASE_URL / REDIS_URL / OBS_*")


# ---------------------------------------------------------------------------
# 3. 编排与网络隔离
# ---------------------------------------------------------------------------
def check_compose(verbose: bool) -> None:
    print("\n[3] docker-compose 编排")
    print("-" * 66)
    compose = read(REPO_ROOT / "docker-compose.yml")
    if not compose:
        blocker("docker-compose.yml 缺失或不可读")
        return

    for service in ("mysql", "redis", "algorithm", "backend", "frontend"):
        check(f"  {service}:" in compose, f"定义了 {service} 服务", f"缺少 {service} 服务定义")

    check("internal: true" in compose, "内网隔离已启用（数据库/Redis/算法不暴露公网）", "未启用内网隔离")
    check("${MYSQL_PASSWORD" in compose, "数据库密码走环境变量", "数据库密码未走环境变量")
    check("condition: service_healthy" in compose, "依赖服务带健康检查门控", "缺少服务健康检查门控")

    # 算法服务不应映射宿主机端口
    algorithm_block = compose.split("algorithm:", 1)[-1].split("backend:", 1)[0] if "algorithm:" in compose else ""
    check("ports:" not in algorithm_block, "算法服务未映射宿主机端口（仅内网）", "算法服务映射了宿主机端口，存在暴露风险")

    # init_db.sql 挂载
    check("init_db.sql" in compose, "挂载了 init_db.sql 自动建表", "未挂载 init_db.sql")


# ---------------------------------------------------------------------------
# 4. Nginx 与网络暴露面
# ---------------------------------------------------------------------------
def check_nginx(verbose: bool) -> None:
    print("\n[4] Nginx 反向代理")
    print("-" * 66)
    nginx = read(REPO_ROOT / "deploy" / "nginx.conf")
    if not nginx:
        blocker("deploy/nginx.conf 缺失或不可读")
        return

    checks = [
        ("try_files", "SPA history 路由回退"),
        ("client_max_body_size", "上传体积上限"),
        ("proxy_set_header X-Request-ID", "请求追踪 ID 透传"),
        ("location /api/", "API 反向代理"),
        ("location = /health", "健康检查代理"),
        ("server_tokens off", "隐藏 Nginx 版本号"),
        ("X-Content-Type-Options", "安全响应头"),
    ]
    for token, label in checks:
        check(token in nginx, f"Nginx 配置了{label}", f"Nginx 缺少{label}")

    check(not re.search(r"^\s*proxy_pass\s+http://127\.0\.0\.1", nginx, re.MULTILINE),
          "Nginx 使用容器服务名而非 127.0.0.1",
          "Nginx 指向 127.0.0.1 —— 容器内无法访问，应为服务名 backend:8000")


# ---------------------------------------------------------------------------
# 5. 密钥卫生
# ---------------------------------------------------------------------------
def check_secrets(verbose: bool) -> None:
    print("\n[5] 密钥卫生")
    print("-" * 66)

    if (REPO_ROOT / ".env").is_file():
        warn("工作区存在 .env —— 请确认它已被 .gitignore 忽略且从未提交/打包")
    else:
        ok("工作区无 .env（只有 .env.example）")

    gitignore = read(REPO_ROOT / ".gitignore")
    for pattern in (".env", ".vendor/"):
        check(pattern in gitignore, f".gitignore 忽略 {pattern}", f".gitignore 未忽略 {pattern} —— 泄露风险")

    # 全局扫描源码中的长密钥
    suspicious: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".py", ".ts", ".vue", ".yml", ".yaml", ".sql", ".md"}:
            continue
        if any(part in {".vendor", "node_modules", "__pycache__", ".git"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(
            r"(?i)(access_key|secret_key|api_key|password)\s*[:=]\s*[\"']([A-Za-z0-9+/=]{24,})[\"']", text
        ):
            value = match.group(2)
            if "replace_me" in value.lower() or "example" in value.lower():
                continue
            suspicious.append(f"{path.relative_to(REPO_ROOT)}: {match.group(1)}")
    check(not suspicious, "源码中未发现硬编码密钥", f"疑似硬编码密钥：{suspicious[:6]}")


# ---------------------------------------------------------------------------
# 6. 数据库初始化脚本
# ---------------------------------------------------------------------------
def check_database(verbose: bool) -> None:
    print("\n[6] 数据库初始化")
    print("-" * 66)
    sql = read(REPO_ROOT / "scripts" / "init_db.sql")
    if not sql:
        blocker("scripts/init_db.sql 缺失或不可读")
        return

    tables = ["users", "items", "item_images", "matches", "claims", "audit_logs", "alembic_version"]
    for table in tables:
        check(f"`{table}`" in sql, f"init_db.sql 创建 {table}", f"init_db.sql 缺少 {table}")

    check("utf8mb4" in sql, "使用 utf8mb4 字符集", "未使用 utf8mb4")
    check("GRANT SELECT, INSERT" in sql, "包含 audit_logs 最小权限方案", "缺少最小权限方案")
    check("time_zone" in sql.lower() or "+00:00" in sql, "显式设定时区（UTC 存储约定）", "未显式设定时区")

    # 迁移脚本存在性
    migrations = list((REPO_ROOT / "backend" / "alembic" / "versions").glob("*.py"))
    check(bool(migrations), f"Alembic 迁移脚本存在（{len(migrations)} 个）", "缺少 Alembic 迁移脚本")


# ---------------------------------------------------------------------------
# 7. 部署后验证能力
# ---------------------------------------------------------------------------
def check_verification_tools(verbose: bool) -> None:
    print("\n[7] 部署后验证工具")
    print("-" * 66)
    tools = {
        "scripts/healthcheck.py": "健康检查（分别报告各依赖状态）",
        "scripts/seed_demo_data.py": "演示数据导入",
        "scripts/verify_m0.py": "产物与契约自检",
    }
    for path, label in tools.items():
        check((REPO_ROOT / path).is_file(), f"{label}：{path}", f"缺少{label}（{path}）")

    e2e = REPO_ROOT / "scripts" / "e2e_demo.py"
    if e2e.is_file():
        ok("端到端验收脚本：scripts/e2e_demo.py")
    else:
        warn("缺少端到端验收脚本（scripts/e2e_demo.py）—— 云端部署后无法自动走完整流程")


# ---------------------------------------------------------------------------
# 8. 部署文档
# ---------------------------------------------------------------------------
def check_docs(verbose: bool) -> None:
    print("\n[8] 部署文档")
    print("-" * 66)
    docs = {
        "docs/deployment.md": "部署方案（本地 + 云端）",
        "docs/cloud-resources.md": "云资源清单与交接",
        "docs/troubleshooting.md": "排障手册",
    }
    for path, label in docs.items():
        check((REPO_ROOT / path).is_file(), f"{label}：{path}", f"缺少{label}（{path}）")

    cloud_doc = REPO_ROOT / "docs" / "cloud-deployment-runbook.md"
    if cloud_doc.is_file():
        ok("云端部署流程文档：docs/cloud-deployment-runbook.md")
    else:
        warn("缺少独立的云端部署流程文档（docs/cloud-deployment-runbook.md）")

    # 未填写的占位符统计
    for path in ("docs/cloud-resources.md",):
        text = read(REPO_ROOT / path)
        count = text.count("待填")
        if count:
            warn(f"{path} 中仍有 {count} 处「待填」—— 需在资源开通后补齐")


def main() -> int:
    parser = argparse.ArgumentParser(description="云部署前置自检")
    parser.add_argument("--verbose", action="store_true", help="输出全部通过项")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    if not args.json:
        print("=" * 70)
        print("  拾光 · 云部署前置自检（静态检查，无需 Docker / 云资源）")
        print(f"  仓库：{REPO_ROOT}")
        print("=" * 70)

    check_build_sources(args.verbose)
    check_env(args.verbose)
    check_compose(args.verbose)
    check_nginx(args.verbose)
    check_secrets(args.verbose)
    check_database(args.verbose)
    check_verification_tools(args.verbose)
    check_docs(args.verbose)

    if args.json:
        print(json.dumps(
            {"passed": PASSED, "warnings": WARNINGS, "blockers": BLOCKERS},
            ensure_ascii=False, indent=2,
        ))
        return 1 if BLOCKERS else 0

    print("\n" + "=" * 70)
    print(f"  通过 {len(PASSED)} 项 ｜ 警告 {len(WARNINGS)} 项 ｜ 阻塞 {len(BLOCKERS)} 项")
    print("=" * 70)

    if args.verbose and PASSED:
        print("\n  ✓ 通过项：")
        for msg in PASSED:
            print(f"    · {msg}")

    if WARNINGS:
        print("\n  ! 警告（不阻塞部署，但建议处理）：")
        for msg in WARNINGS:
            print(f"    · {msg}")

    if BLOCKERS:
        print("\n  ✗ 阻塞项（必须修复后才能部署）：")
        for msg in BLOCKERS:
            print(f"    · {msg}")
        print("\n  结论：尚不具备云端部署条件")
        return 1

    print("\n  结论：静态前置条件齐备，可以进入云端部署")
    print("  下一步：确认华为云账号与资源归属后，执行 docs/cloud-deployment-runbook.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
