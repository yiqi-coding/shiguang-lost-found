"""AI 属性提取服务（契约 §3.14 / §4.2 / §4.3）。

调用链（**逐级降级，任何一级失败都不影响物品本身的创建**）：

1. 算法服务 `POST /algorithm/v1/extract-attributes`（契约 §4.2，唯一认可的模型入口）；
2. 算法服务不可用时尝试 MaaS（`MAAS_ENABLED=true` 才启用）；
3. 全部不可用 → **规则提取**（用关键词表从描述中抽取类别/颜色/品牌/特征），
   同时把 `items.ai_status` 标记为 `failed`，前端提示「AI 分析失败，请手动补充物品属性」。

落库字段：`ai_attributes` / `ai_status` / `ai_model_version` / `ai_prompt_version` / `ai_analyzed_at`。

缓存：`ai:attributes:item:{item_id}:{content_hash}`（TTL 24h，契约 §5）；
物品内容变化后由 `cache.invalidate_item()` 清理。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.clients.algorithm_client import get_algorithm_client
from app.clients.maas_client import PROMPT_VERSION_ATTRIBUTES, get_maas_client
from app.core.config import settings
from app.core.enums import AIStatus, Category
from app.core.errors import AlgorithmServiceError
from app.models.item import Item
from app.services.cache import cache

logger = logging.getLogger(__name__)

# 算法服务返回的属性结构版本（写入 ai_model_version 的兜底值）
DEFAULT_MODEL_VERSION = "baseline-1.0"

# ----------------------------------------------------------------------
# JSON Schema（契约 §4.3：算法输出必须经 Schema 校验，校验失败视为服务错误）
# ----------------------------------------------------------------------
ATTRIBUTE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {"type": ["string", "null"], "maxLength": 64},
        "brand": {"type": ["string", "null"], "maxLength": 64},
        "model": {"type": ["string", "null"], "maxLength": 64},
        "color": {"type": ["string", "null"], "maxLength": 32},
        "material": {"type": ["string", "null"], "maxLength": 32},
        "visible_features": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "accessories": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
        "uncertainties": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
    },
    "required": ["category", "visible_features", "keywords", "uncertainties"],
    "additionalProperties": True,
}

# Schema 中声明的属性名（供测试与文档断言使用，避免两处字段清单漂移）
ATTRIBUTE_JSON_SCHEMA_KEYS: tuple[str, ...] = tuple(ATTRIBUTE_JSON_SCHEMA["properties"].keys())

# ----------------------------------------------------------------------
# 规则降级词表（仅用于算法/MaaS 全部不可用时的兜底，禁止用于正常路径）
# ----------------------------------------------------------------------
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    Category.EARPHONE: ("无线耳机", "蓝牙耳机", "蓝牙", "airpods", "充电盒", "耳机盒", "耳塞", "耳机"),
    Category.CUP: ("保温杯", "吸管杯", "马克杯", "玻璃杯", "茶杯", "水杯", "杯子", "水壶"),
    Category.BOOK: ("专业书", "参考书", "习题集", "教材", "课本", "书本", "书籍", "笔记"),
    Category.CARD: ("校园卡", "一卡通", "学生卡", "借阅卡", "饭卡", "学生证"),
    Category.UMBRELLA: ("折叠伞", "长柄伞", "自动伞", "雨伞", "伞"),
    Category.BAG: ("双肩包", "电脑包", "旅行包", "帆布包", "书包", "背包"),
}

COLOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "黑色": ("黑色", "曜石黑", "哑光黑", "深黑", "黑", "black"),
    "白色": ("白色", "米白", "象牙白", "珍珠白", "白", "white"),
    "银色": ("银色", "银灰", "银", "silver"),
    "灰色": ("灰色", "深灰", "浅灰", "灰", "gray", "grey"),
    "蓝色": ("蓝色", "深蓝", "天蓝", "藏青", "蓝", "blue"),
    "红色": ("红色", "酒红", "大红", "红", "red"),
    "绿色": ("绿色", "墨绿", "草绿", "绿", "green"),
    "黄色": ("黄色", "米黄", "鹅黄", "黄", "yellow"),
    "粉色": ("粉色", "藕粉", "粉", "pink"),
    "紫色": ("紫色", "香芋紫", "紫", "purple"),
    "棕色": ("棕色", "咖啡色", "卡其", "驼色", "棕", "brown"),
    "透明": ("半透明", "透明", "transparent"),
}

# 品牌词表：**按「品牌 → 产品线」排序**，保证「小米 AirPods 保护壳」这类文本命中的是
# 品牌（小米）而不是产品线（AirPods）。命中即返回第一个匹配项，因此顺序即优先级。
BRAND_KEYWORDS: tuple[str, ...] = (
    "苹果", "Apple", "华为", "HUAWEI", "荣耀", "小米", "红米", "Redmi",
    "漫步者", "Edifier", "索尼", "SONY", "森海塞尔", "膳魔师", "象印", "富光",
    "得力", "晨光", "乐扣乐扣", "天堂伞", "天堂", "网易严选", "京东京造",
    "无印良品", "MUJI", "迪卡侬", "安踏", "李宁", "耐克", "斑马", "三菱",
    # ---- 产品线（优先级最低）----
    "AirPods",
)

FEATURE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[^，。；;]{0,6}(?:划痕|刮痕|划伤|刮花)", "外观损伤"),
    (r"[^，。；;]{0,6}(?:磨损|磨痕|磕碰|凹痕|裂纹|缺口|破损|裂缝)", "磨损/破损"),
    (r"[^，。；;]{0,6}(?:污渍|水渍|茶渍|斑点|褪色|变形|开线)", "污损"),
    (r"[^，。；;]{0,8}(?:贴纸|贴画|挂件|钥匙扣|挂饰|标签|吊牌)", "附着物"),
    (r"[^，。；;]{0,8}(?:logo|标志|标识|字样|姓名|名字|编号)", "标识"),
    (r"(?:圆形|椭圆形|方形|长方形|三角形|圆筒形|扁平|长条)[^，。；;]{0,8}", "形状"),
    (r"[^，。；;]{0,8}(?:图案|卡通|字母|花纹|校徽|刺绣)", "图案"),
)

ACCESSORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "充电盒": ("充电盒", "充电仓", "耳机盒", "电池仓"),
    "数据线": ("数据线", "充电线", "type-c", "typec"),
    "充电器": ("充电器", "充电头", "适配器", "插头"),
    "说明书": ("说明书", "保修卡", "使用手册"),
    "杯盖": ("杯盖", "杯塞", "吸管盖"),
    "吸管": ("吸管", "搅拌棒", "茶漏"),
    "卡套": ("卡套", "卡包", "保护套"),
    "钥匙": ("钥匙串", "门钥匙", "钥匙扣", "钥匙"),
    "伞套": ("伞套", "伞袋", "收纳袋"),
    "挂绳": ("挂绳", "挂带", "腕带", "手绳"),
}

UNCERTAINTY_MARKERS: tuple[str, ...] = (
    "疑似", "好像", "似乎", "大概", "可能", "不确定", "记不清", "应该是", "大约", "模糊", "看不清", "不清晰",
)


# ----------------------------------------------------------------------
# 结果对象
# ----------------------------------------------------------------------
@dataclass
class AttributeResult:
    """属性提取结果（同时描述「写入 items 的字段」与「给路由的提示」）。"""

    attributes: dict[str, Any] | None
    ai_status: str
    ai_model_version: str | None = None
    ai_prompt_version: str | None = None
    ai_analyzed_at: datetime | None = None
    degraded: bool = False
    message: str = "success"
    problems: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.ai_status == AIStatus.SUCCESS


# ----------------------------------------------------------------------
# 最小 JSON Schema 校验器
# ----------------------------------------------------------------------
def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True  # 未知类型不做限制（宽松以兼容算法侧扩展）


def validate_attribute_payload(payload: Any) -> tuple[bool, list[str]]:
    """按 `ATTRIBUTE_JSON_SCHEMA` 校验算法输出。

    这里实现一个**只覆盖本项目所需关键字**的最小校验器（type / properties / required /
    items / enum / maxItems / maxLength / additionalProperties），避免为一次校验引入
    额外依赖；若运行环境安装了 `jsonschema`，语义与其一致。

    :return: (是否通过, 中文问题列表)
    """
    return _validate_node(payload, ATTRIBUTE_JSON_SCHEMA, path="")


def _validate_node(node: Any, schema: dict[str, Any], *, path: str) -> tuple[bool, list[str]]:
    problems: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(node, item) for item in expected_types):
            problems.append(f"{path or '根节点'} 类型应为 {'/'.join(expected_types)}")
            return False, problems

    if node is None:
        return True, problems

    if "enum" in schema and node not in schema["enum"]:
        problems.append(f"{path or '根节点'} 取值不在允许范围内")

    if isinstance(node, str) and "maxLength" in schema and len(node) > schema["maxLength"]:
        problems.append(f"{path or '根节点'} 长度超过 {schema['maxLength']}")

    if isinstance(node, list):
        if "maxItems" in schema and len(node) > schema["maxItems"]:
            problems.append(f"{path or '根节点'} 元素数量超过 {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, element in enumerate(node):
                ok, sub = _validate_node(element, item_schema, path=f"{path}[{index}]")
                if not ok:
                    problems.extend(sub)

    if isinstance(node, dict):
        for key in schema.get("required", []):
            if key not in node:
                problems.append(f"缺少必填字段 {path + '.' if path else ''}{key}")
        properties = schema.get("properties") or {}
        for key, value in node.items():
            if key in properties:
                ok, sub = _validate_node(value, properties[key], path=f"{path + '.' if path else ''}{key}")
                if not ok:
                    problems.extend(sub)
    return not problems, problems


# ----------------------------------------------------------------------
# 规则提取（降级路径）
# ----------------------------------------------------------------------
def rule_extract_attributes(
    *,
    title: str,
    description: str,
    category: str | None = None,
    color: str | None = None,
    brand: str | None = None,
) -> dict[str, Any]:
    """用关键词表从标题/描述中抽取属性（**纯确定性**，同输入必得同输出）。

    仅在算法服务与 MaaS 均不可用时使用，结果仍满足 `ATTRIBUTE_JSON_SCHEMA`。
    """
    text = f"{title or ''} {description or ''}"
    lowered = text.lower()

    # 类别：用户填写优先，其次按关键词表匹配
    resolved_category = category or None
    if not resolved_category:
        for name, words in CATEGORY_KEYWORDS.items():
            if any(word.lower() in lowered for word in words):
                resolved_category = str(name)
                break

    # 颜色
    resolved_color = color or None
    if not resolved_color:
        for name, words in COLOR_KEYWORDS.items():
            if any(word.lower() in lowered for word in words):
                resolved_color = name
                break

    # 品牌
    resolved_brand = brand or None
    if not resolved_brand:
        for word in BRAND_KEYWORDS:
            if word.lower() in lowered:
                resolved_brand = word
                break

    # 可见特征（正则模式）
    features: list[str] = []
    for pattern, _label in FEATURE_PATTERNS:
        for match in re.findall(pattern, text):
            candidate = match.strip(" ，。；;")
            if candidate and candidate not in features:
                features.append(candidate)

    # 配件
    accessories: list[str] = []
    for name, words in ACCESSORY_KEYWORDS.items():
        if any(word.lower() in lowered for word in words):
            accessories.append(name)

    # 关键词：类别 / 颜色 / 品牌 + 特征短语（去重、限长）
    keywords: list[str] = []
    for value in (resolved_category, resolved_color, resolved_brand):
        if value and value not in keywords:
            keywords.append(str(value))
    for feature in features:
        if feature not in keywords and 2 <= len(feature) <= 12:
            keywords.append(feature)

    uncertainties: list[str] = ["当前结果由规则降级提取，未经模型确认"]
    if not resolved_brand:
        uncertainties.append("品牌信息未识别")
    if not resolved_color:
        uncertainties.append("颜色信息未识别")
    if any(marker in text for marker in UNCERTAINTY_MARKERS):
        uncertainties.append("描述中存在不确定措辞，部分属性可能不准确")

    return {
        "category": resolved_category,
        "brand": resolved_brand,
        "model": None,
        "color": resolved_color,
        "material": None,
        "visible_features": features[:12],
        "accessories": accessories[:12],
        "keywords": keywords[:20],
        "uncertainties": uncertainties[:12],
        "source": "rule_fallback",
    }


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def compute_content_hash(item: Item) -> str:
    """物品内容指纹（标题 + 描述 + 类别 + 主图 Key），用于 AI 属性缓存键。"""
    primary = item.primary_image
    raw = "|".join(
        [
            item.title or "",
            item.description or "",
            item.category or "",
            getattr(primary, "object_key", "") or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _primary_image_url(item: Item) -> str | None:
    """主图的受控访问 URL（存储不可用时返回 None，不阻断提取）。"""
    primary = item.primary_image
    if primary is None:
        return None
    from app.services.storage import get_signed_url

    return get_signed_url(primary.object_key)


def extract_attributes(
    db: Session,
    *,
    item: Item,
    force: bool = False,
    commit: bool = False,
) -> AttributeResult:
    """执行属性提取并写回 `items`（**永不抛异常**，失败降级为规则提取）。

    :param force: True 时忽略缓存重新提取
    :param commit: True 时立即提交（发布流程由调用方统一提交，保证事务边界清晰）
    """
    content_hash = compute_content_hash(item)
    cache_key = cache.ai_attributes_key(item.id or 0, content_hash)

    # ---------- 1) 缓存命中 ----------
    if not force:
        cached = cache.get_json(cache_key)
        if isinstance(cached, dict) and cached.get("attributes"):
            result = AttributeResult(
                attributes=cached["attributes"],
                ai_status=cached.get("ai_status", AIStatus.SUCCESS),
                ai_model_version=cached.get("ai_model_version"),
                ai_prompt_version=cached.get("ai_prompt_version"),
                ai_analyzed_at=_utcnow(),
                degraded=bool(cached.get("degraded")),
                message="已使用缓存的 AI 属性结果",
            )
            _apply(db, item, result, commit=commit)
            return result

    # ---------- 2) 算法服务 ----------
    image_url = _primary_image_url(item)
    result: AttributeResult | None = None
    try:
        payload = get_algorithm_client().extract_attributes(
            image_url=image_url, description=item.description or "", category=item.category
        )
        ok, problems = validate_attribute_payload(payload)
        if ok:
            result = AttributeResult(
                attributes=_normalize_attributes(payload),
                ai_status=AIStatus.SUCCESS,
                ai_model_version=str(payload.get("model_version") or DEFAULT_MODEL_VERSION),
                ai_prompt_version=PROMPT_VERSION_ATTRIBUTES,
                ai_analyzed_at=_utcnow(),
                message="AI 属性提取成功",
            )
        else:
            logger.warning("算法服务返回的属性未通过 Schema 校验：%s", "; ".join(problems[:3]))
    except AlgorithmServiceError as exc:
        logger.warning("算法服务不可用，尝试降级：%s", exc.message)
    except Exception as exc:  # noqa: BLE001 - 属性提取绝不能阻断主流程
        logger.warning("算法服务调用异常（将降级）：%s", type(exc).__name__)

    # ---------- 3) MaaS 兜底 ----------
    if result is None:
        try:
            maas_payload = get_maas_client().extract_attributes(
                description=item.description or "", category=item.category, image_url=image_url
            )
            if maas_payload:
                ok, problems = validate_attribute_payload(maas_payload)
                if ok:
                    result = AttributeResult(
                        attributes=_normalize_attributes(maas_payload),
                        ai_status=AIStatus.SUCCESS,
                        ai_model_version=settings.MAAS_MODEL or "maas",
                        ai_prompt_version=PROMPT_VERSION_ATTRIBUTES,
                        ai_analyzed_at=_utcnow(),
                        message="AI 属性提取成功（MaaS）",
                    )
                else:
                    logger.warning("MaaS 返回的属性未通过 Schema 校验：%s", "; ".join(problems[:3]))
        except Exception as exc:  # noqa: BLE001
            logger.warning("MaaS 调用异常（将降级为规则提取）：%s", type(exc).__name__)

    # ---------- 4) 规则降级 ----------
    if result is None:
        attributes = rule_extract_attributes(
            title=item.title or "",
            description=item.description or "",
            category=item.category,
            color=item.color,
            brand=item.brand,
        )
        result = AttributeResult(
            attributes=attributes,
            ai_status=AIStatus.FAILED,
            ai_model_version="rule-fallback",
            ai_prompt_version=None,
            ai_analyzed_at=_utcnow(),
            degraded=True,
            message="AI 分析失败，请手动补充物品属性",
            problems=["算法服务与 MaaS 均不可用，已降级为规则提取"],
        )

    _apply(db, item, result, commit=commit)

    # 仅缓存成功结果；失败结果入缓存会让用户永远看不到恢复后的真实结果
    if result.succeeded:
        cache.set_json(
            cache_key,
            {
                "attributes": result.attributes,
                "ai_status": result.ai_status,
                "ai_model_version": result.ai_model_version,
                "ai_prompt_version": result.ai_prompt_version,
                "degraded": result.degraded,
            },
            settings.REDIS_TTL_AI_ATTRIBUTES,
        )
    return result


def _normalize_attributes(payload: dict[str, Any]) -> dict[str, Any]:
    """规范化算法输出：补齐缺失键、统一为可 JSON 序列化的结构（不改变字段名）。"""
    normalized: dict[str, Any] = {
        "category": payload.get("category"),
        "brand": payload.get("brand"),
        "model": payload.get("model"),
        "color": payload.get("color"),
        "material": payload.get("material"),
        "visible_features": [str(x) for x in (payload.get("visible_features") or [])],
        "accessories": [str(x) for x in (payload.get("accessories") or [])],
        "keywords": [str(x) for x in (payload.get("keywords") or [])],
        "uncertainties": [str(x) for x in (payload.get("uncertainties") or [])],
    }
    # 保留算法侧可能扩展的字段，便于灰度与消融实验
    for key, value in payload.items():
        if key not in normalized and key != "model_version":
            normalized[key] = value
    return normalized


def _apply(db: Session, item: Item, result: AttributeResult, *, commit: bool) -> None:
    """把结果写入 `items` 的 AI 相关字段。"""
    item.ai_attributes = result.attributes
    item.ai_status = result.ai_status
    item.ai_model_version = result.ai_model_version
    item.ai_prompt_version = result.ai_prompt_version
    item.ai_analyzed_at = result.ai_analyzed_at
    db.add(item)
    if commit:
        db.commit()
    logger.info(
        "AI 属性提取完成 item_id=%s status=%s model=%s degraded=%s",
        item.id,
        result.ai_status,
        result.ai_model_version,
        result.degraded,
    )


def attributes_json(item: Item) -> dict[str, Any] | None:
    """给响应体用的属性快照（`json.dumps` 可序列化校验）。"""
    if not item.ai_attributes:
        return None
    return json.loads(json.dumps(item.ai_attributes, ensure_ascii=False, default=str))


__all__ = [
    "ATTRIBUTE_JSON_SCHEMA",
    "ATTRIBUTE_JSON_SCHEMA_KEYS",
    "CATEGORY_KEYWORDS",
    "COLOR_KEYWORDS",
    "BRAND_KEYWORDS",
    "FEATURE_PATTERNS",
    "ACCESSORY_KEYWORDS",
    "AttributeResult",
    "validate_attribute_payload",
    "rule_extract_attributes",
    "compute_content_hash",
    "extract_attributes",
    "attributes_json",
]
