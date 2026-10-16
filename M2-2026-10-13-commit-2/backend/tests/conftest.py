"""测试公共设施：环境准备 + 内存对象工厂 + 框架版本兼容工具。

**本文件必须在任何 `app.*` 导入之前被导入**（测试模块首行 `import conftest`），
因为它负责：

1. 设定确定性的环境变量（避免读取开发者的 `.env` 造成测试漂移）；
2. 校验真实第三方依赖是否可用（**不再使用依赖替身**，原因见下）；
3. 提供 `make_user` / `make_item` / `make_match` / `make_claim` 等内存对象工厂
   —— **测试全程不连接数据库、不启动服务**。

## 关于「依赖替身」的取舍（重要，勿回退）

本项目最初在无第三方依赖的机器上开发，`tests/_stubs.py` 用自制的假
fastapi / sqlalchemy / pydantic 让测试跑起来，得到「237 个测试全绿」的结论。
但拿到真实依赖（FastAPI 0.143 / Starlette 1.3 / SQLAlchemy 2.0.51 /
Pydantic 2.13）下复跑时，**暴露出 25 个失败**，其中包括：

- 路由契约测试完全失效：新版 FastAPI 把 `include_router()` 的子路由包进
  `_IncludedRouter`，替身没有这层结构，于是「接口全部存在」是假象；
- 密码哈希在真实环境下**直接抛异常**（passlib 1.7.4 无法驱动 bcrypt 5.0），
  登录接口会 500，而替身环境下测试却是绿的；
- `app.user_middleware` 元素结构、`Response.content` 等在版本间变化。

**结论：替身制造的确定性是假的，代价是掩盖真实缺陷。**
因此现在改为**强制要求真实依赖**：缺依赖时直接给出明确指引并让测试失败，
而不是退回替身。依赖获取方式（本机 pip 受沙箱限制时）：

    python scripts/vendor_deps.py          # 解包到 .vendor/
    $env:PYTHONPATH = "E:\\ICT\\.vendor"
    cd backend; python -m unittest discover -s tests

用法：

    cd E:\\ICT\\backend && python -m unittest discover -s tests -v
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
# 2) 依赖可用性校验（**不再打桩** —— 见文件头部说明）
# ----------------------------------------------------------------------
def _require_real_dependencies() -> None:
    """确认关键第三方依赖真实可用；缺失则给出明确指引并失败。

    为什么宁可直接失败也不用替身：
        替身会让「路由没挂上」「密码哈希抛异常」这类真实缺陷在测试中隐形
        —— 本项目已经因此吃过一次亏（替身全绿 → 真依赖 25 个失败）。
    """
    import importlib

    missing: list[str] = []
    for module_name in ("fastapi", "sqlalchemy", "pydantic", "pydantic_settings", "jwt", "bcrypt"):
        try:
            importlib.import_module(module_name)
        except Exception:  # noqa: BLE001
            missing.append(module_name)

    if missing:
        raise RuntimeError(
            "缺少真实第三方依赖：{missing}\n"
            "本项目的测试**必须**在真实依赖下运行（不使用替身，避免掩盖真实缺陷）。\n"
            "请任选一种方式准备依赖：\n"
            "  1) pip install -r backend/requirements.txt\n"
            "  2) 若 pip 受网络/沙箱限制：python scripts/vendor_deps.py\n"
            "     然后设置环境变量 PYTHONPATH=E:\\ICT\\.vendor 再运行测试\n"
            "  3) 在 Docker 中运行：docker compose run --rm backend python -m unittest discover -s tests\n"
            "详见 docs/troubleshooting.md 与 docs/deployment.md。".format(missing=", ".join(missing))
        )


_require_real_dependencies()

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


# ---------------------------------------------------------------------------
# 框架版本兼容工具
#
# 背景：本项目先在「依赖替身」下开发，之后才在真实 FastAPI/Starlette 上复跑，
# 结果暴露出多处**框架 API 变化**导致的假绿/假红。为免每个测试文件各自踩坑，
# 统一在此收口。实测差异：
#   1. FastAPI >= 0.116：`include_router()` 后子路由被包进 `_IncludedRouter`，
#      `app.routes` 不再是扁平的 APIRoute 列表；且 `_IncludedRouter.routes`
#      返回它自身（继承属性），直接遍历会陷入循环或漏掉全部业务路由；
#   2. 前缀分散在 `APIRouter.prefix` 与 `include_router(prefix=...)` 两处，
#      手工拼完整 URL 极易出错 —— 因此**一律以 `app.openapi()` 为真值**；
#   3. Starlette 1.x：`app.user_middleware` 的元素由 2 元组
#      `(cls, options)` 变为 3 元组 `(cls, args, kwargs)`；
#   4. Starlette 1.x：`Response.content` 被移除，只有 `.body`（bytes）。
# ---------------------------------------------------------------------------


def openapi_paths(app) -> dict[str, set[str]]:
    """返回应用**对外实际提供**的 `{路径: {方法}}`，以 OpenAPI schema 为准。

    这是契约比对的权威视图：它由 FastAPI 自己解析前缀后生成，
    且天然排除静态挂载与文档路由。
    """
    schema = app.openapi()
    result: dict[str, set[str]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        result[path] = {method.upper() for method in operations}
    return result


def iter_leaf_routes(routes=None, app=None):
    """递归展开路由树，产出 `(路由对象, 路由自身 path, 方法集合)` 三元组。

    ⚠ 产出的 path 是路由**自身**的 path（可能不含父前缀），仅适合尾段匹配；
    完整对外路径请用 `openapi_paths()`。
    """
    if routes is None:
        if app is None:
            from app.main import app as _app

            app = _app
        routes = app.routes

    for node in list(routes):
        methods = getattr(node, "methods", None)
        path = getattr(node, "path", None)
        if methods and path:
            yield node, path, methods
            continue

        # _IncludedRouter 包装：真实 router 在 original_router 上
        container = getattr(node, "original_router", None)
        if container is None:
            children = getattr(node, "routes", None)
            # 防御：routes 返回节点自身时不可下钻（否则无限递归）
            container = node if (children is not None and children is not node) else None
        if container is None:
            continue
        yield from iter_leaf_routes(getattr(container, "routes", None), app=app)


def middleware_entries(app) -> list[tuple[type, dict]]:
    """返回 `[(中间件类, 选项 kwargs)]`，兼容三种已知结构。

    实测差异：
        - FastAPI/Starlette 旧版：`app.user_middleware` 是
          `[(cls, options_dict), ...]` 的 2 元组列表；
        - Starlette 1.x：元素是 `Middleware` 数据类，形如
          `Middleware(RequestIDMiddleware)` /
          `Middleware(CORSMiddleware, allow_origins=[...], ...)`，
          **不可解包**（没有 `__len__`），要用 `.cls` 与 `.kwargs`。
    """
    entries: list[tuple[type, dict]] = []
    for item in app.user_middleware:
        # Starlette 1.x：Middleware 数据类
        cls = getattr(item, "cls", None)
        if cls is not None:
            kwargs = getattr(item, "kwargs", None)
            entries.append((cls, dict(kwargs) if isinstance(kwargs, dict) else {}))
            continue

        # 旧版：2 元组 / 3 元组
        if isinstance(item, (tuple, list)):
            if len(item) == 2:
                entries.append((item[0], dict(item[1] or {})))
            elif len(item) >= 3:
                options = item[2] if isinstance(item[2], dict) else {}
                if not options and isinstance(item[1], dict):
                    options = item[1]
                entries.append((item[0], dict(options)))
            else:  # pragma: no cover - 防御未知结构
                entries.append((item[0], {}))
            continue

        # 兜底：至少有类
        entries.append((getattr(item, "cls", item), {}))
    return entries


def middleware_classes(app) -> list[type]:
    """中间件类名列表（按注册顺序，靠后者更靠外层）。"""
    return [cls for cls, _options in middleware_entries(app)]


def json_body(response) -> dict:
    """读取响应体为 dict，兼容旧版 `.content` 与新版 `.body`。"""
    content = getattr(response, "content", None)
    if isinstance(content, dict):
        return content
    import json

    body = getattr(response, "body", None)
    if isinstance(body, (bytes, bytearray)):
        return json.loads(body.decode("utf-8"))
    return json.loads(str(body))


__all__ = [
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
