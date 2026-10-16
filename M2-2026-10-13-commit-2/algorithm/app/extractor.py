"""属性提取：图片 URL + 描述文本 → 固定 JSON Schema 的结构化属性。

* **纯规则 / 纯标准库**：类别词表、品牌词表、颜色词表、材质词表、特征正则、
  配件词表、不确定性表述全部来自 ``config/matching.yaml`` 的 ``rules.*``。
* **不抛异常**：输入为空、类型错误、完全无法识别时，返回带 ``uncertainties``
  的降级结果（各字段为 null / 空列表）。
* **保留不确定性**：文本中出现「疑似 / 好像 / 可能」等表述时，抽取结果会被标注
  为推测（写入 ``uncertainties``），绝不当成确定事实。

输出 Schema（:data:`ATTRIBUTE_SCHEMA`）::

    {
      "category": str|null, "brand": str|null, "model": str|null,
      "color": str|null, "material": str|null,
      "visible_features": [str], "accessories": [str],
      "keywords": [str], "uncertainties": [str]
    }
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .config_loader import RuntimeConfig, load_config
from .text_utils import as_text_list, normalize, safe_float

#: 输出字段的 JSON Schema（用于结果校验；纯 Python 校验器见 validate_attributes）
ATTRIBUTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": [
        "category", "brand", "model", "color", "material",
        "visible_features", "accessories", "keywords", "uncertainties",
    ],
    "additionalProperties": False,
    "properties": {
        "category": {"type": ["string", "null"], "maxLength": 32},
        "brand": {"type": ["string", "null"], "maxLength": 32},
        "model": {"type": ["string", "null"], "maxLength": 64},
        "color": {"type": ["string", "null"], "maxLength": 16},
        "material": {"type": ["string", "null"], "maxLength": 16},
        "visible_features": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 64}},
        "accessories": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 32}},
        "keywords": {"type": "array", "maxItems": 24, "items": {"type": "string", "maxLength": 24}},
        "uncertainties": {"type": "array", "maxItems": 16, "items": {"type": "string", "maxLength": 96}},
    },
}

# 型号常见写法：字母+数字组合（FreeBuds 5i / WH-1000XM4 / ZBJ-1500 / A2140 / M1）
_MODEL_PATTERNS: Sequence[str] = (
    r"\b([A-Za-z]{1,6}[- ]?\d{2,5}[A-Za-z]{0,3})\b",
    r"\b([A-Z]{1,4}\d{2,5}[A-Z]{0,3})\b",
    r"型号[:：]?\s*([A-Za-z0-9\- ]{2,20})",
)

_SCHEMA_TYPE_MAP = {
    "string": str,
    "array": list,
    "object": dict,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate_attributes(payload: Any, schema: Optional[Dict[str, Any]] = None) -> List[str]:
    """纯标准库的 JSON Schema 子集校验器，返回错误列表（空列表 = 通过）。"""
    schema = schema or ATTRIBUTE_SCHEMA
    errors: List[str] = []

    def check(value: Any, node: Dict[str, Any], path: str) -> None:
        expected = node.get("type")
        if expected:
            types = expected if isinstance(expected, list) else [expected]
            allowed = tuple(
                cls for name in types for cls in (
                    _SCHEMA_TYPE_MAP[name] if isinstance(_SCHEMA_TYPE_MAP.get(name), tuple)
                    else (_SCHEMA_TYPE_MAP.get(name),)
                ) if cls is not None
            )
            # bool 是 int 的子类，单独排除避免误判
            if allowed and (not isinstance(value, allowed) or (isinstance(value, bool) and bool not in allowed)):
                errors.append(f"{path}: 类型应为 {types}，实际为 {type(value).__name__}")
                return
        if isinstance(value, str):
            if "maxLength" in node and len(value) > int(node["maxLength"]):
                errors.append(f"{path}: 长度超过 {node['maxLength']}")
        if isinstance(value, list):
            if "maxItems" in node and len(value) > int(node["maxItems"]):
                errors.append(f"{path}: 元素数量超过 {node['maxItems']}")
            item_schema = node.get("items")
            if isinstance(item_schema, dict):
                for index, item in enumerate(value):
                    check(item, item_schema, f"{path}[{index}]")
        if isinstance(value, dict):
            for key in node.get("required", []) or []:
                if key not in value:
                    errors.append(f"{path}: 缺少必填字段 {key}")
            if node.get("additionalProperties") is False:
                for key in value:
                    if key not in (node.get("properties") or {}):
                        errors.append(f"{path}: 出现未定义字段 {key}")
            for key, sub_schema in (node.get("properties") or {}).items():
                if key in value and isinstance(sub_schema, dict):
                    check(value[key], sub_schema, f"{path}.{key}")

    check(payload, schema, "$")
    return errors


@dataclass
class ExtractedAttributes:
    """属性提取结果（可直接 json 序列化）。"""

    category: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    material: Optional[str] = None
    visible_features: List[str] = field(default_factory=list)
    accessories: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    # 调试/溯源信息（不属于对外 Schema，序列化时会剔除）
    debug: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_debug: bool = False) -> Dict[str, Any]:
        data = asdict(self)
        if not include_debug:
            data.pop("debug", None)
        return data


class AttributeExtractor:
    """基于规则表的属性抽取器（确定性、无外部依赖）。"""

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self.config = config or load_config()
        rules = self.config.rules
        self._category_keywords = self._compile_keyword_table(rules.get("category_keywords") or {})
        self._brand_keywords = self._compile_flat_table(rules.get("brand_keywords") or [])
        self._color_table = self._compile_keyword_table(rules.get("color_keywords") or {})
        self._material_table = self._compile_keyword_table(rules.get("material_keywords") or {})
        self._accessory_table = self._compile_keyword_table(rules.get("accessory_keywords") or {})
        self._feature_patterns: List[Tuple[re.Pattern, str]] = []
        for entry in rules.get("feature_patterns") or []:
            if not isinstance(entry, dict) or not entry.get("pattern"):
                continue
            try:
                self._feature_patterns.append((re.compile(str(entry["pattern"])), str(entry.get("label", "特征"))))
            except re.error:
                continue
        self._uncertainty_markers = [str(x) for x in (rules.get("uncertainty_markers") or [])]
        self._normalization = rules.get("text_normalization") or {}
        self._keyword_rules = rules.get("keyword_rules") or {}
        self._brand_aliases = {
            normalize(str(key)): str(value)
            for key, value in ((rules.get("brand_model") or {}).get("brand_aliases") or {}).items()
            if value
        }

    # -- 词表编译 ---------------------------------------------------------
    @staticmethod
    def _compile_keyword_table(table: Dict[str, Any]) -> List[Tuple[str, str]]:
        """把 {规范名: [触发词...]} 编译成按触发词长度倒序的 (触发词, 规范名) 列表。"""
        pairs: List[Tuple[str, str]] = []
        for canonical, triggers in (table or {}).items():
            values = triggers if isinstance(triggers, (list, tuple)) else [triggers]
            for trigger in values:
                key = normalize(trigger)
                if key:
                    pairs.append((key, str(canonical)))
            canonical_key = normalize(canonical)
            if canonical_key:
                pairs.append((canonical_key, str(canonical)))
        pairs.sort(key=lambda item: -len(item[0]))
        return pairs

    @staticmethod
    def _compile_flat_table(values: Iterable[Any]) -> List[str]:
        return sorted({normalize(v) for v in values if normalize(v)}, key=len, reverse=True)

    # -- 基础抽取 ---------------------------------------------------------
    def _norm_text(self, text: Any, strip_punctuation: bool = False) -> str:
        cfg = self._normalization
        return normalize(
            text,
            fullwidth_to_halfwidth=bool(cfg.get("fullwidth_to_halfwidth", True)),
            lowercase=bool(cfg.get("lowercase", True)),
            strip_whitespace=False,          # 保留空格便于匹配型号
            strip_punctuation=strip_punctuation,  # 特征抽取时保留标点以免跨句粘连
            max_length=int(cfg.get("max_length", 200) or 200),
        )

    def match_category(self, text: str) -> Optional[str]:
        key = self._norm_text(text)
        for trigger, canonical in self._category_keywords:
            if trigger and trigger in key:
                return canonical
        return None

    def match_color(self, text: str) -> Optional[str]:
        key = self._norm_text(text)
        for trigger, canonical in self._color_table:
            if trigger and trigger in key:
                return canonical
        return None

    def match_material(self, text: str) -> Optional[str]:
        key = self._norm_text(text)
        for trigger, canonical in self._material_table:
            if trigger and trigger in key:
                return canonical
        return None

    def match_brand(self, text: str) -> Optional[str]:
        key = self._norm_text(text)
        for trigger in self._brand_keywords:
            if trigger and trigger in key:
                return self._brand_aliases.get(trigger, trigger)
        return None

    # -- 规范化（把实体的不同写法映射到规范值） ---------------------------
    def canonical_brand(self, value: Any) -> Optional[str]:
        """品牌归一：命中词表 → 别名规范值；否则 None。"""
        if value is None or str(value).strip() == "":
            return None
        return self.match_brand(str(value))

    def canonical_color(self, value: Any) -> Optional[str]:
        """颜色归一：命中颜色词表 → 规范色名（如「深黑」→「黑色」）；否则 None。"""
        if value is None or str(value).strip() == "":
            return None
        return self.match_color(str(value))

    def canonical_material(self, value: Any) -> Optional[str]:
        """材质归一：命中材质词表 → 规范材质名；否则 None。"""
        if value is None or str(value).strip() == "":
            return None
        return self.match_material(str(value))

    def match_model(self, text: str, allow: bool = True) -> Optional[str]:
        """抽型号（字母+数字组合）。``allow=False`` 时可关闭（如在图片文件名里找型号）。"""
        if not allow:
            return None
        key = self._norm_text(text)
        for pattern in _MODEL_PATTERNS:
            match = re.search(pattern, key)
            if match:
                candidate = match.group(1).strip()
                if len(candidate) >= 2 and any(ch.isdigit() for ch in candidate):
                    return candidate
        return None

    def match_accessories(self, text: str, limit: int = 8) -> List[str]:
        key = self._norm_text(text)
        found: List[str] = []
        for trigger, canonical in self._accessory_table:
            if trigger and trigger in key and canonical not in found:
                found.append(canonical)
            if len(found) >= limit:
                break
        return found

    def match_features(self, text: str, limit: int = 6) -> List[str]:
        raw = "" if text is None else str(text)
        features: List[str] = []
        for pattern, label in self._feature_patterns:
            for match in pattern.finditer(raw):
                phrase = match.group(0).strip(" ，。；;、,.")
                if len(phrase) < 3:
                    continue
                if phrase not in features:
                    features.append(phrase)
                break  # 每个规则只取第一条，避免重复堆叠
            if len(features) >= limit:
                break
        return features[:limit]
    def has_uncertainty_marker(self, text: Any) -> Optional[str]:
        if not text:
            return None
        raw = str(text)
        for marker in self._uncertainty_markers:
            if marker and marker in raw:
                return marker
        return None

    # -- 关键词 -----------------------------------------------------------
    def extract_keywords(self, *texts: Any, extra: Optional[Iterable[str]] = None) -> List[str]:
        """规则式中文关键词抽取：词表命中（类别/颜色/品牌/材质/配件/特征）+ 名词短语。"""
        blob_parts = [str(t) for t in texts if t]
        blob = " ".join(blob_parts)
        if not blob.strip():
            return []
        key = self._norm_text(blob)
        collected: List[Tuple[str, int]] = []  # (关键词, 优先级序号)

        def add(value: Optional[str], rank: int) -> None:
            if not value:
                return
            text_value = str(value)
            if len(text_value) < int(self._keyword_rules.get("min_length", 2) or 2):
                return
            if text_value not in [item[0] for item in collected]:
                collected.append((text_value, rank))

        order = [str(x) for x in (self._keyword_rules.get("include_source_tags") or
                                  ["category", "color", "brand", "material", "accessory", "feature"])]
        rank = 0
        if "category" in order:
            add(self.match_category(blob), rank)
        rank += 1
        if "color" in order:
            add(self.match_color(blob), rank)
        rank += 1
        if "brand" in order:
            add(self.match_brand(blob), rank)
        rank += 1
        if "material" in order:
            add(self.match_material(blob), rank)
        rank += 1
        if "accessory" in order:
            for item in self.match_accessories(blob, limit=4):
                add(item, rank)
        rank += 1
        if "feature" in order:
            for item in self.match_features(blob, limit=3):
                add(item, rank)
        rank += 1
        for item in extra or []:
            add(str(item), rank)

        if bool(self._keyword_rules.get("noun_phrase_enabled", True)):
            for phrase, freq in self._noun_phrases(blob):
                if phrase not in [item[0] for item in collected]:
                    collected.append((phrase, rank + 10 - min(freq, 9)))

        collected.sort(key=lambda item: (item[1], -len(item[0])))
        max_keywords = int(self._keyword_rules.get("max_keywords", 12) or 12)
        max_length = int(self._keyword_rules.get("max_length", 12) or 12)
        return [word for word, _rank in collected if len(word) <= max_length][:max_keywords]

    def _noun_phrases(self, text: str, limit: int = 6) -> List[Tuple[str, int]]:
        """滑窗抽取纯汉字名词短语；含虚词/助词或命中停用词的片段直接丢弃。"""
        stopwords = {str(x) for x in (self._keyword_rules.get("generic_stopwords") or [])}
        stopchars = {str(x) for x in (self._keyword_rules.get("noun_phrase_stopchars") or [])}
        lengths = self._keyword_rules.get("noun_phrase_length") or [2, 4]
        low, high = int(lengths[0]), int(lengths[1])
        counts: Dict[str, int] = {}
        for size in range(high, low - 1, -1):  # 长词优先
            for index in range(0, max(0, len(text) - size + 1)):
                phrase = text[index:index + size]
                if not all("\u4e00" <= ch <= "\u9fff" for ch in phrase):
                    continue
                if phrase in stopwords or any(ch in stopchars for ch in phrase):
                    continue
                counts[phrase] = counts.get(phrase, 0) + 1
            if len(counts) >= limit:
                break
        ranked = sorted(counts.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
        return ranked[:limit]

    # -- 主入口 -----------------------------------------------------------
    def extract(
        self,
        description: Any = None,
        title: Any = None,
        category: Any = None,
        image_url: Any = None,
        attributes: Optional[Dict[str, Any]] = None,
        public_features: Optional[Sequence[Any]] = None,
        image_filename: Any = None,
    ) -> ExtractedAttributes:
        """抽取属性；任何异常都被吞掉并转成 uncertainties。"""
        uncertainties: List[str] = []
        try:
            return self._extract_inner(
                description, title, category, image_url, attributes, public_features, image_filename, uncertainties
            )
        except Exception as exc:  # noqa: BLE001 - 降级而不是崩溃
            fallback = ExtractedAttributes(uncertainties=uncertainties + [
                f"属性提取过程出现异常（{exc.__class__.__name__}），已返回降级结果"
            ])
            fallback.debug["error"] = repr(exc)
            return fallback

    def _extract_inner(
        self,
        description: Any,
        title: Any,
        category: Any,
        image_url: Any,
        attributes: Optional[Dict[str, Any]],
        public_features: Optional[Sequence[Any]],
        image_filename: Any,
        uncertainties: List[str],
    ) -> ExtractedAttributes:
        attributes = attributes if isinstance(attributes, dict) else {}
        description_text = "" if description is None else str(description)
        title_text = "" if title is None else str(title)
        image_text = " ".join(str(x) for x in (image_url, image_filename) if x)
        # 图片 URL / 文件名也参与规则匹配（文件名常带颜色、类别等线索）
        blob = " ".join(part for part in (title_text, description_text, image_text) if part).strip()
        result = ExtractedAttributes()

        if not blob.strip() or not any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in blob):
            result.uncertainties = ["未提供可用的描述文本或图片信息，无法提取属性"]
            result.debug["empty_input"] = True
            return result

        attr_category = attributes.get("category") if isinstance(attributes, dict) else None
        declared_category = attr_category or category
        result.category = str(declared_category) if declared_category else self.match_category(blob)
        if not result.category:
            uncertainties.append("类别未能识别，建议人工补充")

        declared_brand = attributes.get("brand")
        result.brand = str(declared_brand) if declared_brand else self.match_brand(blob)
        if not result.brand:
            uncertainties.append("品牌标识不清晰或未提供，未能确认品牌")

        declared_model = attributes.get("model") or attributes.get("brand_model")
        # NOTE: 型号只在标题/描述里找。图片文件名里的 "lost-1001-v0" 这类串不是型号，
        #       否则会把演示数据的文件命名误当成型号（同一物品的正负样本都会被污染）。
        result.model = str(declared_model) if declared_model else self.match_model(
            " ".join(part for part in (title_text, description_text) if part))
        if result.model is None and result.brand:
            uncertainties.append("未识别到明确型号")

        declared_color = attributes.get("color")
        result.color = str(declared_color) if declared_color else self.match_color(blob)
        if not result.color:
            uncertainties.append("颜色未能从描述或图片线索中确认")

        declared_material = attributes.get("material")
        result.material = str(declared_material) if declared_material else self.match_material(blob)

        features = [str(x).strip() for x in (public_features or []) if str(x).strip()]
        for feature in self.match_features(description_text or title_text):
            if feature not in features:
                features.append(feature)
        result.visible_features = features[:12]
        result.accessories = self.match_accessories(blob)
        if not result.accessories:
            uncertainties.append("未识别到明确配件信息")

        # 不确定性表述：只标注，不臆断
        marker = self.has_uncertainty_marker(description_text) or self.has_uncertainty_marker(title_text)
        if marker:
            uncertainties.append(f"描述中出现「{marker}」等不确定表述，相关特征仅作推测")
        if not result.category and not result.color and not result.visible_features:
            uncertainties.append("可用信息过少，抽取结果置信度低")

        keyword_extra = []
        if declared_category:
            keyword_extra.append(str(declared_category))
        if declared_brand:
            keyword_extra.append(str(declared_brand))
        result.keywords = self.extract_keywords(title_text, description_text, extra=keyword_extra)
        result.uncertainties = self._dedupe(uncertainties)
        result.debug = {
            "blob_length": len(blob),
            "used_image_hint": bool(image_text),
            "feature_rules": len(self._feature_patterns),
        }
        # 自校验：结果必须符合 ATTRIBUTE_SCHEMA
        errors = validate_attributes(result.to_dict())
        if errors:
            result.uncertainties = self._dedupe(result.uncertainties + ["抽取结果未通过 Schema 校验，已降级"])
            result.debug["schema_errors"] = errors
        return result

    @staticmethod
    def _dedupe(values: Iterable[str]) -> List[str]:
        out: List[str] = []
        for value in values:
            if value and value not in out:
                out.append(value)
        return out


# ---------------------------------------------------------------------------
# 模块级便捷函数
# ---------------------------------------------------------------------------
def extract_attributes(
    image_url: Any = None,
    description: Any = None,
    title: Any = None,
    category: Any = None,
    attributes: Optional[Dict[str, Any]] = None,
    config: Optional[RuntimeConfig] = None,
) -> Dict[str, Any]:
    """便捷入口：返回符合 :data:`ATTRIBUTE_SCHEMA` 的 dict（不抛异常）。"""
    extractor = AttributeExtractor(config)
    result = extractor.extract(
        description=description, title=title, category=category,
        image_url=image_url, attributes=attributes,
    )
    payload = result.to_dict()
    errors = validate_attributes(payload)
    if errors:  # 理论上不会发生，双重保险：兜底成空结构
        return ExtractedAttributes(uncertainties=["属性提取结果异常，已返回空结构"]).to_dict()
    return payload


__all__ = [
    "ATTRIBUTE_SCHEMA",
    "ExtractedAttributes",
    "AttributeExtractor",
    "extract_attributes",
    "validate_attributes",
]
