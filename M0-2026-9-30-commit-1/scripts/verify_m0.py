#!/usr/bin/env python
"""M0 产物自检脚本（评审与 CI 用）。

用途：
    在不启动任何服务、不连接数据库的前提下，校验 M0 交付物是否齐全、契约是否一致、
    安全红线是否有明文缺口。**只读**，不修改任何文件。

用法：
    python scripts/verify_m0.py
    python scripts/verify_m0.py --verbose

退出码：
    0  全部通过（允许存在 WARN）
    1  存在 FAIL
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FAIL: list[str] = []
WARN: list[str] = []
PASS: list[str] = []


def ok(msg: str) -> None:
    PASS.append(msg)


def fail(msg: str) -> None:
    FAIL.append(msg)


def warn(msg: str) -> None:
    WARN.append(msg)


def check(condition: bool, good: str, bad: str) -> bool:
    if condition:
        ok(good)
    else:
        fail(bad)
    return condition


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        fail(f"无法读取 {path.relative_to(REPO_ROOT)}：{type(exc).__name__}")
        return ""


# ---------------------------------------------------------------------------
# 1. 交付物完整性
# ---------------------------------------------------------------------------
REQUIRED_FILES = [
    "README.md",
    ".env.example",
    ".gitignore",
    "Dockerfile",
    "docker-compose.yml",
    "deploy/nginx.conf",
    "docs/api-contract.md",
    "docs/database-design.md",
    "docs/prototypes.md",
    "docs/plan.md",
    "docs/deployment.md",
    "docs/M0-交付索引.md",
    "scripts/init_db.sql",
    "scripts/seed_demo_data.py",
    "scripts/healthcheck.py",
    "backend/requirements.txt",
    "backend/app/main.py",
    "backend/app/core/config.py",
    "backend/app/core/enums.py",
    "backend/app/core/errors.py",
    "backend/app/core/response.py",
    "backend/app/core/security.py",
    "backend/app/models/user.py",
    "backend/app/models/item.py",
    "backend/app/models/match.py",
    "backend/app/models/claim.py",
    "backend/app/models/audit_log.py",
    "backend/app/services/serializers.py",
    "backend/app/services/state_machine.py",
    "backend/app/services/scoring.py",
    "backend/app/services/cache.py",
    "backend/app/services/storage.py",
    "backend/app/services/file_service.py",
    "backend/app/services/audit_service.py",
    "backend/app/clients/algorithm_client.py",
    "backend/app/clients/maas_client.py",
    "backend/alembic/env.py",
    "backend/alembic/versions/0001_initial_schema.py",
    "frontend/package.json",
    "frontend/src/main.ts",
    "frontend/src/router/index.ts",
    "frontend/src/api/http.ts",
    "algorithm/app/matcher.py",
    "algorithm/app/extractor.py",
    "algorithm/app/credibility.py",
    "algorithm/config/matching.yaml",
    "algorithm/config/locations.yaml",
    "algorithm/config/questions.yaml",
    "algorithm/data/demo_items.json",
    "algorithm/data/demo_matches.json",
]

FRONTEND_VIEWS = [
    "HomeView.vue",
    "ItemListView.vue",
    "ItemCreateLostView.vue",
    "ItemCreateFoundView.vue",
    "ItemDetailView.vue",
    "MatchResultView.vue",
    "ClaimVerifyView.vue",
    "MyRecordsView.vue",
    "AdminReviewView.vue",
    "AdminLogsView.vue",
]


def check_files(verbose: bool) -> None:
    print("\n[1] 交付物完整性")
    print("-" * 66)

    missing = [f for f in REQUIRED_FILES if not (REPO_ROOT / f).is_file()]
    check(not missing, f"必需文件齐全（{len(REQUIRED_FILES)} 项）", f"缺失文件 {len(missing)} 个：{missing}")

    views_dir = REPO_ROOT / "frontend" / "src" / "views"
    missing_views = [v for v in FRONTEND_VIEWS if not (views_dir / v).is_file()]
    check(
        not missing_views,
        f"前端 10 个页面齐全",
        f"缺失页面：{missing_views}",
    )

    # .env 绝不能存在（存在即说明有人把密钥放进了工作区）
    if (REPO_ROOT / ".env").is_file():
        warn("工作区存在 .env（本地开发正常，但务必确认它被 .gitignore 忽略且从未提交）")
    else:
        ok("工作区没有 .env（只有 .env.example）")

    gitignore = read(REPO_ROOT / ".gitignore")
    check(
        bool(re.search(r"^\.env$", gitignore, re.MULTILINE)),
        ".gitignore 已忽略 .env",
        ".gitignore 未忽略 .env —— 密钥泄露风险！",
    )


# ---------------------------------------------------------------------------
# 2. 契约一致性
# ---------------------------------------------------------------------------
def check_contract(verbose: bool) -> None:
    print("\n[2] 契约一致性（枚举 / 权重 / 错误码）")
    print("-" * 66)

    enums_src = read(REPO_ROOT / "backend" / "app" / "core" / "enums.py")
    contract = read(REPO_ROOT / "docs" / "api-contract.md")
    errors_src = read(REPO_ROOT / "backend" / "app" / "core" / "errors.py")

    # 2.1 物品状态枚举
    expected_item_status = [
        "published", "matched", "claiming", "pending_review", "request_more_info",
        "claimed", "rejected", "suspended", "disputed", "closed",
    ]
    missing_in_enum = [s for s in expected_item_status if f'"{s}"' not in enums_src]
    missing_in_doc = [s for s in expected_item_status if f"`{s}`" not in contract]
    check(
        not missing_in_enum,
        "后端 enums.py 包含全部 10 个物品状态",
        f"enums.py 缺失状态：{missing_in_enum}",
    )
    check(
        not missing_in_doc,
        "api-contract.md 包含全部 10 个物品状态",
        f"api-contract.md 缺失状态：{missing_in_doc}",
    )

    # 2.2 错误码
    expected_errors = [
        "VALIDATION_ERROR", "UNAUTHORIZED", "FORBIDDEN", "ITEM_NOT_FOUND",
        "CLAIM_NOT_FOUND", "MATCH_NOT_FOUND", "ALGORITHM_SERVICE_ERROR",
        "STORAGE_SERVICE_ERROR", "AI_SERVICE_ERROR", "RATE_LIMITED", "INTERNAL_ERROR",
    ]
    missing_errors = [e for e in expected_errors if e not in errors_src]
    check(
        not missing_errors,
        f"后端错误码齐全（{len(expected_errors)} 个）",
        f"errors.py 缺失错误码：{missing_errors}",
    )

    # 2.3 权重总和必须为 1.0（从配置读取，不硬编码）
    matching_yaml = read(REPO_ROOT / "algorithm" / "config" / "matching.yaml")
    weights = {}
    in_weights_block = False
    for line in matching_yaml.splitlines():
        if re.match(r"^weights:\s*$", line):
            in_weights_block = True
            continue
        if in_weights_block:
            if re.match(r"^\S", line):
                break
            m = re.match(r"^\s+(\w+):\s*([\d.]+)", line)
            if m:
                weights[m.group(1)] = float(m.group(2))

    if weights:
        total = round(sum(weights.values()), 6)
        check(
            abs(total - 1.0) < 1e-6,
            f"匹配权重总和 = 1.0（{weights}）",
            f"匹配权重总和 = {total} ≠ 1.0 —— 总分将超出 [0,1] 归一化要求",
        )
        expected_weights = {
            "category": 0.20, "text": 0.25, "image": 0.20,
            "time": 0.15, "location": 0.15, "brand_model": 0.05,
        }
        mismatched = {
            k: (weights.get(k), v) for k, v in expected_weights.items() if abs(weights.get(k, -1) - v) > 1e-9
        }
        check(
            not mismatched,
            "匹配权重与需求 §4.4.2 冻结值一致",
            f"权重与冻结值不符（实际, 期望）：{mismatched}",
        )
    else:
        fail("未能从 algorithm/config/matching.yaml 解析出 weights")

    # 2.4 算法配置与后端兜底默认值一致
    config_py = read(REPO_ROOT / "backend" / "app" / "core" / "config.py")
    pairs = [
        ("MATCH_WEIGHT_CATEGORY", 0.20), ("MATCH_WEIGHT_ATTRIBUTE", 0.25),
        ("MATCH_WEIGHT_IMAGE", 0.20), ("MATCH_WEIGHT_TIME", 0.15),
        ("MATCH_WEIGHT_LOCATION", 0.15), ("MATCH_WEIGHT_BRAND_MODEL", 0.05),
    ]
    bad = []
    for name, value in pairs:
        m = re.search(rf"{name}:\s*float\s*=\s*([\d.]+)", config_py)
        if not m or abs(float(m.group(1)) - value) > 1e-9:
            bad.append(name)
    check(
        not bad,
        "后端 config.py 的权重兜底默认值与契约一致",
        f"后端兜底默认值不符：{bad}",
    )


# ---------------------------------------------------------------------------
# 3. 安全红线
# ---------------------------------------------------------------------------
def check_security(verbose: bool) -> None:
    print("\n[3] 安全红线")
    print("-" * 66)

    serializers = read(REPO_ROOT / "backend" / "app" / "services" / "serializers.py")
    check(
        "hidden_features" in serializers and "can_see_private" in serializers,
        "序列化层实现了 hidden_features 的字段级权限控制",
        "serializers.py 未实现 hidden_features 权限控制 —— 红线风险！",
    )
    check(
        'FORBIDDEN_KEYS_FOR_PUBLIC' in serializers,
        "存在公共响应泄露自检常量（供测试断言）",
        "缺少 FORBIDDEN_KEYS_FOR_PUBLIC 自检常量",
    )

    logging_src = read(REPO_ROOT / "backend" / "app" / "core" / "logging.py")
    for token, label in [
        ("authorization", "Authorization 头"),
        ("hidden_features", "隐藏特征"),
        ("answers", "认领答案"),
    ]:
        check(
            token in logging_src.lower(),
            f"日志脱敏覆盖 {label}",
            f"日志脱敏未覆盖 {label} —— 泄露风险！",
        )

    audit_src = read(REPO_ROOT / "backend" / "app" / "services" / "audit_service.py")
    check(
        "sanitize_detail" in audit_src,
        "审计详情写入前统一脱敏",
        "审计详情缺少脱敏函数",
    )

    security_src = read(REPO_ROOT / "backend" / "app" / "core" / "security.py")
    check(
        "hmac" in security_src and "hash_answer" in security_src,
        "认领答案使用加盐 HMAC 哈希（不落明文）",
        "答案哈希未使用加盐 HMAC",
    )
    check(
        "generate_object_key" in security_src,
        "对象 Key 由服务端生成（不使用原始文件名）",
        "缺少对象 Key 生成函数",
    )

    file_svc = read(REPO_ROOT / "backend" / "app" / "services" / "file_service.py")
    for token, label in [
        ("MAGIC_SIGNATURES", "魔数校验"),
        ("DANGEROUS_SIGNATURES", "危险文件类型拦截"),
        ("verify()", "图片可解析性校验"),
    ]:
        check(token in file_svc, f"上传校验包含{label}", f"上传校验缺少{label}")

    init_sql = read(REPO_ROOT / "scripts" / "init_db.sql")
    check(
        "audit_logs" in init_sql and "GRANT SELECT, INSERT" in init_sql,
        "数据库层为 audit_logs 提供最小权限方案（只增不改）",
        "init_db.sql 未体现 audit_logs 的最小权限设计",
    )

    # 全局搜索硬编码密钥特征
    suspicious: list[str] = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {".venv", "venv", "__pycache__", "node_modules"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # 明显的真实密钥赋值（排除示例占位符与测试）
        for pattern in (
            r"(?i)(api_key|access_key|secret_key|password)\s*=\s*[\"'](?!replace_me|demo|test|example|xxx|\*\*\*)[A-Za-z0-9+/=]{24,}[\"']",
        ):
            for match in re.finditer(pattern, text):
                suspicious.append(f"{path.relative_to(REPO_ROOT)}: {match.group(0)[:60]}")
    check(
        not suspicious,
        "源码中未发现硬编码的长密钥",
        f"疑似硬编码密钥：{suspicious[:5]}",
    )


# ---------------------------------------------------------------------------
# 4. 数据集与需求 §9.1
# ---------------------------------------------------------------------------
def check_dataset(verbose: bool) -> None:
    print("\n[4] 演示数据集（需求 §9.1）")
    print("-" * 66)

    items_path = REPO_ROOT / "algorithm" / "data" / "demo_items.json"
    matches_path = REPO_ROOT / "algorithm" / "data" / "demo_matches.json"

    if not items_path.is_file() or not matches_path.is_file():
        fail("演示数据集文件缺失")
        return

    try:
        items = json.loads(items_path.read_text(encoding="utf-8"))
        matches = json.loads(matches_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"演示数据集不是合法 JSON：{type(exc).__name__}")
        return

    lost = [i for i in items if i.get("post_type") == "lost"]
    found = [i for i in items if i.get("post_type") == "found"]
    labels = [m.get("label") for m in matches]

    check(len(items) >= 60, f"物品记录 {len(items)} 条 ≥ 60", f"物品记录 {len(items)} 条 < 60")
    check(len(lost) >= 30, f"失物记录 {len(lost)} 条 ≥ 30", f"失物记录 {len(lost)} 条 < 30")
    check(len(found) >= 30, f"拾物记录 {len(found)} 条 ≥ 30", f"拾物记录 {len(found)} 条 < 30")
    check(
        labels.count("positive") >= 20,
        f"正匹配样本 {labels.count('positive')} 组 ≥ 20",
        f"正匹配样本 {labels.count('positive')} 组 < 20",
    )
    negatives = labels.count("negative") + labels.count("hard_negative")
    check(
        negatives >= 20,
        f"负匹配样本 {negatives} 组 ≥ 20（其中 hard_negative {labels.count('hard_negative')} 组）",
        f"负匹配样本 {negatives} 组 < 20",
    )
    check(
        labels.count("hard_negative") >= 8,
        f"hard_negative 样本 {labels.count('hard_negative')} 组 ≥ 8",
        f"hard_negative 样本 {labels.count('hard_negative')} 组 < 8（区分度不足）",
    )

    categories = {i.get("category") for i in items}
    required_categories = {"无线耳机", "水杯", "书籍", "校园卡", "雨伞", "背包"}
    uncovered = required_categories - categories
    check(
        not uncovered,
        f"类别覆盖完整（{len(categories)} 类）",
        f"未覆盖类别：{uncovered}",
    )

    no_hidden = [i.get("id") for i in items if not i.get("hidden_features")]
    check(
        not no_hidden,
        "全部记录都带有隐藏特征（认领验证可用）",
        f"{len(no_hidden)} 条记录缺少隐藏特征",
    )

    # 证件类必须为虚构信息
    card_items = [i for i in items if i.get("category") == "校园卡"]
    leaked_real = [
        i.get("id")
        for i in card_items
        if re.search(r"1[3-9]\d{9}", json.dumps(i, ensure_ascii=False))
    ]
    check(
        not leaked_real,
        f"校园卡类记录（{len(card_items)} 条）未包含真实手机号",
        f"校园卡记录疑似包含真实手机号：{leaked_real[:5]}",
    )

    if verbose:
        print(f"    样本标签分布：{ {l: labels.count(l) for l in set(labels)} }")


# ---------------------------------------------------------------------------
# 5. Docker 与部署
# ---------------------------------------------------------------------------
def check_deployment(verbose: bool) -> None:
    print("\n[5] 部署文件")
    print("-" * 66)

    compose = read(REPO_ROOT / "docker-compose.yml")
    for service in ("mysql", "redis", "algorithm", "backend", "frontend"):
        check(f"  {service}:" in compose, f"docker-compose 定义服务 {service}", f"docker-compose 缺少服务 {service}")

    check(
        "internal: true" in compose,
        "内部网络已启用隔离（数据库/Redis/算法不暴露公网）",
        "docker-compose 未启用内网隔离",
    )
    check(
        "${MYSQL_PASSWORD" in compose or "$MYSQL_PASSWORD" in compose,
        "数据库密码通过环境变量注入",
        "docker-compose 中数据库密码未走环境变量",
    )

    nginx = read(REPO_ROOT / "deploy" / "nginx.conf")
    check("try_files" in nginx, "Nginx 配置了 SPA history 路由回退", "Nginx 缺少 SPA 路由回退")
    check("client_max_body_size" in nginx, "Nginx 配置了上传体积上限", "Nginx 未配置上传体积上限")

    env_example = read(REPO_ROOT / ".env.example")
    for var in ("APP_SECRET_KEY", "DATABASE_URL", "REDIS_URL", "OBS_ACCESS_KEY", "MAAS_API_KEY"):
        check(f"{var}=" in env_example, f".env.example 含 {var}", f".env.example 缺少 {var}")

    real_secret = re.findall(
        r"(?i)^(?:OBS_SECRET_KEY|OBS_ACCESS_KEY|MAAS_API_KEY|APP_SECRET_KEY|MYSQL_PASSWORD|REDIS_PASSWORD)=(.+)$",
        env_example,
        re.MULTILINE,
    )
    bad = [v for v in real_secret if v.strip() and "replace_me" not in v and not v.startswith("dev_only")]
    check(not bad, ".env.example 中所有密钥均为占位符", f".env.example 含真实值：{bad}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="M0 产物自检")
    parser.add_argument("--verbose", action="store_true", help="输出更多细节")
    args = parser.parse_args()

    print("=" * 66)
    print("  拾光 · M0 产物自检")
    print(f"  仓库：{REPO_ROOT}")
    print("=" * 66)

    check_files(args.verbose)
    check_contract(args.verbose)
    check_security(args.verbose)
    check_dataset(args.verbose)
    check_deployment(args.verbose)

    print("\n" + "=" * 66)
    print(f"  通过 {len(PASS)} 项 ｜ 警告 {len(WARN)} 项 ｜ 失败 {len(FAIL)} 项")
    print("=" * 66)

    if args.verbose or FAIL:
        if PASS and args.verbose:
            print("\n  ✓ 通过项：")
            for msg in PASS:
                print(f"    · {msg}")
        if WARN:
            print("\n  ! 警告：")
            for msg in WARN:
                print(f"    · {msg}")
        if FAIL:
            print("\n  ✗ 失败项：")
            for msg in FAIL:
                print(f"    · {msg}")

    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
