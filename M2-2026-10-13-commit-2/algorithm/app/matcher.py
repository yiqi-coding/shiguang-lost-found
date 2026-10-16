"""核心匹配评分。

总分（权重全部来自 ``config/matching.yaml``）::

    总分 = 0.20×类别分 + 0.25×属性/文本分 + 0.20×图像相似度
         + 0.15×时间合理度 + 0.15×地点合理度 + 0.05×品牌/型号分
    总分 = Σ(权重 × 维度分) + Σ冲突惩罚（惩罚默认 0，见 rules.adjustments）

设计约定
--------
* 所有维度分与总分都归一化到 ``[0, 1]``。
* 任何维度缺数据 → 取 ``neutral_scores`` 里配置的中性分，并写入 ``uncertainties``，
  **绝不抛异常**。
* 图像相似度通过可插拔的 :class:`ImageEmbedder` 协议获取，本模块不依赖任何具体
  实现类；默认实现是确定性的 :class:`MockImageEmbedder`。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from .config_loader import DIMENSIONS, RuntimeConfig, load_config
from .extractor import AttributeExtractor
from .locations import ParsedLocation, get_index, parse_location, score_locations
from .text_utils import (
    as_text_list,
    clamp,
    normalize,
    normalize_key,
    ngram_similarity,
    safe_float,
    token_dice,
)

# 维度展示名（evidence / uncertainties 文案用）
DIMENSION_LABELS = {
    "category": "类别",
    "text": "属性/文本",
    "image": "图像",
    "time": "时间",
    "location": "地点",
    "brand_model": "品牌/型号",
}


# ===========================================================================
# 一、图像相似度：可插拔接口 + 确定性 Mock
# ===========================================================================
@runtime_checkable
class ImageEmbedder(Protocol):
    """图像向量化接口（协议）。

    任何满足 ``embed(image_ref) -> list[float]`` 的对象都可以作为后端注入，
    例如真实模型适配器::

        class HuaweiMaaSImageEmbedder:
            '''真实实现（华为云 MaaS 多模态向量 / 本地 CLIP 服务）示例：'''
            def embed(self, image_ref):
                # 1) 用 image_ref（URL 或对象存储 key）下载 / 读取图片字节
                # 2) 调用远程多模态向量接口或本地 CLIP 前向，拿到 512/768 维向量
                # 3) 返回 list[float]（建议先做 L2 归一化）
                ...

    接入方式（不改动 matcher 任何代码）::

        register_image_embedder("huawei_maas", HuaweiMaaSImageEmbedder())
        # 然后把 config/matching.yaml 的 rules.image.backend 改成 "huawei_maas"，
        # 或在创建 MatchEngine 时传参：MatchEngine(image_embedder="huawei_maas")

    :meth:`MatchEngine._image_score` 只使用 ``embed`` + ``cosine_similarity``，
    不引用任何具体类名。
    """

    def embed(self, image_ref: Any) -> List[float]:  # pragma: no cover - 协议声明
        ...


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """余弦相似度（纯标准库实现，无需 numpy）。零向量或空向量返回 0.0。"""
    if not vec_a or not vec_b:
        return 0.0
    size = min(len(vec_a), len(vec_b))
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for idx in range(size):
        a = safe_float(vec_a[idx])
        b = safe_float(vec_b[idx])
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


@dataclass
class MockImageEmbedder:
    """确定性 Mock 图像向量化器（无任何真实模型 / 网络依赖）。

    由「图片文件名 + 类别/颜色/品牌/型号/材质/特征」等**伪视觉词袋**做稳定哈希
    （``blake2b``，不用受 ``PYTHONHASHSEED`` 影响的 Python 内置 ``hash``）生成伪向量：
    相同输入永远得到相同向量，因此余弦相似度可复现，测试可断言。

    为了贴近真实视觉嵌入的语义，词袋只使用与外观强相关的规范属性（权重见
    ``rules.image.mock_token_weights``），**不掺入「失物/拾物」这类发文措辞**，
    所以同一件物品必然得到高相似度，而外观不同的物品相似度更低。

    传入 ``image_ref`` 时优先使用它；缺失时回退到 ``extra_tokens``。
    ``extra_tokens`` 既可以是字符串序列（简单场景），也可以是 ``[(token, weight)]``。
    """

    dimension: int = 64
    seed: str = "shiguang-image-v1"
    name: str = "mock"
    token_weights: Dict[str, float] = field(default_factory=dict)

    # -- 输入归一化 -------------------------------------------------------
    def _image_name_tokens(self, image_ref: Any) -> List[str]:
        if not image_ref:
            return []
        raw = str(image_ref)
        base = raw.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
        if not base:
            return []
        stem = base.rsplit(".", 1)[0]
        tokens = []
        # 去掉「lost/found/失物/拾物」这类发文侧标记，只保留外观相关线索
        cleaned = re.sub(r"(?i)(lost|found|丢失|遗失|拾获|捡到|拾到)", "", stem)
        for candidate in (stem, cleaned or stem):
            key = normalize(candidate)
            if key:
                tokens.append(key)
        return tokens

    def _tokens(self, image_ref: Any, extra_tokens: Optional[Iterable[Any]] = None) -> List[Tuple[str, float]]:
        tokens: List[Tuple[str, float]] = []
        weights = dict(self.token_weights or {})
        name_weight = float(weights.get("image_name", 2.0) or 2.0)
        for token in self._image_name_tokens(image_ref):
            tokens.append((f"name:{token}", name_weight))
        for entry in extra_tokens or []:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                token, weight = entry[0], entry[1]
            else:
                token, weight = entry, 1.0
            key = normalize(token)
            if key:
                tokens.append((key, float(weight)))
        return tokens

    def embed(self, image_ref: Any = None, extra_tokens: Optional[Iterable[Any]] = None) -> List[float]:
        """返回 ``dimension`` 维伪向量（L2 归一化）。"""
        tokens = self._tokens(image_ref, extra_tokens)
        dimension = max(1, int(self.dimension))
        vector = [0.0] * dimension
        if not tokens:
            # 完全没有输入：返回全零向量（余弦相似度定义为 0）
            return vector
        total_weight = sum(weight for _token, weight in tokens) or 1.0
        for token, weight in tokens:
            scaled = weight / total_weight
            for idx in range(dimension):
                digest = hashlib.blake2b(
                    f"{self.seed}|{token}|{idx}".encode("utf-8"), digest_size=8
                ).digest()
                unit = int.from_bytes(digest, "big") / float(1 << 64)   # [0,1)
                vector[idx] += (unit * 2.0 - 1.0) * scaled             # 加权 [-1,1)
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


#: 后端正则表：名称 → 实现实例（真实模型通过 register_image_embedder 注入）
_IMAGE_BACKENDS: Dict[str, ImageEmbedder] = {}


def register_image_embedder(name: str, embedder: ImageEmbedder) -> None:
    """注册图像向量化后端（真实实现接入点，见 :class:`ImageEmbedder`）。"""
    if not name:
        raise ValueError("后端名称不能为空")
    if not hasattr(embedder, "embed"):
        raise TypeError("embedder 必须实现 embed(image_ref) -> list[float]")
    _IMAGE_BACKENDS[str(name)] = embedder


def get_image_embedder(name: Optional[str] = None, config: Optional[RuntimeConfig] = None) -> ImageEmbedder:
    """按名称取后端；未注册时回退到配置指定的后端，再回退到 Mock。"""
    config = config or load_config()
    image_rules = config.rule("image", {}) or {}
    wanted = str(name or image_rules.get("backend") or "mock")
    if wanted in _IMAGE_BACKENDS:
        return _IMAGE_BACKENDS[wanted]
    mock = MockImageEmbedder(
        dimension=int(image_rules.get("dimension", 64) or 64),
        seed=str(image_rules.get("seed", "shiguang-image-v1")),
        name="mock",
        token_weights=image_rules.get("mock_token_weights") or {},
    )
    _IMAGE_BACKENDS.setdefault("mock", mock)
    return _IMAGE_BACKENDS.get(wanted, mock)


def available_image_backends() -> List[str]:
    get_image_embedder()  # 确保 mock 已注册
    return sorted(_IMAGE_BACKENDS)


# ===========================================================================
# 二、时间合理度（分段函数）
# ===========================================================================
@dataclass
class TimeWindow:
    """时间输入：单点时间或时间范围。"""

    start: Optional[datetime] = None
    end: Optional[datetime] = None
    raw: Any = None
    known: bool = False
    is_range: bool = False

    def distance_hours_to(self, other: "TimeWindow") -> Optional[float]:
        """点到区间 / 区间到区间的最短时间差（小时）。"""
        if not self.known or not other.known:
            return None
        if self.is_range or other.is_range:
            a_start, a_end = self.start, self.end or self.start
            b_start, b_end = other.start, other.end or other.start
            if a_start is None or b_start is None:
                return None
            assert a_end is not None and b_end is not None
            if a_end < b_start:
                delta = b_start - a_end
            elif b_end < a_start:
                delta = a_start - b_end
            else:
                return 0.0  # 区间重叠
            return abs(delta.total_seconds()) / 3600.0
        assert self.start is not None and other.start is not None
        return abs((self.start - other.start).total_seconds()) / 3600.0


@dataclass
class TimeScore:
    score: float
    tier: str
    reason: str
    diff_hours: Optional[float] = None
    known: bool = True
    simulated: bool = False


def _parse_datetime(value: Any, formats: Sequence[str]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
    try:
        return datetime.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None


def parse_time_window(value: Any, config: Optional[RuntimeConfig] = None) -> TimeWindow:
    """把各种时间写法解析成 :class:`TimeWindow`。

    支持 ``"2026-09-28T15:00:00"``、``"2026-09-28 15:00"``、``"2026-09-28"``、
    ``{"start": ..., "end": ...}``、``[start, end]`` 以及 datetime 对象。
    解析失败 → ``known=False``（上层取中性分）。
    """
    config = config or load_config()
    formats = list(config.section("time.event_time_formats", []) or [])
    if not formats:
        formats = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"]

    if isinstance(value, dict):
        start = _parse_datetime(value.get("start", value.get("from")), formats)
        end = _parse_datetime(value.get("end", value.get("to")), formats)
        if start or end:
            start = start or end
            end = end or start
            is_range = bool(value.get("end") or value.get("to"))
            return TimeWindow(start=start, end=end, raw=value, known=True, is_range=is_range or start != end)
        # 兼容 {"time": "..."} / {"event_time": "..."} 写法
        for key in ("time", "event_time", "lost_time", "found_time"):
            if key in value:
                return parse_time_window(value[key], config)
        return TimeWindow(raw=value, known=False)

    if isinstance(value, (list, tuple)) and len(value) >= 1:
        start = _parse_datetime(value[0], formats)
        end = _parse_datetime(value[1], formats) if len(value) > 1 else None
        if start or end:
            start = start or end
            end = end or start
            return TimeWindow(start=start, end=end, raw=value, known=True, is_range=len(value) > 1)
        return TimeWindow(raw=value, known=False)

    if isinstance(value, str) and "~" in value:
        left, _, right = value.partition("~")
        return parse_time_window([left.strip(), right.strip()], config)

    moment = _parse_datetime(value, formats)
    if moment is None:
        return TimeWindow(raw=value, known=False)
    return TimeWindow(start=moment, end=moment, raw=value, known=True, is_range=False)


def time_score_from_diff(diff_hours: Optional[float], config: Optional[RuntimeConfig] = None) -> TimeScore:
    """按 ``config/matching.yaml → rules.time.segments`` 的分段函数打分。"""
    config = config or load_config()
    time_rules = config.rule("time", {}) or {}
    segments = time_rules.get("segments") or []
    fallback = float(time_rules.get("fallback_score", 0.10))
    neutral = float(config.neutral_scores.get("time", 0.5))

    if diff_hours is None or diff_hours != diff_hours:
        return TimeScore(score=neutral, tier="unknown", reason="时间信息缺失或无法解析，按中性分处理", known=False)
    if diff_hours < 0:
        diff_hours = abs(diff_hours)

    previous_max = 0.0
    previous_score: Optional[float] = None
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        raw_max = segment.get("max_diff", None)
        score = float(segment.get("score", fallback))
        interpolation = str(segment.get("interpolation", "step"))
        name = str(segment.get("name", "segment"))
        if raw_max is None:
            return TimeScore(score=score, tier=name, reason=_time_reason(diff_hours, name), diff_hours=diff_hours)
        max_diff = float(raw_max)
        if diff_hours <= max_diff:
            if interpolation == "linear" and previous_score is not None:
                span = max_diff - previous_max
                ratio = 0.0 if span <= 0 else (diff_hours - previous_max) / span
                value = previous_score + (score - previous_score) * ratio
                return TimeScore(score=clamp(value), tier=name,
                                 reason=_time_reason(diff_hours, name, interpolated=True), diff_hours=diff_hours)
            return TimeScore(score=clamp(score), tier=name, reason=_time_reason(diff_hours, name), diff_hours=diff_hours)
        previous_max, previous_score = max_diff, score
    return TimeScore(score=clamp(fallback), tier="beyond_all_segments",
                     reason=_time_reason(diff_hours, "beyond_all_segments"), diff_hours=diff_hours)


def _time_reason(diff_hours: float, tier: str, interpolated: bool = False) -> str:
    if diff_hours < 1:
        readable = f"{diff_hours * 60:.0f}分钟"
    elif diff_hours < 48:
        readable = f"{diff_hours:.1f}小时"
    else:
        readable = f"{diff_hours / 24:.1f}天"
    if tier == "unknown":
        return "时间信息缺失"
    suffix = "（线性过渡）" if interpolated else ""
    return f"时间相差约{readable}{suffix}"


def score_time(left: Any, right: Any, config: Optional[RuntimeConfig] = None) -> TimeScore:
    config = config or load_config()
    window_left = left if isinstance(left, TimeWindow) else parse_time_window(left, config)
    window_right = right if isinstance(right, TimeWindow) else parse_time_window(right, config)
    if not window_left.known or not window_right.known:
        return time_score_from_diff(None, config)
    return time_score_from_diff(window_left.distance_hours_to(window_right), config)


# ===========================================================================
# 三、维度评分明细
# ===========================================================================
@dataclass
class DimensionScore:
    name: str
    score: float
    weight: float
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def contribution(self) -> float:
        return self.score * self.weight


@dataclass
class MatchOutcome:
    candidate_id: Any
    total_score: float
    scores: Dict[str, float]
    evidence: List[str]
    uncertainties: List[str]
    dimensions: Dict[str, DimensionScore] = field(default_factory=dict)
    applied_adjustments: float = 0.0
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "total_score": round(self.total_score, 4),
            "scores": {key: round(value, 4) for key, value in self.scores.items()},
            "evidence": list(self.evidence),
            "uncertainties": list(self.uncertainties),
        }


@dataclass
class ItemView:
    """统一后的物品视图（请求体 / 数据集记录 → 内部结构）。"""

    raw: Dict[str, Any]
    id: Any = None
    category: str = ""
    title: str = ""
    description: str = ""
    color: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    material: Optional[str] = None
    location: str = ""
    raw_time: Any = None
    image_url: Any = None
    public_features: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    accessories: List[str] = field(default_factory=list)
    hidden_features: List[str] = field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, Any]], extractor: AttributeExtractor) -> "ItemView":
        payload = payload or {}
        attributes = payload.get("attributes") or {}
        if not isinstance(attributes, dict):
            attributes = {}
        # ai_attributes（数据集）> attributes（接口）> 直接字段
        ai_attributes = payload.get("ai_attributes") or {}
        if not isinstance(ai_attributes, dict):
            ai_attributes = {}

        def pick(*keys, default=None):
            for source in (attributes, ai_attributes, payload):
                for key in keys:
                    if isinstance(source, dict) and source.get(key) not in (None, "", [], {}):
                        return source[key]
            return default

        color = pick("color")
        brand = pick("brand")
        model = pick("model", "brand_model", "model_name")
        material = pick("material")

        # 颜色/材质/类别等缺失时用规则抽取器补全（确定性规则，不用模型）
        text_blob = " ".join(str(x) for x in (payload.get("title"), payload.get("description")) if x)
        extracted = extractor.extract(
            description=payload.get("description") or "",
            title=payload.get("title") or "",
            category=pick("category"),
            image_url=pick("image_url", "image"),
        )
        category = pick("category") or extracted.category or ""
        color = color or extracted.color
        brand = brand or extracted.brand
        model = model or extracted.model
        material = material or extracted.material
        # 用规则表把厂商/颜色写法归一（Apple / apple → apple；深黑 → 黑色），
        # 保证「同一实体不同写法」不会被判成冲突。
        brand = extractor.canonical_brand(brand) or brand
        color = extractor.canonical_color(color) or color
        material = extractor.canonical_material(material) or material

        features = as_text_list(pick("public_features", "visible_features", "features"))
        if not features:
            features = list(extracted.visible_features)
        keywords = as_text_list(pick("keywords"))
        if not keywords:
            keywords = list(extracted.keywords)
        accessories = as_text_list(pick("accessories"))
        if not accessories:
            accessories = list(extracted.accessories)

        return cls(
            raw=payload,
            id=payload.get("id"),
            category=str(category or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or text_blob),
            color=str(color) if color else None,
            brand=str(brand) if brand else None,
            model=str(model) if model else None,
            material=str(material) if material else None,
            location=str(pick("location", "place") or ""),
            raw_time=pick("event_time", "time", "lost_time", "found_time"),
            image_url=pick("image_url", "image"),
            public_features=features,
            keywords=keywords,
            accessories=accessories,
            hidden_features=as_text_list(pick("hidden_features")),
        )


# ===========================================================================
# 四、引擎
# ===========================================================================
class MatchEngine:
    """把两个物品的类别/属性/图像/时间/地点/品牌融合成分数 + 可解释理由。"""

    def __init__(
        self,
        config: Optional[RuntimeConfig] = None,
        image_embedder: Any = None,
        extractor: Optional[AttributeExtractor] = None,
    ) -> None:
        self.config = config or load_config()
        self.image_embedder = (
            image_embedder if (image_embedder is not None and hasattr(image_embedder, "embed"))
            else get_image_embedder(image_embedder if isinstance(image_embedder, str) else None, self.config)
        )
        self.extractor = extractor or AttributeExtractor(self.config)
        self.index = get_index()
        self.weights = self.config.weights
        self.neutral = self.config.neutral_scores
        category_rules = self.config.rule("category", {}) or {}
        self._category_aliases = {
            normalize(str(key)): str(value)
            for key, value in (category_rules.get("aliases") or {}).items()
            if value
        }
        self._families: List[set] = []
        for family in category_rules.get("families") or []:
            if isinstance(family, (list, tuple)) and len(family) >= 2:
                self._families.append({self.canonical_category(x) for x in family})
        self._related: List[set] = []
        for pair in category_rules.get("related_pairs") or []:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                self._related.append({self.canonical_category(x) for x in pair})
        self.brand_aliases = {
            normalize(str(key)): normalize(str(value))
            for key, value in ((self.config.rule("brand_model", {}) or {}).get("brand_aliases") or {}).items()
            if value
        }

    # -- 类别 -------------------------------------------------------------
    def canonical_category(self, value: Any) -> str:
        key = normalize(value)
        if not key:
            return ""
        return self._category_aliases.get(key, key)

    def score_category(self, left: ItemView, right: ItemView) -> DimensionScore:
        rules = self.config.rule("category", {}) or {}
        weight = self.weights.get("category", 0.0)
        a, b = self.canonical_category(left.category), self.canonical_category(right.category)
        if not a or not b:
            return DimensionScore(
                name="category", score=self.neutral.get("category", 0.5), weight=weight,
                reason="一侧类别缺失",
                uncertainties=[f"{'失物' if not a else '拾物'}类别信息缺失，按中性分处理"],
                detail={"left": a, "right": b},
            )
        evidence: List[str] = []
        uncertainties: List[str] = []
        if a == b:
            evidence.append("物品类别一致")
            return DimensionScore(
                name="category", score=float(rules.get("same_category_score", 1.0)), weight=weight,
                reason=f"类别一致（{left.category}）", evidence=evidence,
                detail={"left": a, "right": b, "relation": "same"},
            )
        same_family = any(a in family and b in family for family in self._families)
        if same_family:
            uncertainties.append(f"类别表述不同（{left.category} / {right.category}），按同族近似处理")
            return DimensionScore(
                name="category", score=float(rules.get("same_family_score", 0.6)), weight=weight,
                reason="类别同族但表述不同", evidence=evidence, uncertainties=uncertainties,
                detail={"left": a, "right": b, "relation": "family"},
            )
        related = any(a in pair and b in pair for pair in self._related)
        if related:
            uncertainties.append("仅属于弱相关类别，需人工确认")
            return DimensionScore(
                name="category", score=float(rules.get("related_score", 0.25)), weight=weight,
                reason="类别弱相关", evidence=evidence, uncertainties=uncertainties,
                detail={"left": a, "right": b, "relation": "related"},
            )
        uncertainties.append(f"类别不一致（{left.category} / {right.category}）")
        return DimensionScore(
            name="category", score=float(rules.get("mismatch_score", 0.0)), weight=weight,
            reason="类别不一致", evidence=evidence, uncertainties=uncertainties,
            detail={"left": a, "right": b, "relation": "mismatch"},
        )

    # -- 图像 -------------------------------------------------------------
    def _image_token_source(self, item: ItemView) -> List[Tuple[str, float]]:
        """构造「伪视觉词袋」：只取外观相关的规范属性 + 文件名线索。

        任何后端都可以直接使用这些 (token, weight)，
        真实实现（如 CLIP / 华为云 MaaS）会忽略它们、直接读图片字节。
        """
        rules = self.config.rule("image", {}) or {}
        weights = rules.get("mock_token_weights") or {}
        tokens: List[Tuple[str, float]] = []

        def add(prefix: str, value: Any, default_weight: float) -> None:
            if value in (None, "", [], {}):
                return
            weight = float(weights.get(prefix, default_weight) or default_weight)
            for text in ([value] if isinstance(value, str) else list(value)):
                key = normalize(str(text))
                if key:
                    tokens.append((f"{prefix}:{key}", weight))

        add("category", item.category, 3.0)
        add("color", item.color, 2.0)
        add("brand", item.brand, 1.5)
        add("model", item.model, 1.5)
        add("material", item.material, 1.0)
        add("features", item.public_features, 2.0)
        add("accessories", item.accessories, 0.5)
        if not tokens:
            # 完全没有结构化属性时，退回配置里声明的兜底文本字段
            fields = rules.get("text_fallback_fields") or ["category", "title", "description"]
            fallback: List[str] = []
            for name in fields:
                value = getattr(item, str(name), None)
                if isinstance(value, (list, tuple)):
                    fallback.extend(str(x) for x in value)
                elif value:
                    fallback.append(str(value))
            add("fallback", fallback, 1.0)
        return tokens

    @staticmethod
    def _embed(embedder: Any, image_ref: Any, tokens: List[Tuple[str, float]]) -> List[float]:
        """调用后端 embed；兼容只接受一个位置参数的实现。"""
        try:
            return list(embedder.embed(image_ref, tokens))  # type: ignore[misc]
        except TypeError:
            return list(embedder.embed(image_ref))

    def score_image(self, left: ItemView, right: ItemView) -> DimensionScore:
        rules = self.config.rule("image", {}) or {}
        weight = self.weights.get("image", 0.0)
        neutral = self.neutral.get("image", 0.5)
        try:
            vector_left = self._embed(self.image_embedder, left.image_url, self._image_token_source(left))
            vector_right = self._embed(self.image_embedder, right.image_url, self._image_token_source(right))
        except Exception as exc:  # noqa: BLE001 - 任何后端异常都降级
            return DimensionScore(
                name="image", score=neutral, weight=weight,
                reason=f"图像向量化失败（{exc.__class__.__name__}），按中性分处理",
                uncertainties=["图像特征不可用，图像维度按中性分处理"],
            )
        similarity = cosine_similarity(vector_left, vector_right)
        scale = float(rules.get("similarity_scale", 1.0) or 1.0)
        score = clamp((similarity + 1.0) / 2.0 * scale,
                      float(rules.get("min_similarity", 0.0)), float(rules.get("max_similarity", 1.0)))
        missing_image = not left.image_url or not right.image_url
        evidence: List[str] = []
        uncertainties: List[str] = []
        if missing_image:
            uncertainties.append("缺少图片，图像相似度由文本特征兜底推算")
        if score >= 0.80:
            evidence.append("图像特征高度相似")
        elif score >= 0.62:
            evidence.append("图像特征较为相似")
        elif score < 0.45:
            uncertainties.append("图像特征差异较大")
        return DimensionScore(
            name="image", score=score, weight=weight,
            reason=f"图像余弦相似度 {similarity:+.3f} → 维度分 {score:.3f}",
            evidence=evidence, uncertainties=uncertainties,
            detail={"cosine": round(similarity, 6), "mock": True},
        )

    # -- 文本 / 属性 -------------------------------------------------------
    def _color_pair(self, color_a: Optional[str], color_b: Optional[str]) -> Tuple[float, List[str], List[str], str]:
        rules = self.config.rule("text", {}) or {}
        evidence: List[str] = []
        uncertainties: List[str] = []
        key_a, key_b = normalize(color_a), normalize(color_b)
        if not key_a or not key_b:
            return float(self.neutral.get("text", 0.5)), evidence, uncertainties, "颜色信息缺失"
        if key_a == key_b:
            evidence.append("颜色一致")
            return float(rules.get("color_score", 1.0)), evidence, uncertainties, "颜色一致"
        if key_a in key_b or key_b in key_a:
            uncertainties.append("颜色描述相近但表述不同")
            return float(rules.get("color_partial_score", 0.6)), evidence, uncertainties, "颜色描述相近"
        uncertainties.append("颜色不一致")
        return float(rules.get("color_conflict_score", 0.1)), evidence, uncertainties, "颜色不一致"

    def _material_pair(self, material_a: Optional[str], material_b: Optional[str]) -> Tuple[float, List[str], List[str], str]:
        rules = self.config.rule("text", {}) or {}
        evidence: List[str] = []
        uncertainties: List[str] = []
        key_a, key_b = normalize(material_a), normalize(material_b)
        if not key_a or not key_b:
            return float(rules.get("material_neutral", 0.5)), evidence, uncertainties, "材质信息缺失"
        if key_a == key_b or key_a in key_b or key_b in key_a:
            evidence.append("材质一致")
            return float(rules.get("material_score", 1.0)), evidence, uncertainties, "材质一致"
        uncertainties.append("材质描述不一致")
        return float(rules.get("material_conflict_score", 0.2)), evidence, uncertainties, "材质不一致"

    def _feature_similarity(self, feature_a: str, feature_b: str) -> float:
        """特征短语相似度：字符 bigram/trigram Dice 取较大值。

        用 trigram 可以避免「椭圆形充电盒」与「方形充电盒」因共享「充电盒」而被判为同一细节。
        """
        key_a, key_b = normalize(feature_a), normalize(feature_b)
        if not key_a or not key_b:
            return 0.0
        return max(
            ngram_similarity(key_a, key_b, [2], "dice"),
            ngram_similarity(key_a, key_b, [3], "dice"),
        )

    def _feature_pairs(self, features_a: Sequence[str], features_b: Sequence[str], threshold: float) -> Dict[str, Any]:
        """逐条特征做 n-gram 相似度比对（贪心一一配对 + 双向覆盖率）。

        ``score`` 取「A 侧被解释比例」与「B 侧被解释比例」的较小值：
        - 双方特征一一对应 → 1.0；
        - 一方多出/缺少特征 → 明显下降（避免「同色不同款」被判成同一件物品）。
        """
        if not features_a or not features_b:
            return {
                "score": None, "coverage_a": 0.0, "coverage_b": 0.0,
                "matched": [], "unmatched": list(features_a) + list(features_b), "max": 0.0,
            }
        matched: List[Tuple[str, str, float]] = []
        unmatched_a: List[str] = []
        used_b: set = set()
        best_overall = 0.0
        for feature_a in features_a:
            best, best_index = 0.0, -1
            for index, feature_b in enumerate(features_b):
                if index in used_b:
                    continue
                similarity = self._feature_similarity(feature_a, feature_b)
                if similarity > best:
                    best, best_index = similarity, index
            best_overall = max(best_overall, best)
            if best >= threshold and best_index >= 0:
                used_b.add(best_index)
                matched.append((feature_a, features_b[best_index], best))
            else:
                unmatched_a.append(feature_a)
        coverage_a = len(matched) / max(len(features_a), 1)
        coverage_b = len(matched) / max(len(features_b), 1)
        unmatched_b = [text for index, text in enumerate(features_b) if index not in used_b]
        return {
            "score": clamp(min(coverage_a, coverage_b)),
            "coverage_a": coverage_a,
            "coverage_b": coverage_b,
            "matched": matched,
            "unmatched": unmatched_a + unmatched_b,
            "max": best_overall,
        }

    def score_text(self, left: ItemView, right: ItemView) -> DimensionScore:
        rules = self.config.rule("text", {}) or {}
        weight = self.weights.get("text", 0.0)
        neutral = self.neutral.get("text", 0.5)
        evidence: List[str] = []
        uncertainties: List[str] = []
        min_length = int(rules.get("min_text_length", 2) or 2)
        sizes = [int(x) for x in (rules.get("ngram_sizes") or [2, 3])]
        method = str(rules.get("ngram_similarity", "dice"))

        text_a = normalize(" ".join(filter(None, [left.title, left.description])))
        text_b = normalize(" ".join(filter(None, [right.title, right.description])))
        text_known = len(text_a) >= min_length and len(text_b) >= min_length
        char_similarity = ngram_similarity(text_a, text_b, sizes, method) if text_known else 0.0

        keywords_a = [normalize(k) for k in (left.keywords or []) if normalize(k)]
        keywords_b = [normalize(k) for k in (right.keywords or []) if normalize(k)]
        if not keywords_a:
            keywords_a = [normalize(x) for x in self.extractor.extract_keywords(left.description or left.title)]
        if not keywords_b:
            keywords_b = [normalize(x) for x in self.extractor.extract_keywords(right.description or right.title)]
        keyword_similarity = token_dice(keywords_a, keywords_b) if (keywords_a and keywords_b) else 0.0

        color_score, color_evidence, color_uncertainty, color_reason = self._color_pair(left.color, right.color)
        material_score, material_evidence, material_uncertainty, material_reason = self._material_pair(left.material, right.material)
        evidence.extend(color_evidence + material_evidence)
        uncertainties.extend(color_uncertainty + material_uncertainty)

        threshold = float(rules.get("feature_pair_threshold", 0.42))
        feature_info = self._feature_pairs(left.public_features, right.public_features, threshold)
        pairs_weight = float(rules.get("feature_pairs_weight", 0.60))
        attr_weight = float(rules.get("feature_attr_weight", 0.40))
        color_material = (color_score + material_score) / 2.0
        if feature_info["score"] is None:
            attribute_score = color_material
            uncertainties.append("公开特征描述不足，特征比对按颜色/材质推断")
        else:
            feature_score = float(feature_info["score"])
            # 单条特征的 n-gram 相似度也计入，避免「措辞不同但指向同一细节」被判死
            feature_score = clamp(0.8 * feature_score + 0.2 * clamp(float(feature_info["max"])))
            attribute_score = feature_score * pairs_weight + color_material * attr_weight
            if feature_info["matched"]:
                example = feature_info["matched"][0]
                evidence.append(f"特征描述吻合（{example[0]}）")
            if feature_info["unmatched"]:
                uncertainties.append(f"有 {len(feature_info['unmatched'])} 条特征未能在对方描述中找到对应")

        components = {
            "char_ngram": char_similarity,
            "keyword_overlap": keyword_similarity,
            "attribute_match": attribute_score,
        }
        if not text_known and not keywords_a and not keywords_b and not left.public_features and not right.public_features \
                and not left.color and not right.color:
            return DimensionScore(
                name="text", score=neutral, weight=weight,
                reason="两侧均无可用文本/属性信息，按中性分处理",
                uncertainties=["缺少描述、关键词与颜色等属性，只能按中性分处理"],
                detail={"components": components},
            )

        attr_rules = rules.get("component_weights") or {}
        weights = {
            "char_ngram": float(attr_rules.get("char_ngram", 0.40)),
            "keyword_overlap": float(attr_rules.get("keyword_overlap", 0.25)),
            "attribute_match": float(attr_rules.get("attribute_match", 0.35)),
        }
        total_weight = sum(weights.values()) or 1.0
        fused = sum(components[key] * weights[key] for key in components) / total_weight

        # 品牌线索（描述里出现对方品牌）作为小幅加分
        brand_hint = 0.0
        if left.brand and normalize(left.brand) in text_b:
            brand_hint = 1.0
        if right.brand and normalize(right.brand) in text_a:
            brand_hint = 1.0
        hint_weight = float(rules.get("brand_hint_weight", 0.15))
        if brand_hint:
            fused = clamp(fused + (1.0 - fused) * hint_weight)
            evidence.append("描述中提及了对方品牌")

        score = self._calibrate(fused, rules.get("calibration") or [])

        # ---- 冲突 gate：明确冲突时给属性/文本分设上限（阈值来自配置） ----
        gates: List[str] = []
        if color_reason == "颜色不一致":
            cap = float(rules.get("max_text_score_on_color_conflict", 1.0))
            if score > cap:
                score = cap
                gates.append("color_conflict")
        if material_reason == "材质不一致":
            cap = float(rules.get("max_text_score_on_material_conflict", 1.0))
            if score > cap:
                score = cap
                gates.append("material_conflict")
        if feature_info["score"] is not None:
            coverage_gate = float(rules.get("feature_gate_coverage", 0.0))
            if float(feature_info["score"]) < coverage_gate:
                cap = float(rules.get("max_text_score_on_feature_mismatch", 1.0))
                if score > cap:
                    score = cap
                    gates.append("feature_mismatch")
                uncertainties.append("公开特征与对方描述明显对不上，文本分已按上限压制")

        if char_similarity >= 0.70:
            evidence.append("文字描述高度相似")
        elif char_similarity >= 0.40:
            evidence.append("文字描述部分重合")
        if keyword_similarity >= 0.5:
            evidence.append("关键词高度重合")

        return DimensionScore(
            name="text", score=score, weight=weight,
            reason=f"n-gram {char_similarity:.2f} / 关键词 {keyword_similarity:.2f} / 属性 {attribute_score:.2f}"
                   f" → 融合 {fused:.3f}" + (f"（gates: {','.join(gates)}）" if gates else ""),
            evidence=evidence, uncertainties=uncertainties,
            detail={
                "components": {key: round(value, 6) for key, value in components.items()},
                "fused": round(fused, 6),
                "color": color_reason, "material": material_reason,
                "matched_features": [
                    {"lost": m[0], "found": m[1], "similarity": round(float(m[2]), 4)}
                    for m in feature_info["matched"]
                ],
            },
        )

    @staticmethod
    def _calibrate(similarity: float, calibration: Sequence[Dict[str, Any]]) -> float:
        for band in calibration:
            if not isinstance(band, dict):
                continue
            if similarity >= float(band.get("min", 0.0)):
                return clamp(float(band.get("score", similarity)))
        return clamp(similarity)

    # -- 品牌 / 型号 -------------------------------------------------------
    def _normalize_brand(self, value: Optional[str]) -> Optional[str]:
        key = normalize(value)
        if not key:
            return None
        return self.brand_aliases.get(key, key)

    @staticmethod
    def _normalize_model(value: Optional[str]) -> Optional[str]:
        key = normalize_key(value)
        return key or None

    def score_brand_model(self, left: ItemView, right: ItemView) -> DimensionScore:
        rules = self.config.rule("brand_model", {}) or {}
        weight = self.weights.get("brand_model", 0.0)
        evidence: List[str] = []
        uncertainties: List[str] = []
        penalties: List[str] = []

        brand_a, brand_b = self._normalize_brand(left.brand), self._normalize_brand(right.brand)
        if not brand_a or not brand_b:
            brand_score = float(rules.get("brand_unknown_neutral", 0.5))
            uncertainties.append("品牌未确认" if not (brand_a and brand_b) else "品牌信息不足")
        elif brand_a == brand_b:
            brand_score = float(rules.get("brand_exact_score", 1.0))
            evidence.append("品牌一致")
        else:
            brand_score = float(rules.get("brand_conflict_score", 0.05))
            uncertainties.append("品牌不一致，疑似不是同一物品")
            penalties.append("brand_conflict")

        model_a, model_b = self._normalize_model(left.model), self._normalize_model(right.model)
        if not model_a or not model_b:
            model_score = float(rules.get("model_unknown_neutral", 0.5))
            if model_a or model_b:
                uncertainties.append("型号信息仅一方提供，无法核对")
        elif model_a == model_b:
            model_score = float(rules.get("model_exact_score", 1.0))
            evidence.append("型号一致")
        elif model_a in model_b or model_b in model_a:
            model_score = float(rules.get("model_partial_score", 0.7))
            uncertainties.append("型号部分重合，需进一步核对")
        else:
            model_score = float(rules.get("model_conflict_score", 0.15))
            uncertainties.append("型号不一致")
            penalties.append("model_conflict")

        weight_brand = float(rules.get("weight_brand", 0.6))
        weight_model = float(rules.get("weight_model", 0.4))
        total_weight = (weight_brand + weight_model) or 1.0
        score = clamp((brand_score * weight_brand + model_score * weight_model) / total_weight)
        return DimensionScore(
            name="brand_model", score=score, weight=weight,
            reason=f"品牌 {brand_score:.2f} / 型号 {model_score:.2f}",
            evidence=evidence, uncertainties=uncertainties,
            detail={"brand": brand_score, "model": model_score, "penalties": penalties},
        )

    # -- 时间 / 地点 -------------------------------------------------------
    def score_time_dimension(self, left: ItemView, right: ItemView,
                             window_left: Optional[TimeWindow] = None,
                             window_right: Optional[TimeWindow] = None) -> DimensionScore:
        weight = self.weights.get("time", 0.0)
        result = score_time(
            window_left or parse_time_window(left.raw_time, self.config),
            window_right or parse_time_window(right.raw_time, self.config),
            self.config,
        )
        evidence: List[str] = []
        uncertainties: List[str] = []
        if not result.known:
            uncertainties.append("时间信息缺失，时间维度按中性分处理")
        elif result.diff_hours is not None and result.diff_hours <= 2.0:
            evidence.append("时间高度吻合")
        elif result.diff_hours is not None and result.diff_hours > 72.0:
            uncertainties.append("时间相差过大，存在拾得物与失物并非同一件的可能")
        return DimensionScore(
            name="time", score=result.score, weight=weight, reason=result.reason,
            evidence=evidence, uncertainties=uncertainties,
            detail={"diff_hours": None if result.diff_hours is None else round(result.diff_hours, 4),
                    "tier": result.tier},
        )

    def score_location_dimension(self, left: ItemView, right: ItemView,
                                 parsed_left: Optional[ParsedLocation] = None,
                                 parsed_right: Optional[ParsedLocation] = None) -> DimensionScore:
        weight = self.weights.get("location", 0.0)
        result = score_locations(
            left.location, right.location, self.index,
            left_parsed=parsed_left, right_parsed=parsed_right,
        )
        evidence: List[str] = []
        uncertainties: List[str] = []
        if result.tier in ("same_spot", "same_floor"):
            evidence.append("地点高度吻合")
        elif result.tier == "same_building_diff_floor":
            evidence.append("同一建筑不同楼层")
        elif result.tier == "adjacent_building":
            evidence.append("地点相邻")
        elif result.tier == "unknown":
            uncertainties.append("地点信息缺失或无法解析，按中性分处理")
        elif result.tier == "cross_campus":
            uncertainties.append("分属不同校区，地点差异很大")
        else:
            uncertainties.append("同校区但距离较远")
        return DimensionScore(
            name="location", score=result.score, weight=weight, reason=result.reason,
            evidence=evidence, uncertainties=uncertainties,
            detail=result.to_dict(),
        )

    # -- 总分 -------------------------------------------------------------
    def _adjustments(self, dimensions: Dict[str, DimensionScore]) -> float:
        rules = self.config.rule("adjustments", {}) or {}
        penalties = rules.get("conflict_penalty") or {}
        bonuses = rules.get("bonus") or {}
        total = 0.0
        for dimension in dimensions.values():
            for name in dimension.detail.get("penalties", []) or []:
                total -= float(penalties.get(name, 0.0) or 0.0)
        if dimensions.get("text") and dimensions["text"].detail.get("matched_features"):
            total += float(bonuses.get("hidden_feature_hit", 0.0) or 0.0)
        return total

    def match_pair(
        self,
        query_item: Optional[Dict[str, Any]],
        candidate_item: Optional[Dict[str, Any]],
        query_view: Optional[ItemView] = None,
        candidate_view: Optional[ItemView] = None,
    ) -> MatchOutcome:
        left = query_view or ItemView.from_payload(query_item, self.extractor)
        right = candidate_view or ItemView.from_payload(candidate_item, self.extractor)
        dimensions = {
            "category": self.score_category(left, right),
            "text": self.score_text(left, right),
            "image": self.score_image(left, right),
            "time": self.score_time_dimension(left, right),
            "location": self.score_location_dimension(left, right),
            "brand_model": self.score_brand_model(left, right),
        }
        raw_total = sum(dimensions[name].score * self.weights.get(name, 0.0) for name in DIMENSIONS)
        adjustment = self._adjustments(dimensions)
        total = clamp(raw_total + adjustment,
                      float(self.config.scoring.get("clamp_min", 0.0) or 0.0),
                      float(self.config.scoring.get("clamp_max", 1.0) or 1.0))

        evidence: List[str] = []
        uncertainties: List[str] = []
        for name in DIMENSIONS:
            dimension = dimensions[name]
            for item in dimension.evidence:
                if item not in evidence:
                    evidence.append(item)
            for item in dimension.uncertainties:
                # 不做跨维度去重：不同维度的同名提示（如「品牌未确认」来自类别与品牌两个维度）
                # 都保留，避免丢失可解释信息
                uncertainties.append(item)
        return MatchOutcome(
            candidate_id=right.id,
            total_score=total,
            scores={name: dimensions[name].score for name in DIMENSIONS},
            evidence=evidence,
            uncertainties=uncertainties,
            dimensions=dimensions,
            applied_adjustments=adjustment,
            debug={"weighted_sum": raw_total, "left": left.category, "right": right.category},
        )

    def match(
        self,
        query_item: Optional[Dict[str, Any]],
        candidate_items: Optional[Sequence[Dict[str, Any]]],
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """主入口：返回接口契约要求的响应体。"""
        candidates = list(candidate_items or [])
        if not query_item or not isinstance(query_item, dict):
            return {
                "model_version": self.config.model_version,
                "results": [],
                "warnings": ["query_item 缺失或为空，未执行匹配"],
            }
        query_view = ItemView.from_payload(query_item, self.extractor)
        outcomes: List[MatchOutcome] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            try:
                outcomes.append(self.match_pair(query_item, candidate, query_view=query_view))
            except Exception as exc:  # noqa: BLE001 - 单条候选异常不影响整体
                outcomes.append(MatchOutcome(
                    candidate_id=candidate.get("id"),
                    total_score=0.0,
                    scores={name: 0.0 for name in DIMENSIONS},
                    evidence=[],
                    uncertainties=[f"该候选条目处理失败（{exc.__class__.__name__}），已跳过"],
                ))
        outcomes.sort(key=lambda item: (-item.total_score, str(item.candidate_id)))
        limit = int(top_k or self.config.scoring.get("max_results", 20) or 20)
        return {
            "model_version": self.config.model_version,
            "results": [outcome.to_dict() for outcome in outcomes[:max(0, limit)]],
        }


__all__ = [
    "DIMENSION_LABELS",
    "ImageEmbedder",
    "MockImageEmbedder",
    "cosine_similarity",
    "register_image_embedder",
    "get_image_embedder",
    "available_image_backends",
    "TimeWindow",
    "TimeScore",
    "parse_time_window",
    "time_score_from_diff",
    "score_time",
    "DimensionScore",
    "MatchOutcome",
    "ItemView",
    "MatchEngine",
]
