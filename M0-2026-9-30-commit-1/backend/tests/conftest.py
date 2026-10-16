"""测试公共设施：环境准备 + 第三方依赖打桩 + 内存对象工厂。

**本文件必须在任何 `app.*` 导入之前被导入**（测试模块第一行 `import conftest`），
因为它负责：

1. 设定确定性的环境变量（避免读取开发者的 `.env` 造成测试漂移）；
2. 为缺失的第三方依赖安装替身（见 `tests/_stubs.py`）；
3. 提供 `make_user` / `make_item` / `make_match` / `make_claim` 等内存对象工厂
   —— **测试全程不连接数据库、不启动服务**。

用法：

    cd E:\\ICT\\backend && python -m unittest discover -s tests -v

完整运行（真实 fastapi/sqlalchemy/pydantic-settings 等）需先：
    pip install -r requirements.txt
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------
# 0) 保证 `app` 包可导入
#    （某些环境设置了 PYTHONSAFEPATH / 无 '' 路径，`python -m unittest` 不会把
#      CWD 放进 sys.path，因此这里显式加入 backend/ 目录）
# ----------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# ----------------------------------------------------------------------
# 1) 环境变量：必须在导入 app.core.config 之前设定
# ----------------------------------------------------------------------
_TEMP_STORAGE = Path(os.environ.get("TEMP", ".")) / "shiguang_test_storage"

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_SECRET_KEY", "test_only_secret_key_0123456789abcdef")
os.environ.setdefault("DATABASE_URL", "sqlite:///./_unittest_never_connected.db")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ["LOCAL_STORAGE_DIR"] = str(_TEMP_STORAGE)
os.environ.setdefault("MAAS_ENABLED", "false")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
os.environ.setdefault("ALGORITHM_FALLBACK_ENABLED", "true")

# ----------------------------------------------------------------------
# 2) 依赖打桩
# ----------------------------------------------------------------------
try:  # 兼容 `discover -s tests`（模块名为 test_xxx）
    from _stubs import INSTALLED_STUBS, install_stubs
except ImportError:  # pragma: no cover - 兼容 `discover -t .`（模块名为 tests.test_xxx）
    from tests._stubs import INSTALLED_STUBS, install_stubs  # type: ignore[no-redef]

install_stubs()

# ----------------------------------------------------------------------
# 3) 应用导入（此刻依赖已就绪）
# ----------------------------------------------------------------------
from app.core.enums import (  # noqa: E402
    AIStatus,
    Category,
    ClaimStatus,
    ItemStatus,
    PostType,
    RiskLevel,
    UserRole,
    UserStatus,
)
from app.models.claim import Claim  # noqa: E402
from app.models.item import Item, ItemImage  # noqa: E402
from app.models.match import Match  # noqa: E402
from app.models.user import User  # noqa: E402

UTC = timezone.utc

# ----------------------------------------------------------------------
# 4) 内存对象工厂（不触发懒加载：所有关系都在构造时显式赋值）
# ----------------------------------------------------------------------
_DEFAULT_TIME = datetime(2026, 9, 28, 7, 0, 0)  # 2026-09-28 15:00 +08:00


def _naive(value: datetime | None) -> datetime:
    """统一为「不带时区的 UTC」（与数据库存储一致）。"""
    value = value or _DEFAULT_TIME
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def make_user(
    *,
    user_id: int = 1,
    username: str = "demo_student",
    role: str = UserRole.STUDENT,
    status: str = UserStatus.ACTIVE,
    campus_id: str | None = "main",
    contact_id: str | None = "138****8888",
    password_hash: str | None = "hashed-secret",
) -> User:
    """构造内存 `User`（**包含** `password_hash`，用于验证序列化层不外泄）。"""
    return User(
        id=user_id,
        username=username,
        role=role,
        status=status,
        campus_id=campus_id,
        contact_id=contact_id,
        password_hash=password_hash,
        last_login_at=None,
        created_at=_naive(None),
        updated_at=_naive(None),
        items=[],
        claims=[],
    )


def make_admin(*, user_id: int = 99, username: str = "admin_demo") -> User:
    """构造管理员。"""
    return make_user(user_id=user_id, username=username, role=UserRole.ADMIN, contact_id="137****0000")


def make_item_image(
    *,
    object_key: str = "items/2026/09/abc123.jpg",
    is_primary: int = 1,
    size: int = 1024,
    mime_type: str = "image/jpeg",
) -> ItemImage:
    """构造内存 `ItemImage`。"""
    return ItemImage(
        id=1,
        item_id=1,
        object_key=object_key,
        mime_type=mime_type,
        size_bytes=size,
        width=800,
        height=600,
        sort_order=0,
        is_primary=is_primary,
        created_at=_naive(None),
    )


def make_item(
    *,
    item_id: int = 1001,
    owner: User | None = None,
    owner_user_id: int | None = None,
    post_type: str = PostType.LOST,
    category: str = Category.EARPHONE,
    title: str = "丢失一副黑色无线耳机",
    description: str = "在图书馆三楼自习时落在座位上，充电盒有一道划痕。",
    location: str = "图书馆三楼",
    event_time: datetime | None = None,
    status: str = ItemStatus.PUBLISHED,
    hidden_features: list[Any] | None = None,
    public_features: list[Any] | None = None,
    contact_id: str | None = "138****8888",
    color: str | None = "黑色",
    brand: str | None = "小米",
    ai_status: str = AIStatus.SUCCESS,
    ai_attributes: dict[str, Any] | None = None,
    images: list[ItemImage] | None = None,
    location_parsed: dict[str, Any] | None = None,
) -> Item:
    """构造内存 `Item`。

    ⚠ 直接给 `images` 赋值（列表），**不要**触发 SQLAlchemy 关系懒加载：
    `Item.images` 在真实 ORM 下是关系属性，测试中我们只做纯内存对象断言。
    """
    resolved_owner = owner
    item = Item(
        id=item_id,
        owner_user_id=owner_user_id if owner_user_id is not None else (owner.id if owner else 1),
        post_type=post_type,
        category=category,
        title=title,
        description=description,
        color=color,
        brand=brand,
        location=location,
        location_parsed=location_parsed
        if location_parsed is not None
        else {"campus": "main", "building": "图书馆", "floor": "3", "spot": None},
        event_time=_naive(event_time),
        public_features=public_features if public_features is not None else ["椭圆形充电盒"],
        hidden_features=hidden_features
        if hidden_features is not None
        else ["盒盖内侧有一道约3厘米划痕", "蓝牙名称 YiQi-AirPods"],
        contact_id=contact_id,
        ai_attributes=ai_attributes
        if ai_attributes is not None
        else {
            "category": "无线耳机",
            "color": "黑色",
            "visible_features": ["椭圆形充电盒"],
            "keywords": ["蓝牙耳机", "黑色"],
            "uncertainties": ["品牌标识不清晰"],
        },
        ai_status=ai_status,
        ai_model_version="baseline-1.0",
        ai_prompt_version="v1.2",
        ai_analyzed_at=_naive(None),
        attributes_edited_by_user=0,
        status=status,
        created_at=_naive(None),
        updated_at=_naive(None),
    )
    item.images = images if images is not None else []
    item.owner = resolved_owner if resolved_owner is not None else make_user(user_id=item.owner_user_id)
    return item


def make_lost_and_found(
    *, lost_owner: User | None = None, found_owner: User | None = None
) -> tuple[Item, Item, Match]:
    """构造一对匹配物品 + 匹配记录（认领相关测试的公共前置）。"""
    lost_owner = lost_owner or make_user(user_id=12, username="demo_student")
    found_owner = found_owner or make_user(user_id=13, username="demo_finder", contact_id="139****6666")
    lost = make_item(
        item_id=1001,
        owner=lost_owner,
        post_type=PostType.LOST,
        title="丢失一副黑色无线耳机",
        hidden_features=["盒盖内侧有一道约3厘米划痕", "蓝牙名称 YiQi-AirPods"],
    )
    found = make_item(
        item_id=2001,
        owner=found_owner,
        post_type=PostType.FOUND,
        title="在图书馆捡到黑色耳机",
        hidden_features=["耳机盒右侧有一道划痕"],
        status=ItemStatus.MATCHED,
    )
    match = make_match(lost_item=lost, found_item=found)
    lost.status = ItemStatus.MATCHED
    return lost, found, match


def make_match(
    *,
    match_id: int = 3001,
    lost_item: Item | None = None,
    found_item: Item | None = None,
    total_score: float = 0.88,
    scores: dict[str, float] | None = None,
    evidence: list[str] | None = None,
    uncertainties: list[str] | None = None,
    risk_tips: list[str] | None = None,
    model_version: str = "baseline-1.0",
    is_degraded: int = 0,
) -> Match:
    """构造内存 `Match`（六维分数 + 总分 + 可解释性字段）。"""
    lost_item = lost_item or make_item(item_id=1001)
    found_item = found_item or make_item(item_id=2001, post_type=PostType.FOUND)
    values = scores or {
        "category_score": 1.0,
        "attribute_score": 0.92,
        "image_score": 0.81,
        "time_score": 0.95,
        "location_score": 1.0,
        "brand_model_score": 0.5,
    }
    match = Match(
        id=match_id,
        lost_item_id=lost_item.id,
        found_item_id=found_item.id,
        category_score=values.get("category_score", 0.0),
        attribute_score=values.get("attribute_score", 0.0),
        image_score=values.get("image_score", 0.0),
        time_score=values.get("time_score", 0.0),
        location_score=values.get("location_score", 0.0),
        brand_model_score=values.get("brand_model_score", 0.0),
        total_score=total_score,
        evidence=evidence if evidence is not None else ["物品类别一致", "颜色一致（黑色）"],
        uncertainties=uncertainties if uncertainties is not None else ["品牌信息未确认"],
        risk_tips=risk_tips if risk_tips is not None else [],
        weights_snapshot=None,
        model_version=model_version,
        is_degraded=is_degraded,
        created_at=_naive(None),
        updated_at=_naive(None),
    )
    match.lost_item = lost_item
    match.found_item = found_item
    return match


def make_claim(
    *,
    claim_id: int = 4001,
    match: Match | None = None,
    applicant: User | None = None,
    questions: list[dict[str, Any]] | None = None,
    answers_hash: dict[str, str] | None = None,
    answer_summary: list[dict[str, Any]] | None = None,
    status: str = ClaimStatus.CLAIMING,
    attempts_used: int = 0,
    max_attempts: int = 3,
    verification_score: float | None = None,
    credibility_score: float | None = None,
    risk_level: str | None = None,
    risk_tips: list[str] | None = None,
    admin_comment: str | None = None,
    reviewed_at: datetime | None = None,
    reviewer: User | None = None,
) -> Claim:
    """构造内存 `Claim`（默认未提交验证：分数与风险字段均为 None）。"""
    match = match or make_match()
    applicant = applicant or make_user(user_id=13, username="demo_finder")
    claim = Claim(
        id=claim_id,
        match_id=match.id,
        applicant_id=applicant.id,
        questions=questions
        if questions is not None
        else [
            {
                "question_id": "q1",
                "question_text": "请描述充电盒上的特殊痕迹（位置、形状或长度）。",
                "source": "template",
                "feature_ref": 0,
                "weight": 1.0,
            },
            {
                "question_id": "q2",
                "question_text": "请填写该设备设置的蓝牙名称。",
                "source": "template",
                "feature_ref": 1,
                "weight": 1.0,
            },
        ],
        answers_hash=answers_hash if answers_hash is not None else {"q1": "sha256:deadbeef", "q2": "sha256:cafebabe"},
        answer_summary=answer_summary,
        answers_plain=None,
        verification_score=verification_score,
        credibility_score=credibility_score,
        risk_level=risk_level,
        risk_tips=risk_tips,
        attempts_used=attempts_used,
        max_attempts=max_attempts,
        status=status,
        admin_comment=admin_comment,
        reviewed_by=reviewer.id if reviewer else None,
        created_at=_naive(None),
        reviewed_at=_naive(reviewed_at) if reviewed_at else None,
    )
    claim.match = match
    claim.applicant = applicant
    claim.reviewer = reviewer
    return claim


def make_verified_claim(**kwargs: Any) -> Claim:
    """构造「已提交验证且通过」的认领（用于 §3.10.1 已提交分支）。"""
    defaults: dict[str, Any] = {
        "status": ClaimStatus.PENDING_REVIEW,
        "attempts_used": 1,
        "verification_score": 0.86,
        "credibility_score": 0.79,
        "risk_level": RiskLevel.LOW,
        "risk_tips": ["验证结果较好，可按常规流程交付并留存记录"],
        "answer_summary": [
            {"question_id": "q1", "matched": True, "score": 0.9, "reason": "关键特征吻合"},
            {"question_id": "q2", "matched": True, "score": 1.0, "reason": "设备名称完全一致"},
        ],
    }
    defaults.update(kwargs)
    return make_claim(**defaults)


def utc(*, days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    """相对基准时间偏移（测试构造 event_time 用）。"""
    return _DEFAULT_TIME + timedelta(days=days, hours=hours, minutes=minutes)


__all__ = [
    "INSTALLED_STUBS",
    "UTC",
    "make_user",
    "make_admin",
    "make_item",
    "make_item_image",
    "make_lost_and_found",
    "make_match",
    "make_claim",
    "make_verified_claim",
    "utc",
]
