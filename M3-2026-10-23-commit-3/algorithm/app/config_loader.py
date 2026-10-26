"""配置加载器：把 ``config/*.yaml`` 读成运行时配置对象。

设计要点
--------
1. **无强制第三方依赖**：优先使用 PyYAML；环境里没有 PyYAML 时退回到内置的
   ``_parse_minimal_yaml``（支持本项目配置用到的 YAML 子集：块映射、块序列、
   行内流式 ``[a, b]`` / ``{k: v}``、注释、引号、null/true/false、数字）。
2. **绝不抛异常**：任何文件缺失、解析失败、字段缺失，都退回到 ``BUILTIN_FALLBACK``
   并把原因写入 :attr:`RuntimeConfig.warnings`，服务照常运行。
3. **唯一配置源**：权重 / 阈值 / 规则词表全部来自 YAML；代码里只有数值与 YAML
   完全一致的兜底副本（用于文件不可读时），不存在第二处"真值"。
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

# 维度的规范顺序（接口契约里的 scores 字段顺序）
DIMENSIONS = ("category", "text", "image", "time", "location", "brand_model")

#: 权重定义必须满足的等式（README / 测试都会核对这一点）
WEIGHT_SUM_EXPECTED = 1.0


class ConfigError(Exception):
    """配置加载错误（仅在显式调用 strict 模式时使用）。"""


# ---------------------------------------------------------------------------
# 内置兜底配置：数值与 config/*.yaml 完全一致，仅用于文件不可读的极端情况
# ---------------------------------------------------------------------------
BUILTIN_FALLBACK: Dict[str, Any] = {
    "matching": {
        "version": "baseline-1.0",
        "model_version": "baseline-1.0",
        "weights": {
            "category": 0.20,
            "text": 0.25,
            "image": 0.20,
            "time": 0.15,
            "location": 0.15,
            "brand_model": 0.05,
        },
        "scoring": {
            "normalize_min": 0.0,
            "normalize_max": 1.0,
            "max_results": 20,
            "clamp_min": 0.0,
            "clamp_max": 1.0,
        },
        "neutral_scores": {
            "category": 0.50,
            "text": 0.50,
            "image": 0.50,
            "time": 0.50,
            "location": 0.50,
            "brand_model": 0.50,
        },
        "rules": {
            "category": {
                "same_category_score": 1.0,
                "same_family_score": 0.60,
                "related_score": 0.25,
                "mismatch_score": 0.0,
                "families": [["无线耳机", "充电盒"], ["校园卡", "证件卡"]],
                "related_pairs": [["背包", "雨伞"], ["书籍", "校园卡"]],
            },
            "text": {
                "component_weights": {"char_ngram": 0.30, "keyword_overlap": 0.25, "attribute_match": 0.45},
                "ngram_sizes": [2, 3],
                "ngram_similarity": "dice",
                "min_text_length": 2,
                "color_score": 1.0,
                "color_conflict_score": 0.10,
                "color_partial_score": 0.60,
                "material_score": 1.0,
                "material_conflict_score": 0.20,
                "material_neutral": 0.50,
                "feature_pair_threshold": 0.42,
                "feature_pairs_weight": 0.60,
                "feature_attr_weight": 0.40,
                "brand_hint_weight": 0.15,
                "max_text_score_on_color_conflict": 0.65,
                "max_text_score_on_material_conflict": 0.80,
                "max_text_score_on_feature_mismatch": 0.70,
                "feature_gate_coverage": 0.60,
                "calibration": [
                    {"min": 0.62, "score": 1.00},
                    {"min": 0.46, "score": 0.85},
                    {"min": 0.30, "score": 0.62},
                    {"min": 0.15, "score": 0.42},
                    {"min": 0.00, "score": 0.25},
                ],
            },
            "image": {
                "backend": "mock",
                "dimension": 64,
                "seed": "shiguang-image-v1",
                "min_similarity": 0.0,
                "max_similarity": 1.0,
                "similarity_scale": 1.0,
                "text_fallback_fields": ["category", "title", "description"],
                "mock_token_weights": {
                    "category": 3.0,
                    "color": 2.0,
                    "brand": 1.5,
                    "model": 1.5,
                    "material": 1.0,
                    "features": 2.0,
                    "accessories": 0.5,
                    "image_name": 2.0,
                },
                "mock_debug": False,
            },
            "brand_model": {
                "weight_brand": 0.60,
                "weight_model": 0.40,
                "brand_exact_score": 1.0,
                "brand_alias_score": 0.85,
                "brand_unknown_neutral": 0.50,
                "brand_conflict_score": 0.05,
                "model_exact_score": 1.0,
                "model_partial_score": 0.70,
                "model_unknown_neutral": 0.50,
                "model_conflict_score": 0.15,
            },
            "time": {
                "unit": "hour",
                "range_mode": "point_to_range_shortest",
                "low_diff_score": 1.00,
                "fallback_score": 0.10,
                "segments": [
                    {"name": "within_2h", "max_diff": 2.0, "score": 1.00, "interpolation": "flat"},
                    {"name": "2h_to_12h", "max_diff": 12.0, "score": 0.80, "interpolation": "linear"},
                    {"name": "12h_to_24h", "max_diff": 24.0, "score": 0.60, "interpolation": "step"},
                    {"name": "24h_to_72h", "max_diff": 72.0, "score": 0.30, "interpolation": "step"},
                    {"name": "beyond_72h", "max_diff": None, "score": 0.10, "interpolation": "flat"},
                ],
                "event_time_formats": [
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M",
                    "%Y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M",
                    "%Y-%m-%d",
                    "%Y/%m/%d",
                ],
            },
        },
    },
    "locations": {
        "version": "1.0",
        "campuses": [],
        "output": {
            "tiers": {
                "same_spot": 1.00,
                "same_floor": 1.00,
                "same_building_diff_floor": 0.85,
                "adjacent_building": 0.60,
                "same_campus_far": 0.35,
                "cross_campus": 0.10,
                "unknown": 0.50,
            },
            "tier_labels": {
                "same_spot": "同一具体地点",
                "same_floor": "同一建筑同一楼层",
                "same_building_diff_floor": "同一建筑不同楼层",
                "adjacent_building": "相邻建筑",
                "same_campus_far": "同一校区较远地点",
                "cross_campus": "不同校区",
                "unknown": "地点未知",
            },
            "parsing": {"floor_patterns": ["([一二三四五六七八九十0-9]{1,2})(?:楼|层)", "F([0-9]{1,2})"]},
        },
        "keyword_hints": {},
    },
    "questions": {
        "version": "1.0",
        "weights": {"answer_match": 0.60, "candidate_match": 0.20, "completeness": 0.10, "behavior": 0.10},
        "neutral_scores": {"answer_match": 0.50, "candidate_match": 0.50, "completeness": 0.30, "behavior": 0.85},
        "scoring": {
            "clamp_min": 0.0,
            "clamp_max": 1.0,
            "round_digits": 4,
            "risk_levels": {"low_min": 0.80, "medium_min": 0.55, "high_below": 0.55},
        },
        "answer_matching": {
            "normalization": {
                "fullwidth_to_halfwidth": True,
                "lowercase": True,
                "strip_whitespace": True,
                "strip_punctuation": True,
                "unicodedata_nfkc": True,
            },
            "fuzzy": {
                "ngram_size": 2,
                "confident_threshold": 0.62,
                "partial_threshold": 0.34,
                "confident_score": 1.0,
                "partial_score": 0.50,
                "below_score": 0.0,
            },
            "exact": {
                "match_score": 1.0,
                "partial_score": 0.60,
                "mismatch_score": 0.0,
                "confusion_pairs": [["0", "o"], ["1", "l"], ["1", "i"]],
            },
            "unanswered": {"score": 0.0, "reason": "该问题未作答或答案过短，无法验证"},
            "min_answer_length": 2,
            "max_reason_length": 60,
        },
        "synonym_groups": [],
        "completeness": {
            "fields": {
                "title": 0.10,
                "description": 0.15,
                "category": 0.10,
                "color": 0.10,
                "brand": 0.05,
                "location": 0.15,
                "event_time": 0.15,
                "image_url": 0.05,
                "public_features": 0.15,
            },
            "description_min_length": 12,
        },
        "behavior": {"base_score": 1.0, "rules": []},
        "risk_tips": {"high": [], "medium": [], "low": []},
        "question_templates": [],
        "question_selection": {"max_questions": 3, "min_questions": 1, "prefer_required": True},
    },
}


# ---------------------------------------------------------------------------
# 极简 YAML 解析器（PyYAML 缺失时使用；只覆盖本项目配置用到的子集）
# ---------------------------------------------------------------------------
_NULL_TOKENS = {"null", "none", "~", ""}


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _split_top_level(text: str, sep: str = ",") -> List[str]:
    """按 sep 切分，忽略引号与嵌套括号内的分隔符。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip() != "" or len(parts) == 1]


def _split_key_value(text: str):
    """在顶层把 ``key: value`` 拆开；找不到冒号时返回 (None, None)。"""
    quote, depth = None, 0
    for idx, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            return text[:idx].strip(), text[idx + 1:].strip()
    return None, None


def _scalar(token: str) -> Any:
    token = token.strip()
    if token == "":
        return None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    lowered = token.lower()
    if lowered in _NULL_TOKENS:
        return None
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    if token[0] in "[{":
        try:
            return ast.literal_eval(token)
        except (ValueError, SyntaxError):
            return token
    return token


def _parse_flow_list(text: str) -> List[Any]:
    inner = text.strip()[1:-1]
    if inner.strip() == "":
        return []
    return [_parse_value(p) for p in _split_top_level(inner, ",")]


def _parse_flow_map(text: str) -> Dict[str, Any]:
    inner = text.strip()[1:-1]
    result: Dict[str, Any] = {}
    if inner.strip() == "":
        return result
    for part in _split_top_level(inner, ","):
        key, value = _split_key_value(part)
        if key is None:
            continue
        result[str(_scalar(key))] = _parse_value(value)
    return result


def _parse_value(text: str) -> Any:
    text = (text or "").strip()
    if text.startswith("[") and text.endswith("]"):
        return _parse_flow_list(text)
    if text.startswith("{") and text.endswith("}"):
        return _parse_flow_map(text)
    return _scalar(text)


def _parse_minimal_yaml(text: str) -> Any:
    """解析 YAML 子集。失败时抛 ValueError，由调用方降级处理。"""
    lines: List[Any] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        cleaned = _strip_comment(raw)
        if not cleaned.strip():
            continue
        lines.append((len(cleaned) - len(cleaned.lstrip(" ")), cleaned.strip()))

    def parse_block(start: int, indent: int):
        block: Any = None
        i = start
        while i < len(lines):
            cur_indent, content = lines[i]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise ValueError("非预期的缩进层级")
            if content.startswith("- "):
                if block is None:
                    block = []
                if not isinstance(block, list):
                    raise ValueError("序列与映射混用")
                item_text = content[2:].strip()
                key, value = _split_key_value(item_text) if item_text else (None, None)
                if key is not None:
                    item: Dict[str, Any] = {}
                    if value:
                        item[str(_scalar(key))] = _parse_value(value)
                        i += 1
                    else:
                        sub, i = parse_block(i + 1, _next_indent(i + 1, indent))
                        item[str(_scalar(key))] = sub
                    while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                        break
                    # 同一序项后续的 key: value 行
                    while i < len(lines) and lines[i][0] > indent and not lines[i][1].startswith("- "):
                        k2, v2 = _split_key_value(lines[i][1])
                        if k2 is None:
                            raise ValueError("序列项内出现非法行")
                        if v2:
                            item[str(_scalar(k2))] = _parse_value(v2)
                            i += 1
                        else:
                            sub, i = parse_block(i + 1, _next_indent(i + 1, indent))
                            item[str(_scalar(k2))] = sub
                    block.append(item)
                else:
                    if item_text == "":
                        sub, i = parse_block(i + 1, _next_indent(i + 1, indent))
                        block.append(sub)
                    else:
                        block.append(_parse_value(item_text))
                        i += 1
            else:
                if block is None:
                    block = {}
                if not isinstance(block, dict):
                    raise ValueError("映射与序列混用")
                key, value = _split_key_value(content)
                if key is None:
                    raise ValueError("缺少冒号")
                if value:
                    block[str(_scalar(key))] = _parse_value(value)
                    i += 1
                else:
                    nxt = _next_indent(i + 1, indent)
                    if i + 1 < len(lines) and lines[i + 1][0] > indent:
                        sub, i = parse_block(i + 1, nxt)
                        block[str(_scalar(key))] = sub
                    else:
                        block[str(_scalar(key))] = None
                        i += 1
        return block, i

    def _next_indent(idx: int, fallback: int) -> int:
        return lines[idx][0] if idx < len(lines) else fallback

    parsed, _ = parse_block(0, lines[0][0] if lines else 0)
    return parsed if parsed is not None else {}


# ---------------------------------------------------------------------------
# 文件加载
# ---------------------------------------------------------------------------
def load_yaml(path: str) -> Dict[str, Any]:
    """读取 YAML 文件；解析失败时抛 ValueError（由 :func:`load_config` 兜底）。"""
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        import yaml  # type: ignore  # 可选依赖
    except ImportError:
        data = _parse_minimal_yaml(text)
    else:
        data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} 的顶层结构不是映射")
    return data


def _deep_merge(base: Any, override: Any, path: str = "", misses: Optional[List[str]] = None) -> Any:
    """把 override 合并进 base（dict 递归，其余整体替换），记录缺失键。"""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(base.get(key), value, f"{path}.{key}" if path else str(key), misses)
        return merged
    if override is None:
        if misses is not None and path:
            misses.append(path)
        return base
    return override


@dataclass
class RuntimeConfig:
    """运行时配置（matching / locations / questions 三份 YAML 的合并视图）。"""

    matching: Dict[str, Any] = field(default_factory=dict)
    locations: Dict[str, Any] = field(default_factory=dict)
    questions: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)

    # -- 便捷访问 ---------------------------------------------------------
    @property
    def model_version(self) -> str:
        return str(self.matching.get("model_version") or self.matching.get("version") or "baseline-1.0")

    @property
    def weights(self) -> Dict[str, float]:
        raw = self.matching.get("weights") or {}
        return {dim: float(raw.get(dim, 0.0)) for dim in DIMENSIONS}

    @property
    def neutral_scores(self) -> Dict[str, float]:
        raw = self.matching.get("neutral_scores") or {}
        return {dim: float(raw.get(dim, 0.5)) for dim in DIMENSIONS}

    @property
    def scoring(self) -> Dict[str, Any]:
        return self.matching.get("scoring") or {}

    @property
    def rules(self) -> Dict[str, Any]:
        return self.matching.get("rules") or {}

    def rule(self, path: str, default: Any = None) -> Any:
        node: Any = self.rules
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def section(self, name: str, default: Any = None) -> Any:
        """按点分路径取任意顶层节点，如 ``time.segments`` / ``behavior.rules``。"""
        for root in (self.matching, self.locations, self.questions):
            node: Any = root
            ok = True
            for part in name.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    ok = False
                    break
            if ok:
                return node
        return default

    @property
    def location_tiers(self) -> Dict[str, float]:
        raw = ((self.locations.get("output") or {}).get("tiers")) or {}
        return {str(k): float(v) for k, v in raw.items()}

    # -- 校验 -------------------------------------------------------------
    def validate(self) -> List[str]:
        """返回问题列表（不抛异常），并给出权重归一化告警。"""
        problems: List[str] = []
        weights = self.weights
        missing = [dim for dim in DIMENSIONS if dim not in (self.matching.get("weights") or {})]
        if missing:
            problems.append(f"weights 缺少维度: {', '.join(missing)}")
        total = sum(weights.values())
        if abs(total - WEIGHT_SUM_EXPECTED) > 1e-9:
            problems.append(f"weights 之和为 {total:.6f}，应为 {WEIGHT_SUM_EXPECTED}")
        if not self.location_tiers:
            problems.append("locations.output.tiers 为空")
        return problems

    def normalized_weights(self) -> Dict[str, float]:
        """权重和不为 1 时按比例归一，保证总分仍在 [0,1]。"""
        weights = self.weights
        total = sum(weights.values())
        if total <= 0 or abs(total - WEIGHT_SUM_EXPECTED) <= 1e-9:
            return weights
        return {dim: value / total for dim, value in weights.items()}


def _load_one(filename: str, key: str, warnings: List[str], sources: Dict[str, str]) -> Dict[str, Any]:
    path = os.path.join(CONFIG_DIR, filename)
    fallback = BUILTIN_FALLBACK[key]
    if not os.path.exists(path):
        warnings.append(f"未找到配置文件 {path}，使用内置兜底配置")
        sources[key] = "builtin-fallback"
        return dict(fallback)
    try:
        data = load_yaml(path)
    except Exception as exc:  # noqa: BLE001 - 任何解析错误都降级
        warnings.append(f"解析 {filename} 失败（{exc.__class__.__name__}: {exc}），使用内置兜底配置")
        sources[key] = "builtin-fallback"
        return dict(fallback)
    misses: List[str] = []
    merged = _deep_merge(fallback, data, key, misses)
    if misses:
        warnings.append(f"{filename} 缺少配置项（已用兜底值）: {', '.join(sorted(set(misses))[:10])}")
    sources[key] = path
    return merged


_CACHE: Optional[RuntimeConfig] = None


def load_config(reload: bool = False) -> RuntimeConfig:
    """加载（并缓存）运行时配置。任何错误都降级为内置配置 + warnings。"""
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    warnings: List[str] = []
    sources: Dict[str, str] = {}
    config = RuntimeConfig(
        matching=_load_one("matching.yaml", "matching", warnings, sources),
        locations=_load_one("locations.yaml", "locations", warnings, sources),
        questions=_load_one("questions.yaml", "questions", warnings, sources),
        warnings=warnings,
        sources=sources,
    )
    problems = config.validate()
    if problems:
        config.warnings.extend(problems)
        # 权重和不等于 1 时按比例归一，保证总分仍落在 [0,1]
        weights = config.matching.setdefault("weights", {})
        normalized = config.normalized_weights()
        if abs(sum(normalized.values()) - WEIGHT_SUM_EXPECTED) <= 1e-9:
            weights.update(normalized)
    _CACHE = config
    return config


def config_path(filename: str) -> str:
    return os.path.join(CONFIG_DIR, filename)


__all__ = [
    "CONFIG_DIR",
    "DIMENSIONS",
    "WEIGHT_SUM_EXPECTED",
    "BUILTIN_FALLBACK",
    "ConfigError",
    "RuntimeConfig",
    "load_config",
    "load_yaml",
    "config_path",
    "_parse_minimal_yaml",
]
