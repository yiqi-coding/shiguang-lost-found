"""物品业务编排服务（契约 §3.3 ~ §3.7、§3.14）。

职责边界：

- **本模块只做编排**：字段级权限一律交给 `app/services/serializers.py`；
  状态变更一律交给 `app/services/state_machine.py`；审计一律交给 `app/services/audit_service.py`；
- 发布流程的降级红线（契约 §3.3）：
  - MaaS/算法不可用 → 物品**照常创建成功**，`ai_status="failed"`，仅提示手动补充属性；
  - 存储不可用 → **含图请求**抛 `StorageServiceError`（502），**无图请求**正常创建；
- 物品任何变更（创建、AI 属性回填、状态流转）之后都必须调用 `cache.invalidate_item(item_id)`，
  否则会出现「详情页已更新、匹配结果还是旧的」这类脏缓存问题。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Select, and_, cast, func, or_, select
from sqlalchemy import String as SAString
from sqlalchemy.orm import Session

from app.clients.algorithm_client import get_algorithm_client
from app.core.config import settings
from app.core.enums import AIStatus, ItemStatus, PostType
from app.core.errors import AlgorithmServiceError, ItemNotFoundError, StorageServiceError
from app.models.item import Item, ItemImage
from app.models.match import Match
from app.models.user import User
from app.schemas.item import ItemCreate
from app.services import attribute_service, audit_service, serializers
from app.services.attribute_service import AttributeResult
from app.services.cache import cache
from app.services.file_service import validate_image, validate_image_count
from app.services.scoring import (
    build_risk_tips,
    brand_model_score,
    category_score,
    clamp,
    get_weights,
    is_above_display_threshold,
    location_score,
    time_score,
    weighted_total,
)
from app.services.state_machine import assert_item_transition, can_transition_item
from app.services.storage import get_storage, get_signed_url, save_image

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 输入/输出数据结构
# ----------------------------------------------------------------------
@dataclass
class IncomingImage:
    """路由层读取到的上传图片（已读出字节，避免在服务层触碰 `UploadFile`）。"""

    filename: str | None
    content_type: str | None
    data: bytes


@dataclass
class ItemFilters:
    """列表查询过滤条件（契约 §3.4）。"""

    post_type: str | None = None
    category: str | None = None
    statuses: list[str] = field(default_factory=list)
    keyword: str | None = None
    location: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None
    owner_user_id: int | None = None
    sort_by: str = "created_at"
    order: str = "desc"


@dataclass
class MatchComputeOutcome:
    """一次匹配重算的结果摘要（供路由组装响应）。"""

    query_item_id: int
    model_version: str
    degraded: bool
    computed: int
    results: list[Match] = field(default_factory=list)
    task_id: str = ""


@dataclass
class MatchListOutcome:
    """匹配结果读取结果（含缓存/降级标记）。"""

    query_item_id: int
    model_version: str
    computed_at: datetime | None
    cached: bool
    degraded: bool
    results: list[tuple[Match, Item]] = field(default_factory=list)


# ----------------------------------------------------------------------
# 发布
# ----------------------------------------------------------------------
def create_item(
    db: Session,
    *,
    owner: User,
    payload: ItemCreate,
    images: list[IncomingImage] | None = None,
) -> tuple[Item, AttributeResult]:
    """发布物品（契约 §3.3）。

    执行顺序：**图片校验 → 图片保存 → 建 Item/ItemImage → 触发 AI 属性提取（可降级）**。

    :raises ValidationError: 图片校验失败（400 / 413）
    :raises StorageServiceError: 含图且存储不可用（502），此时提示用户去掉图片重试
    """
    incoming = images or []
    validate_image_count(len(incoming))  # 契约：单条记录最多 3 张

    # ---------- 1) 全量校验（先校验再落盘，避免半途失败留下垃圾对象）----------
    validated = [
        validate_image(image.data, filename=image.filename, declared_mime=image.content_type)
        for image in incoming
    ]

    # ---------- 2) 保存图片（失败则回滚已保存对象并抛 502）----------
    saved_keys: list[str] = []
    try:
        for image in validated:
            saved_keys.append(save_image(image.data, image.ext, image.mime_type))
    except StorageServiceError:
        _cleanup_objects(saved_keys)
        raise
    except Exception as exc:  # noqa: BLE001 - 存储层任何异常都按 502 处理，绝不 500
        _cleanup_objects(saved_keys)
        logger.error("图片保存失败：%s", type(exc).__name__)
        raise StorageServiceError("图片保存失败，请去掉图片后重试或稍后再试") from exc

    # ---------- 3) 建物品与图片记录 ----------
    item = Item(**payload.to_item_kwargs(owner_user_id=owner.id))
    item.ai_status = AIStatus.PENDING
    db.add(item)
    db.flush()  # 取得 item.id

    for index, image in enumerate(validated):
        db.add(
            ItemImage(
                item_id=item.id,
                object_key=saved_keys[index],
                mime_type=image.mime_type,
                size_bytes=image.size,
                width=image.width,
                height=image.height,
                sort_order=index,
                is_primary=1 if index == 0 else 0,
            )
        )

    audit_service.log_item_create(
        db,
        item_id=item.id,
        operator=owner,
        post_type=item.post_type,
        ai_status=item.ai_status,  # 此时为 pending，AI 结果稍后回填
    )
    db.commit()
    db.refresh(item)

    # ---------- 4) AI 属性提取（失败绝不阻断发布）----------
    attribute_result = attribute_service.extract_attributes(db, item=item, commit=True)

    # 物品内容已变化 → 清理其匹配/AI 缓存
    cache.invalidate_item(item.id)
    logger.info(
        "物品发布完成 item_id=%s owner=%s post_type=%s ai_status=%s images=%s",
        item.id,
        owner.id,
        item.post_type,
        attribute_result.ai_status,
        len(saved_keys),
    )
    return item, attribute_result


def publish_result_data(item: Item, *, owner: User) -> dict[str, Any]:
    """组装 `POST /api/v1/items` 的 201 `data`（契约 §3.3，字段逐字对齐）。

    ⚠ 字段值**全部来自 `serializers.serialize_item()`**（发布者本人视角），
    路由层不得直接读取 `item.hidden_features` 等敏感属性。
    图片的 `key` 只对发布者本人与管理员可见（`serialize_item` 决定），
    因此本函数必须由「发布者本人视角」调用。
    """
    serialized = serializers.serialize_item(item, viewer=owner, detail=True)
    images: list[dict[str, Any]] = []
    for entry in serialized.get("image_keys") or []:
        images.append(
            {
                "key": entry.get("key"),
                "url": get_signed_url(entry.get("key")),
                "size": entry.get("size"),
            }
        )
    return {
        "id": serialized["id"],
        "post_type": serialized["post_type"],
        "status": serialized["status"],
        "ai_status": serialized["ai_status"],
        "images": images,
        "ai_attributes": serialized.get("ai_attributes"),
    }


def extract_attributes_for_route(db: Session, *, item: Item, force: bool = True) -> AttributeResult:
    """`POST /items/{item_id}/extract-attributes` 的服务入口（契约 §3.14）。

    MaaS/算法不可用时返回 `ai_status=failed` 的降级结果，**物品本身不受影响**；
    是否需要返回 502 由路由依据 `AI_SERVICE_ERROR` 契约决定（见路由实现）。
    """
    return attribute_service.extract_attributes(db, item=item, force=force, commit=True)


def _cleanup_objects(object_keys: list[str]) -> None:
    """回滚已上传但未落库的对象（尽力而为，失败仅告警）。"""
    if not object_keys:
        return
    try:
        storage = get_storage()
        for key in object_keys:
            storage.delete(key)
        cache_keys = len(object_keys)
        logger.warning("已回滚 %s 个未落库的图片对象", cache_keys)
    except Exception as exc:  # noqa: BLE001
        logger.warning("回滚图片对象失败（忽略）：%s", type(exc).__name__)


# ----------------------------------------------------------------------
# 查询
# ----------------------------------------------------------------------
def get_item_or_404(db: Session, item_id: int) -> Item:
    """按 ID 取物品，不存在抛 `404 ITEM_NOT_FOUND`。"""
    item = db.get(Item, item_id)
    if item is None:
        raise ItemNotFoundError("物品记录不存在")
    return item


def list_items(db: Session, *, filters: ItemFilters, offset: int, limit: int) -> tuple[list[Item], int]:
    """物品列表（契约 §3.4）：筛选 + 分页 + 关键词，返回 (当页数据, 总数)。"""
    conditions = _build_conditions(filters)

    count_stmt = select(func.count()).select_from(Item)
    stmt: Select = select(Item)
    for condition in conditions:
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = int(db.execute(count_stmt).scalar_one() or 0)

    stmt = stmt.order_by(*_order_by(filters)).offset(offset).limit(limit)
    rows = db.execute(stmt).scalars().unique().all()
    return list(rows), total


def _build_conditions(filters: ItemFilters) -> list[Any]:
    """把过滤条件翻译成 SQLAlchemy 条件列表（关键字匹配与仓库无关）。"""
    conditions: list[Any] = []
    if filters.post_type:
        conditions.append(Item.post_type == filters.post_type)
    if filters.category:
        conditions.append(Item.category == filters.category)
    if filters.statuses:
        conditions.append(Item.status.in_(filters.statuses))
    if filters.location:
        conditions.append(Item.location.like(f"%{filters.location}%"))
    if filters.time_from is not None:
        conditions.append(Item.event_time >= filters.time_from)
    if filters.time_to is not None:
        conditions.append(Item.event_time <= filters.time_to)
    if filters.owner_user_id is not None:
        conditions.append(Item.owner_user_id == filters.owner_user_id)
    if filters.keyword:
        keyword = filters.keyword.strip()
        if keyword:
            like = f"%{keyword}%"
            # 契约 §3.4：在 title + description + ai_attributes.keywords 中模糊匹配。
            # JSON 列无法跨库统一做 JSON_CONTAINS，这里退化为「整体文本 LIKE」，
            # 命中率等价（keywords 是 JSON 数组，序列化后仍包含关键词文本）。
            conditions.append(
                or_(
                    Item.title.like(like),
                    Item.description.like(like),
                    cast(Item.ai_attributes, SAString).like(like),
                )
            )
    return conditions


def _order_by(filters: ItemFilters) -> list[Any]:
    """排序：白名单列 + asc/desc（契约 §1.1，默认 created_at desc）。"""
    columns = {"created_at": Item.created_at, "event_time": Item.event_time, "id": Item.id}
    column = columns.get(filters.sort_by, Item.created_at)
    return [column.asc() if str(filters.order).lower() == "asc" else column.desc()]


# ----------------------------------------------------------------------
# 匹配：候选召回 / 打分 / 落库 / 缓存 / 审计
# ----------------------------------------------------------------------
def find_candidates(db: Session, item: Item, *, limit: int = 50, pool_size: int = 200) -> list[Item]:
    """结构化候选召回（契约 §4.1：后端先做结构化过滤再把候选集交给算法服务）。

    规则：只取**相反 post_type**、且未关闭/未暂停的记录；
    再按「类别是否一致 → 事件时间接近度」在内存中排序（避免依赖 MySQL 专有函数，
    保证单元测试可用 SQLite 运行）。
    """
    opposite = PostType.FOUND if item.post_type == PostType.LOST else PostType.LOST
    stmt = (
        select(Item)
        .where(Item.id != item.id)
        .where(Item.post_type == opposite)
        .where(Item.status.notin_([ItemStatus.CLOSED, ItemStatus.SUSPENDED]))
        .order_by(Item.event_time.desc(), Item.id.desc())
        .limit(pool_size)
    )
    pool = list(db.execute(stmt).scalars().unique().all())
    pool.sort(
        key=lambda candidate: (
            0 if candidate.category == item.category else 1,
            _seconds_between(candidate.event_time, item.event_time),
        )
    )
    return pool[:limit]


def _seconds_between(time_a: datetime | None, time_b: datetime | None) -> float:
    """两个时间的绝对秒差；任一为空时返回一个很大的值（排在最后）。"""
    if time_a is None or time_b is None:
        return float("inf")
    return abs((time_a - time_b).total_seconds())


def build_query_payload(item: Item) -> dict[str, Any]:
    """构造算法服务 `query_item` / `candidate_items` 元素（契约 §4.1）。"""
    return {
        "id": item.id,
        "image_url": get_signed_url(item.primary_image.object_key) if item.primary_image else None,
        "category": item.category,
        "description": item.description,
        "attributes": {
            "color": item.color,
            "brand": item.brand,
            "features": [str(f) for f in (item.public_features or [])],
        },
        "location": item.location,
        "event_time": _iso(item.event_time),
    }


def recompute_matches(
    db: Session,
    *,
    item: Item,
    actor: User | None,
    force: bool = True,
    top_k: int | None = None,
) -> MatchComputeOutcome:
    """重新计算匹配的完整编排（契约 §3.7）。

    步骤：召回候选 → 调算法服务（失败降级为本地规则打分）→ UPSERT `matches`
    → 按需流转物品状态（published → matched）→ 写缓存 → 写审计。
    """
    top_k = top_k or settings.MATCH_TOP_K
    candidates = find_candidates(db, item)
    candidate_payloads = [build_query_payload(candidate) for candidate in candidates]

    degraded = False
    model_version = "baseline-1.0"
    raw_results: list[dict[str, Any]] = []

    try:
        response = get_algorithm_client().match(build_query_payload(item), candidate_payloads)
        model_version = str(response.get("model_version") or model_version)
        raw_results = [r for r in (response.get("results") or []) if isinstance(r, dict)]
    except AlgorithmServiceError as exc:
        if not settings.ALGORITHM_FALLBACK_ENABLED:
            raise
        degraded = True
        model_version = "rule-fallback"
        logger.warning("算法服务不可用，降级为本地规则匹配：%s", exc.message)
        raw_results = _fallback_match(item, candidates)

    if degraded is False and not raw_results and candidates:
        # 算法服务返回空结果：不做本地兜底（尊重算法结论），仅记录
        logger.info("算法服务返回空结果 item_id=%s candidates=%s", item.id, len(candidates))

    weights = get_weights()
    candidate_by_id = {candidate.id: candidate for candidate in candidates}
    computed = 0

    for result in raw_results:
        candidate_id = result.get("candidate_id")
        candidate = candidate_by_id.get(int(candidate_id)) if candidate_id is not None else None
        if candidate is None:
            # 算法返回了不在候选集内的 ID：忽略（防越权读取非候选物品）
            logger.warning("算法返回了非候选集内的 candidate_id=%s，已忽略", candidate_id)
            continue
        scores = _extract_scores(result)
        if scores is None:
            continue
        match = _upsert_match(
            db,
            query_item=item,
            candidate=candidate,
            scores=scores,
            evidence=[str(x) for x in (result.get("evidence") or [])][:8],
            uncertainties=[str(x) for x in (result.get("uncertainties") or [])][:8],
            model_version=model_version,
            degraded=degraded,
            weights=weights,
        )
        computed += 1
        _ = match

    # 有可展示的候选 → 物品从 published 进入 matched（状态机集中流转，禁止直接赋值）
    best = _best_score(db, item)
    if (
        best is not None
        and is_above_display_threshold(best)
        and can_transition_item(item.status, ItemStatus.MATCHED)
    ):
        old_status = item.status
        assert_item_transition(old_status, ItemStatus.MATCHED)
        item.status = ItemStatus.MATCHED
        db.add(item)
        audit_service.log_item_status_change(
            db,
            item_id=item.id,
            operator=actor,
            from_status=old_status,
            to_status=ItemStatus.MATCHED,
            reason="匹配计算发现高于展示阈值的候选",
        )

    audit_service.log_match_compute(
        db,
        item_id=item.id,
        operator=actor,
        model_version=model_version,
        candidate_count=len(candidates),
        degraded=degraded,
    )
    db.commit()
    db.refresh(item)

    # 物品内容/状态已变化 → 先清理旧缓存，再写入本次计算结果
    version = match_cache_version(item)
    cache.invalidate_item(item.id)
    top_results = _top_matches(db, item, top_k=top_k, min_score=0.0)
    _write_match_cache(item, version=version, model_version=model_version, degraded=degraded, results=top_results)

    logger.info(
        "匹配重算完成 item_id=%s candidates=%s computed=%s degraded=%s",
        item.id,
        len(candidates),
        computed,
        degraded,
    )
    return MatchComputeOutcome(
        query_item_id=item.id,
        model_version=model_version,
        degraded=degraded,
        computed=computed,
        results=[match for match, _candidate in top_results],
        task_id=f"task_{uuid.uuid4().hex[:8]}",
    )


def get_matches(
    db: Session,
    *,
    item: Item,
    top_k: int,
    min_score: float,
    refresh: bool = False,
) -> MatchListOutcome:
    """读取匹配结果（契约 §3.6）。

    缓存策略：缓存的是**计算结果**（命中则 `cached=true`）；
    物品详情与候选物品摘要始终从数据库实时读取，保证权限与数据新鲜度。
    """
    version = match_cache_version(item)
    cached_payload = None if refresh else cache.get_json(cache.match_key(item.id, version))

    if isinstance(cached_payload, dict) and cached_payload.get("results"):
        rows = _load_matches_by_ids(db, item, [int(r["match_id"]) for r in cached_payload["results"]])
        results = _filter_and_sort(rows, item, top_k=top_k, min_score=min_score)
        if results:
            return MatchListOutcome(
                query_item_id=item.id,
                model_version=str(cached_payload.get("model_version") or "baseline-1.0"),
                computed_at=_parse_dt(cached_payload.get("computed_at")),
                cached=True,
                degraded=bool(cached_payload.get("degraded")),
                results=results,
            )

    rows = _top_matches(db, item, top_k=max(top_k, settings.MATCH_TOP_K), min_score=min_score)
    results = _filter_and_sort(rows, item, top_k=top_k, min_score=min_score)
    model_version = results[0][0].model_version if results else "baseline-1.0"
    degraded = bool(results[0][0].is_degraded) if results else False
    computed_at = None
    if results:
        computed_at = results[0][0].updated_at or results[0][0].created_at

    if results:
        _write_match_cache(
            item, version=version, model_version=model_version, degraded=degraded, results=results
        )
    return MatchListOutcome(
        query_item_id=item.id,
        model_version=model_version,
        computed_at=computed_at,
        cached=False,
        degraded=degraded,
        results=results,
    )


def has_matches(db: Session, item: Item) -> bool:
    """是否存在该物品的匹配记录（用于 `MATCH_NOT_FOUND` 判定）。"""
    stmt = (
        select(func.count())
        .select_from(Match)
        .where(or_(Match.lost_item_id == item.id, Match.found_item_id == item.id))
    )
    return int(db.execute(stmt).scalar_one() or 0) > 0


# ----------------------------------------------------------------------
# 匹配内部工具
# ----------------------------------------------------------------------
def match_cache_version(item: Item) -> str:
    """匹配缓存版本号：物品内容指纹（内容变化即产生新 Key）。"""
    return attribute_service.compute_content_hash(item)


def _extract_scores(result: dict[str, Any]) -> dict[str, float] | None:
    """把算法返回的 `scores` 映射为六维分数。

    ⚠ 契约 §3.6 明确：算法侧 `text_score` 与后端 `attribute_score` 是同一维度，
    此处做**唯一一次**改名映射，两侧均不得再改字段名。
    """
    scores = result.get("scores")
    if not isinstance(scores, dict):
        return None
    return {
        "category_score": clamp(float(scores.get("category_score", 0.0) or 0.0)),
        "attribute_score": clamp(float(scores.get("text_score", scores.get("attribute_score", 0.0)) or 0.0)),
        "image_score": clamp(float(scores.get("image_score", 0.0) or 0.0)),
        "time_score": clamp(float(scores.get("time_score", 0.0) or 0.0)),
        "location_score": clamp(float(scores.get("location_score", 0.0) or 0.0)),
        "brand_model_score": clamp(float(scores.get("brand_model_score", 0.0) or 0.0)),
    }


def _upsert_match(
    db: Session,
    *,
    query_item: Item,
    candidate: Item,
    scores: dict[str, float],
    evidence: list[str],
    uncertainties: list[str],
    model_version: str,
    degraded: bool,
    weights: dict[str, float],
) -> Match:
    """UPSERT 一条匹配记录（契约 §2 约束：同一对 (lost, found) 只保留一行）。"""
    if query_item.post_type == PostType.LOST:
        lost_item_id, found_item_id = query_item.id, candidate.id
    else:
        lost_item_id, found_item_id = candidate.id, query_item.id

    stmt = select(Match).where(
        and_(Match.lost_item_id == lost_item_id, Match.found_item_id == found_item_id)
    )
    match = db.execute(stmt).scalar_one_or_none()
    if match is None:
        match = Match(lost_item_id=lost_item_id, found_item_id=found_item_id)
        db.add(match)

    match.category_score = scores["category_score"]
    match.attribute_score = scores["attribute_score"]
    match.image_score = scores["image_score"]
    match.time_score = scores["time_score"]
    match.location_score = scores["location_score"]
    match.brand_model_score = scores["brand_model_score"]
    match.evidence = evidence
    match.uncertainties = uncertainties
    match.model_version = model_version
    match.is_degraded = 1 if degraded else 0
    match.weights_snapshot = weights
    match.recompute_total(weights)  # 冻结方法：保证 total_score == 六维加权和
    match.risk_tips = build_risk_tips(
        category=query_item.category,
        total=float(match.total_score or 0),
        evidence=evidence,
        uncertainties=uncertainties,
    )
    return match


def _fallback_match(query: Item, candidates: list[Item]) -> list[dict[str, Any]]:
    """算法服务不可用时的本地规则打分（`degraded=true`，契约 §3.6）。

    仅使用 `app/services/scoring.py` 中冻结的规则，不使用任何随机量，
    保证「同一输入必得同一输出」（Mock 硬性要求 §4.4）。
    """
    query_text = query.to_search_text()
    results: list[dict[str, Any]] = []
    for candidate in candidates:
        cat_score = category_score(query.category, candidate.category)
        time_value, time_note = time_score(query.event_time, candidate.event_time)
        location_value, location_note = location_score(query.location_parsed, candidate.location_parsed)
        brand_value, brand_note = brand_model_score(query.brand, candidate.brand)
        attribute_value = _dice_similarity(query_text, candidate.to_search_text())

        evidence = [f"物品类别一致（{query.category}）" if cat_score >= 1.0 else f"类别相关度 {cat_score:.2f}"]
        uncertainties = ["算法服务不可用，当前为本地规则降级结果", "图像相似度不可用，按中性分 0.50 处理"]
        if time_note:
            evidence.append(time_note)
        if location_note:
            evidence.append(location_note)
        if brand_note:
            (evidence if brand_value >= 0.5 else uncertainties).append(brand_note)

        results.append(
            {
                "candidate_id": candidate.id,
                "evidence": evidence[:8],
                "uncertainties": uncertainties[:8],
                "scores": {
                    "category_score": cat_score,
                    "text_score": attribute_value,
                    "image_score": 0.50,  # 无图像模型 → 中性分
                    "time_score": time_value,
                    "location_score": location_value,
                    "brand_model_score": brand_value,
                },
            }
        )

    results.sort(key=lambda item: float(weighted_total(item["scores"])), reverse=True)
    return results


def _dice_similarity(text_a: str, text_b: str, n: int = 2) -> float:
    """字符 n-gram Dice 相似度（确定性、无依赖，用于降级文本分）。"""
    a = "".join((text_a or "").split())
    b = "".join((text_b or "").split())
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[i : i + n] for i in range(len(a) - n + 1)}
    grams_b = {b[i : i + n] for i in range(len(b) - n + 1)}
    if not grams_a or not grams_b:
        return 0.0
    overlap = len(grams_a & grams_b)
    return clamp(2 * overlap / (len(grams_a) + len(grams_b)))


def _top_matches(
    db: Session, item: Item, *, top_k: int, min_score: float
) -> list[tuple[Match, Item]]:
    """按总分倒序取该物品的匹配（自动识别查询物品是失物还是拾物）。"""
    stmt = (
        select(Match)
        .where(or_(Match.lost_item_id == item.id, Match.found_item_id == item.id))
        .where(Match.total_score >= min_score)
        .order_by(Match.total_score.desc(), Match.id.asc())
        .limit(top_k)
    )
    rows = db.execute(stmt).scalars().unique().all()
    pairs: list[tuple[Match, Item]] = []
    for match in rows:
        candidate = _candidate_of(match, item)
        if candidate is not None:
            pairs.append((match, candidate))
    return pairs


def _load_matches_by_ids(db: Session, item: Item, match_ids: list[int]) -> list[tuple[Match, Item]]:
    """按缓存中的 match_id 列表回表读取（保证权限与数据实时性）。"""
    if not match_ids:
        return []
    stmt = select(Match).where(Match.id.in_(match_ids))
    rows = db.execute(stmt).scalars().unique().all()
    pairs = [(match, _candidate_of(match, item)) for match in rows]
    pairs = [(match, candidate) for match, candidate in pairs if candidate is not None]
    order = {match_id: index for index, match_id in enumerate(match_ids)}
    pairs.sort(key=lambda pair: order.get(pair[0].id, len(order)))
    return pairs


def _candidate_of(match: Match, item: Item) -> Item | None:
    """匹配结果中的「对方物品」（查询物品为失物则候选是拾物，反之亦然）。"""
    if match.lost_item_id == item.id:
        return match.found_item
    if match.found_item_id == item.id:
        return match.lost_item
    return None


def _filter_and_sort(
    rows: list[tuple[Match, Item]], item: Item, *, top_k: int, min_score: float
) -> list[tuple[Match, Item]]:
    filtered = [
        (match, candidate)
        for match, candidate in rows
        if float(match.total_score or 0) >= min_score
    ]
    filtered.sort(key=lambda pair: float(pair[0].total_score or 0), reverse=True)
    return filtered[:top_k]


def _best_score(db: Session, item: Item) -> float | None:
    stmt = (
        select(func.max(Match.total_score))
        .where(or_(Match.lost_item_id == item.id, Match.found_item_id == item.id))
    )
    value = db.execute(stmt).scalar_one_or_none()
    return float(value) if value is not None else None


def _write_match_cache(
    item: Item,
    *,
    version: str,
    model_version: str,
    degraded: bool,
    results: list[tuple[Match, Item]],
) -> None:
    """写入匹配结果缓存（TTL 10min，契约 §5）。仅存 ID 与分数，不含任何敏感字段。"""
    if not settings.MATCH_CACHE_ENABLED:
        return
    cache.set_json(
        cache.match_key(item.id, version),
        {
            "query_item_id": item.id,
            "model_version": model_version,
            "degraded": degraded,
            "computed_at": _iso(datetime.now(timezone.utc).replace(tzinfo=None)),
            "results": [
                {"match_id": match.id, "total_score": float(match.total_score or 0)}
                for match, _candidate in results
            ],
        },
        settings.REDIS_TTL_MATCH,
    )


def serialize_match_result(match: Match, *, candidate: Item, viewer: User | None) -> dict[str, Any]:
    """匹配结果序列化（统一走 `serializers`，禁止在路由里手写 dict）。

    说明：当查询物品是 `found` 记录时，候选应为 `lost_item`，
    因此这里用 `serialize_match` + `serialize_item(candidate)` 组合，
    而不是固定取 `found_item` 的 `serialize_match_candidate()`。
    """
    data = serializers.serialize_match(match, viewer=viewer)
    data["candidate_item"] = serializers.serialize_item(candidate, viewer=viewer, detail=False)
    return data


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = [
    "IncomingImage",
    "ItemFilters",
    "MatchComputeOutcome",
    "MatchListOutcome",
    "create_item",
    "publish_result_data",
    "extract_attributes_for_route",
    "get_item_or_404",
    "list_items",
    "find_candidates",
    "build_query_payload",
    "recompute_matches",
    "get_matches",
    "has_matches",
    "match_cache_version",
    "serialize_match_result",
]
