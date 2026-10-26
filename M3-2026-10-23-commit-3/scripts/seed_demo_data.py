#!/usr/bin/env python
"""演示数据导入脚本（数据库设计 §6.3）。

用途：
    把 `algorithm/data/` 下的**脱敏演示数据集**导入数据库，并预置演示账号。

用法：
    python scripts/seed_demo_data.py --all            # 账号 + 物品（推荐，答辩前执行）
    python scripts/seed_demo_data.py --users-only     # 只建演示账号
    python scripts/seed_demo_data.py --items-only     # 只导入物品
    python scripts/seed_demo_data.py --reset          # 先清空业务表再导入（⚠ 仅演示环境）
    python scripts/seed_demo_data.py --source algorithm/data/demo_items.json
    python scripts/seed_demo_data.py --dry-run        # 只校验数据，不写库

设计要点：
1. **幂等**：按 `title + post_type + owner` 判重，重复执行不会产生重复记录；
2. **不预置匹配结果**：匹配必须由系统真实计算得出（需求明确禁止"通过直接修改数据库
   模拟关键流程"）。本脚本只导入物品与账号；
3. **不写死任何密钥**：数据库连接来自环境变量 / `.env`；
4. 数据来源优先级：`algorithm` 模块（权威） → JSON 文件 → 报错退出。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径与导入准备
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
ALGORITHM_DIR = REPO_ROOT / "algorithm"

for path in (str(BACKEND_DIR), str(ALGORITHM_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _prepare_backend_import() -> None:
    """把 `app` 这个名字让回给 ``backend/app``。

    背景：``backend/app`` 与 ``algorithm/app`` 包名相同。本脚本为了让演示数据
    与算法服务同源，会先用 ``algorithm.app.mock_data`` 加载数据集，这一步会把
    ``app`` 绑到算法包上；之后再 ``from app.core.config import ...`` 就会
    命中算法包而报 ``No module named 'app.core'``。

    因此真正导入后端模块前，必须：
      1. 清掉 sys.modules 里所有 ``app`` / ``app.*`` 条目；
      2. 把 backend 目录提到 sys.path 最前面。
    """
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    backend = str(BACKEND_DIR)
    while backend in sys.path:
        sys.path.remove(backend)
    sys.path.insert(0, backend)


# ---------------------------------------------------------------------------
# 演示账号定义（密码符合比赛演示要求，**不得用于生产环境**）
# ---------------------------------------------------------------------------
DEMO_USERS: list[dict[str, Any]] = [
    {
        "username": "demo_student",
        "password": "Demo@2026",
        "role": "student",
        "campus_id": "east",
        "contact_id": "138****8888",
        "describe": "失主视角：发布失物、查看候选匹配、发起认领",
    },
    {
        "username": "demo_finder",
        "password": "Demo@2026",
        "role": "student",
        "campus_id": "east",
        "contact_id": "139****6666",
        "describe": "拾到者视角：发布拾物、处理认领申请",
    },
    {
        "username": "admin_demo",
        "password": "Admin@2026",
        "role": "admin",
        "campus_id": "east",
        "contact_id": "137****0001",
        "describe": "管理员：审核认领、查看隐藏特征与审计日志",
    },
]


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def _load_algorithm_mock_data():
    """按文件路径精确加载 `algorithm/app/mock_data.py`。

    为什么不用普通 import：`backend/app` 与 `algorithm/app` 包名相同，
    谁先进入 `sys.path` 谁生效，普通 import 会随机失败。
    """
    import importlib.util

    module_path = ALGORITHM_DIR / "app" / "mock_data.py"
    if not module_path.is_file():
        raise ImportError(f"未找到 {module_path}")

    # mock_data 内部使用相对导入（from .text_utils import ...），
    # 因此必须先把 algorithm 目录加入 sys.path，并以包名 `algorithm.app` 加载。
    if str(ALGORITHM_DIR.parent) not in sys.path:
        sys.path.insert(0, str(ALGORITHM_DIR.parent))

    init_file = ALGORITHM_DIR / "app" / "__init__.py"
    if init_file.is_file():
        return importlib.import_module("algorithm.app.mock_data")

    spec = importlib.util.spec_from_file_location("shiguang_mock_data", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_dataset(source: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """加载演示数据集。

    :return: (items, matches, meta)
    """
    if source:
        path = Path(source)
        if not path.is_absolute():
            path = REPO_ROOT / path
        if not path.is_file():
            raise SystemExit(f"✗ 指定的数据文件不存在：{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload.get("items") or payload.get("demo_items") or []
        matches = payload.get("matches") or payload.get("demo_matches") or []
        return items, matches, payload.get("meta", {})

    # 优先使用 algorithm 模块生成（保证与算法服务同源，避免数据漂移）
    #
    # ⚠ 注意：backend/ 与 algorithm/ 下都有 `app` 包名，直接 import 会冲突。
    # 因此这里用 importlib 按文件路径精确加载 `algorithm/app/mock_data.py` 所在包，
    # 避免 sys.path 顺序导致的误导入。
    try:
        module = _load_algorithm_mock_data()
        dataset = module.build_demo_dataset()
        return dataset["items"], dataset["matches"], dataset.get("meta", {})
    except Exception as exc:  # noqa: BLE001 - 回退到 JSON 文件
        print(f"  ⓘ 无法从 algorithm 模块生成数据（{type(exc).__name__}: {exc}），尝试读取 JSON 文件")

    for candidate in (
        ALGORITHM_DIR / "data" / "demo_items.json",
        REPO_ROOT / "data" / "raw" / "demo_items.json",
    ):
        if candidate.is_file():
            items = json.loads(candidate.read_text(encoding="utf-8"))
            matches_path = candidate.parent / "demo_matches.json"
            matches = json.loads(matches_path.read_text(encoding="utf-8")) if matches_path.is_file() else []
            return items, matches, {"source": str(candidate)}

    raise SystemExit(
        "✗ 未找到演示数据集。请先运行 `python algorithm/scripts/generate_demo_data.py` 生成，"
        "或使用 --source 指定文件。"
    )


def validate_dataset(items: list[dict[str, Any]], matches: list[dict[str, Any]]) -> list[str]:
    """导入前校验（需求 §9.1 的数据集要求）。返回问题列表（空表示通过）。"""
    problems: list[str] = []

    if len(items) < 60:
        problems.append(f"物品记录数 {len(items)} < 60（需求要求至少 60 条）")

    lost = [i for i in items if i.get("post_type") == "lost"]
    found = [i for i in items if i.get("post_type") == "found"]
    if len(lost) < 30:
        problems.append(f"失物记录 {len(lost)} < 30")
    if len(found) < 30:
        problems.append(f"拾物记录 {len(found)} < 30")

    labels = [m.get("label") for m in matches]
    positive = labels.count("positive")
    hard_negative = labels.count("hard_negative")
    negative = labels.count("negative")
    if positive < 20:
        problems.append(f"正匹配样本 {positive} < 20")
    if hard_negative + negative < 20:
        problems.append(f"负匹配样本 {hard_negative + negative} < 20")
    if hard_negative < 8:
        problems.append(f"hard_negative 样本 {hard_negative} < 8（区分度不足）")

    required_fields = ("id", "post_type", "category", "title", "description", "location", "event_time")
    missing = [
        f"item#{item.get('id')} 缺字段 {field}"
        for item in items
        for field in required_fields
        if not item.get(field)
    ]
    problems.extend(missing[:10])

    no_hidden = [item.get("id") for item in items if not item.get("hidden_features")]
    if no_hidden:
        problems.append(f"{len(no_hidden)} 条记录缺少 hidden_features（认领验证将无法工作）")

    categories = {item.get("category") for item in items}
    expected_categories = {"无线耳机", "水杯", "书籍", "校园卡", "雨伞", "背包"}
    uncovered = expected_categories - categories
    if uncovered:
        problems.append(f"未覆盖的类别：{'、'.join(sorted(uncovered))}")

    return problems


# ---------------------------------------------------------------------------
# 写库
# ---------------------------------------------------------------------------
def parse_event_time(value: Any) -> datetime:
    """把数据集里的时间字符串解析为 datetime。"""
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间：{value}")


def ensure_users(db, *, dry_run: bool) -> dict[str, Any]:
    """创建或更新演示账号。返回 username → User 映射。"""
    from app.core.enums import UserRole, UserStatus
    from app.core.security import hash_password
    from app.models.user import User

    users: dict[str, Any] = {}
    for spec in DEMO_USERS:
        existing = db.query(User).filter(User.username == spec["username"]).one_or_none()
        if existing:
            print(f"  · 账号已存在，跳过：{spec['username']}（{spec['role']}）")
            users[spec["username"]] = existing
            continue
        if dry_run:
            print(f"  · [dry-run] 将创建账号：{spec['username']}（{spec['role']}）")
            continue
        user = User(
            username=spec["username"],
            role=spec["role"] if isinstance(spec["role"], str) else UserRole.STUDENT,
            campus_id=spec["campus_id"],
            contact_id=spec["contact_id"],  # 已脱敏
            password_hash=hash_password(spec["password"]),
            status=UserStatus.ACTIVE,
        )
        db.add(user)
        users[spec["username"]] = user
        print(f"  ✓ 创建账号：{spec['username']}（{spec['role']}）— {spec['describe']}")
    if not dry_run:
        db.flush()
    return users


def ensure_items(db, items: list[dict[str, Any]], users: dict[str, Any], *, dry_run: bool) -> int:
    """导入物品记录。返回新增条数。"""
    from app.core.enums import AIStatus
    from app.models.item import Item

    student = users.get("demo_student")
    finder = users.get("demo_finder")
    if not student or not finder:
        raise SystemExit("✗ 演示账号缺失，无法确定物品发布者")

    created = 0
    for raw in items:
        owner = student if raw.get("post_type") == "lost" else finder

        exists = (
            db.query(Item)
            .filter(
                Item.title == raw["title"],
                Item.post_type == raw["post_type"],
                Item.owner_user_id == owner.id,
            )
            .one_or_none()
        )
        if exists:
            continue

        if dry_run:
            created += 1
            continue

        ai_attributes = raw.get("ai_attributes")
        item = Item(
            owner_user_id=owner.id,
            post_type=raw["post_type"],
            category=raw["category"],
            title=raw["title"],
            description=raw["description"],
            color=raw.get("color"),
            brand=raw.get("brand"),
            location=raw["location"],
            event_time=parse_event_time(raw["event_time"]),
            public_features=raw.get("public_features") or [],
            hidden_features=raw.get("hidden_features") or [],
            contact_id=owner.contact_id,
            ai_attributes=ai_attributes,
            ai_status=AIStatus.SUCCESS if ai_attributes else AIStatus.PENDING,
            ai_model_version=(ai_attributes or {}).get("source", "rule-based-extractor(baseline-1.0)"),
            ai_prompt_version="v1.2",
            attributes_edited_by_user=0,
        )
        db.add(item)
        created += 1
        if created % 20 == 0:
            db.flush()

    if not dry_run:
        db.flush()
    return created


def reset_business_tables(db, *, dry_run: bool) -> None:
    """⚠ 清空业务表（保留 users）。仅用于演示环境重建。"""
    if dry_run:
        print("  · [dry-run] 将清空 items / item_images / matches / claims / audit_logs")
        return
    from sqlalchemy import text

    print("  ⚠ 正在清空业务表（users 保留）…")
    for table in ("claims", "matches", "item_images", "items", "audit_logs"):
        db.execute(text(f"DELETE FROM `{table}`"))
    db.commit()
    print("  ✓ 业务表已清空")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="拾光演示数据导入")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="导入账号与物品（默认行为）")
    group.add_argument("--users-only", action="store_true", help="只创建演示账号")
    group.add_argument("--items-only", action="store_true", help="只导入物品")
    parser.add_argument("--reset", action="store_true", help="导入前清空业务表（⚠ 仅演示环境）")
    parser.add_argument("--source", default=None, help="指定数据集 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验与演练，不写数据库")
    args = parser.parse_args()

    print("=" * 66)
    print("  拾光 · 演示数据导入")
    print("=" * 66)

    # ---- 1) 加载数据集 ----
    print("\n[1/5] 加载演示数据集…")
    items, matches, meta = load_dataset(args.source)
    print(f"  ✓ 物品 {len(items)} 条，匹配样本 {len(matches)} 条")
    if meta:
        for key in ("version", "generated_at", "notice"):
            if meta.get(key):
                print(f"    {key}: {meta[key]}")

    # ---- 2) 校验数据集 ----
    print("\n[2/5] 校验数据集完整性（需求 §9.1）…")
    problems = validate_dataset(items, matches)
    if problems:
        print("  ✗ 数据集存在问题：")
        for problem in problems:
            print(f"    - {problem}")
        if not args.dry_run:
            print("\n  提示：使用 --dry-run 可在不写库的情况下反复校验。")
            return 1
    else:
        labels = [m.get("label") for m in matches]
        print(
            f"  ✓ 校验通过：正样本 {labels.count('positive')} / "
            f"困难负样本 {labels.count('hard_negative')} / 负样本 {labels.count('negative')}"
        )

    # ---- 3) 连接数据库 ----
    print("\n[3/5] 连接数据库…")
    if args.dry_run:
        print("  · [dry-run] 跳过数据库连接")
        users: dict[str, Any] = {}
        created_items = len(items)
    else:
        try:
            # 数据集可能已通过 algorithm.app.* 加载，这里先把 `app` 归还给 backend
            _prepare_backend_import()
            from app.core.config import settings
            from app.db.session import get_session_factory
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 无法导入后端模块：{exc}")
            print("    请先执行：cd backend && pip install -r requirements.txt")
            return 2

        driver = settings.DATABASE_URL.split("://", 1)[0]
        print(f"  · 数据库驱动：{driver}（连接串已隐藏）")
        try:
            db = get_session_factory()()
            db.execute(__import__("sqlalchemy").text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ 数据库连接失败：{type(exc).__name__}")
            print("    请检查 .env 中的 DATABASE_URL，并确认已执行 scripts/init_db.sql 或 alembic upgrade head")
            return 2

        try:
            if args.reset:
                reset_business_tables(db, dry_run=False)

            users = {}
            created_items = 0

            if not args.items_only:
                print("\n[4/5] 创建演示账号…")
                users = ensure_users(db, dry_run=False)

            if not args.users_only:
                print("\n[5/5] 导入物品记录…")
                if not users:
                    from app.models.user import User

                    for name in ("demo_student", "demo_finder"):
                        user = db.query(User).filter(User.username == name).one_or_none()
                        if user:
                            users[name] = user
                created_items = ensure_items(db, items, users, dry_run=False)
                print(f"  ✓ 新增物品 {created_items} 条（已存在的记录已跳过）")

            db.commit()
            print("\n  ✓ 数据已提交")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"\n  ✗ 导入失败，已回滚：{type(exc).__name__}: {exc}")
            return 1
        finally:
            db.close()

    # ---- 汇总 ----
    print("\n" + "=" * 66)
    print("  完成")
    print("=" * 66)
    print(f"  演示账号密码：demo_student / demo_finder 用 'Demo@2026'，admin_demo 用 'Admin@2026'")
    print("  ⚠ 这些账号仅用于竞赛演示，生产环境必须删除或改密。")
    print("\n  注意：匹配结果**不会**被预置。请在页面上点击「查看候选匹配」或调用")
    print("        POST /api/v1/items/{item_id}/match，让系统真实计算（验收红线要求）。")
    if args.dry_run:
        print("\n  [dry-run] 未写入任何数据。")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
