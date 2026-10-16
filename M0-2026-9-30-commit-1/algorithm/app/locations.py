"""校园地点层级表：解析中文地点字符串 + 计算地点合理度。

- 层级表（校区 → 建筑 → 楼层 → 具体地点）与所有分值档位全部来自
  ``config/locations.yaml``，本模块只做「解析 + 查表」，不硬编码任何分值。
- 解析不出来（空串 / 无法识别）一律视为 ``unknown`` → 中性分，绝不抛异常。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .config_loader import RuntimeConfig, load_config
from .text_utils import cn_number, normalize

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class ParsedLocation:
    """地点解析结果。spot 对应接口契约里的 ``spot`` 字段。"""

    raw: str = ""
    campus: Optional[str] = None      # 校区 code
    building: Optional[str] = None    # 建筑 code
    floor: Optional[str] = None       # 楼层 code（字符串，如 "3"）
    spot: Optional[str] = None        # 具体地点 code（如 entrance / reading_room）
    campus_name: Optional[str] = None
    building_name: Optional[str] = None
    floor_name: Optional[str] = None
    spot_name: Optional[str] = None

    @property
    def known(self) -> bool:
        return bool(self.campus or self.building)

    def to_dict(self) -> Dict[str, Any]:
        """输出 ``{campus, building, floor, spot}``（接口契约要求的四个键）。"""
        return {
            "campus": self.campus_name or self.campus,
            "building": self.building_name or self.building,
            "floor": self.floor_name or self.floor,
            "spot": self.spot_name or self.spot,
        }

    def describe(self) -> str:
        parts = [p for p in (self.campus_name, self.building_name, self.floor_name, self.spot_name) if p]
        return "".join(parts) if parts else (self.raw or "地点未知")


@dataclass
class LocationScore:
    score: float
    tier: str
    label: str
    reason: str
    left: ParsedLocation
    right: ParsedLocation
    same_campus: Optional[bool] = None
    same_building: Optional[bool] = None
    distance_hops: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "tier": self.tier,
            "label": self.label,
            "reason": self.reason,
            "same_campus": self.same_campus,
            "same_building": self.same_building,
            "distance_hops": self.distance_hops,
        }


@dataclass
class CampusIndex:
    """由 locations.yaml 构建的检索索引。"""

    config: RuntimeConfig
    campus_names: Dict[str, str] = field(default_factory=dict)
    campus_aliases: List[Tuple[str, str]] = field(default_factory=list)   # (归一化别名, campus code)
    building_names: Dict[Tuple[str, str], str] = field(default_factory=dict)
    building_aliases: List[Tuple[str, str, str]] = field(default_factory=list)  # (别名, campus, building)
    floor_aliases: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = field(default_factory=dict)
    spot_aliases: Dict[Tuple[str, str], List[Tuple[str, str, str]]] = field(default_factory=dict)
    adjacency: Dict[Tuple[str, str], set] = field(default_factory=dict)
    campus_adjacency: Dict[str, set] = field(default_factory=dict)
    keyword_hints: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    floor_patterns: List[re.Pattern] = field(default_factory=list)
    tiers: Dict[str, float] = field(default_factory=dict)
    tier_labels: Dict[str, str] = field(default_factory=dict)

    # -- 查询 -------------------------------------------------------------
    def building_name(self, campus: Optional[str], building: Optional[str]) -> Optional[str]:
        if not building:
            return None
        return self.building_names.get((campus or "", building)) or self.building_names.get(("", building), building)

    def build_text(self, campus: Optional[str], building: Optional[str],
                   floor: Optional[str] = None, spot: Optional[str] = None) -> str:
        """用 code 反向拼出一个标准中文地点串（供 demo 数据生成 / 展示使用）。"""
        campus_name = self.campus_names.get(campus or "", campus or "")
        b_name = self.building_name(campus, building) or (building or "")
        parts: List[str] = []
        if campus_name:
            parts.append(campus_name)
        if b_name:
            parts.append(b_name)
        floor_name = self._alias_name(self.floor_aliases.get((campus or "", building or ""), []), floor)
        spot_name = self._alias_name(self.spot_aliases.get((campus or "", building or ""), []), spot)
        if floor_name:
            parts.append(floor_name)
        if spot_name:
            parts.append(spot_name)
        return "".join(parts)

    @staticmethod
    def _alias_name(entries: List[Tuple[str, str, str]], code: Optional[str]) -> Optional[str]:
        if not code:
            return None
        for _alias, value, display in entries:
            if value == code:
                return display
        return code

    def floors_of(self, campus: Optional[str], building: Optional[str]) -> List[str]:
        codes: List[str] = []
        for _alias, value, _display in self.floor_aliases.get((campus or "", building or ""), []):
            if value not in codes:
                codes.append(value)
        return codes

    def buildings_of(self, campus: Optional[str]) -> List[str]:
        return [b for (c, b) in self.building_names if c == campus]

    def is_adjacent(self, campus: Optional[str], building_a: Optional[str], building_b: Optional[str]) -> bool:
        if not campus or not building_a or not building_b:
            return False
        return building_b in self.adjacency.get((campus, building_a), set())

    def campuses_adjacent(self, campus_a: Optional[str], campus_b: Optional[str]) -> bool:
        if not campus_a or not campus_b:
            return False
        return campus_b in self.campus_adjacency.get(campus_a, set())


# ---------------------------------------------------------------------------
# 索引构建
# ---------------------------------------------------------------------------
def build_index(config: Optional[RuntimeConfig] = None) -> CampusIndex:
    config = config or load_config()
    data = config.locations or {}
    index = CampusIndex(config=config)

    for campus in data.get("campuses") or []:
        if not isinstance(campus, dict):
            continue
        c_code = str(campus.get("code") or "")
        c_name = str(campus.get("name") or c_code)
        index.campus_names[c_code] = c_name
        for alias in [c_name, c_code, *(campus.get("aliases") or [])]:
            key = normalize(alias)
            if key:
                index.campus_aliases.append((key, c_code))
        for other in campus.get("adjacent_campuses") or []:
            index.campus_adjacency.setdefault(c_code, set()).add(str(other))
            index.campus_adjacency.setdefault(str(other), set()).add(c_code)

        for building in campus.get("buildings") or []:
            if not isinstance(building, dict):
                continue
            b_code = str(building.get("code") or "")
            b_name = str(building.get("name") or b_code)
            index.building_names[(c_code, b_code)] = b_name
            for alias in [b_name, b_code, *(building.get("aliases") or [])]:
                key = normalize(alias)
                if key:
                    index.building_aliases.append((key, c_code, b_code))
            for other in building.get("adjacent_buildings") or []:
                index.adjacency.setdefault((c_code, b_code), set()).add(str(other))
                index.adjacency.setdefault((c_code, str(other)), set()).add(b_code)

            floor_entries = index.floor_aliases.setdefault((c_code, b_code), [])
            for floor in building.get("floors") or []:
                if not isinstance(floor, dict):
                    continue
                f_code = str(floor.get("code") or "")
                f_name = str(floor.get("name") or f_code)
                for alias in [f_name, f_code, *(floor.get("aliases") or [])]:
                    key = normalize(alias)
                    if key:
                        floor_entries.append((key, f_code, f_name))

            spot_entries = index.spot_aliases.setdefault((c_code, b_code), [])
            for spot in building.get("spots") or []:
                if not isinstance(spot, dict):
                    continue
                s_code = str(spot.get("code") or "")
                s_name = str(spot.get("name") or s_code)
                for alias in [s_name, s_code, *(spot.get("aliases") or [])]:
                    key = normalize(alias)
                    if key:
                        spot_entries.append((key, s_code, s_name))

    for pattern in ((data.get("output") or {}).get("parsing") or {}).get("floor_patterns") or []:
        try:
            index.floor_patterns.append(re.compile(str(pattern)))
        except re.error:
            continue
    index.tiers = config.location_tiers
    index.tier_labels = ((data.get("output") or {}).get("tier_labels")) or {}
    for key, value in (data.get("keyword_hints") or {}).items():
        if isinstance(value, dict):
            index.keyword_hints[normalize(key)] = dict(value)
    return index


@lru_cache(maxsize=1)
def _cached_index() -> CampusIndex:
    return build_index()


def get_index(reload: bool = False) -> CampusIndex:
    if reload:
        _cached_index.cache_clear()
    return _cached_index()


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _longest_alias_match(text: str, aliases: List[Tuple[str, str]]) -> Optional[str]:
    best_key, best_value = "", None
    for alias, value in aliases:
        if alias and alias in text and len(alias) > len(best_key):
            best_key, best_value = alias, value
    return best_value


def _longest_building_match(
    text: str,
    aliases: List[Tuple[str, str, str]],
    campus: Optional[str],
) -> Optional[Tuple[str, str]]:
    best_key, best_value = "", None
    for alias, c_code, b_code in aliases:
        if campus and c_code != campus:
            continue
        if alias and alias in text and len(alias) > len(best_key):
            best_key, best_value = alias, (c_code, b_code)
    return best_value


def _match_floor(text: str, index: CampusIndex, campus: str, building: str, raw: str) -> Optional[str]:
    entries = index.floor_aliases.get((campus, building), [])
    for alias, code, _display in sorted(entries, key=lambda e: -len(e[0])):
        if alias and alias in text:
            return code
    for pattern in index.floor_patterns:
        for match in pattern.finditer(raw):
            number = cn_number(match.group(1))
            if number is None:
                continue
            code = str(number)
            if any(code == value for _alias, value, _display in entries):
                return code
            # 楼层表里没有该层（例如「实验楼六楼」）也返回数字，便于「同层/不同层」判定
            return code
    return None


def _match_spot(text: str, index: CampusIndex, campus: str, building: str) -> Optional[str]:
    entries = index.spot_aliases.get((campus, building), [])
    return _longest_alias_match(text, [(alias, code) for alias, code, _display in sorted(entries, key=lambda e: -len(e[0]))])


def parse_location(value: Any, index: Optional[CampusIndex] = None) -> ParsedLocation:
    """把中文地点字符串解析成 ``{campus, building, floor, spot}``；失败 → 各字段为 None。"""
    index = index or get_index()
    raw = "" if value is None else str(value).strip()
    parsed = ParsedLocation(raw=raw)
    if not raw:
        return parsed

    text = normalize(raw)
    if not text:
        return parsed

    campus = _longest_alias_match(text, index.campus_aliases)
    building_pair = _longest_building_match(text, index.building_aliases, campus)
    if campus is None and building_pair is not None:
        campus = building_pair[0]
    if building_pair is None and campus is None:
        # 兜底：地点关键词提示表（config/locations.yaml → keyword_hints）
        hint, best_len = None, 0
        for key, value_map in index.keyword_hints.items():
            if key and key in text and len(key) > best_len:
                hint, best_len = value_map, len(key)
        if hint:
            campus = hint.get("campus") or None
            if hint.get("building"):
                building_pair = (campus or "", str(hint["building"]))
                campus = campus or building_pair[0]

    if campus:
        parsed.campus = campus
        parsed.campus_name = index.campus_names.get(campus, campus)

    building = None
    if building_pair:
        building = building_pair[1]
        if not campus:
            campus = building_pair[0]
            parsed.campus = campus
            parsed.campus_name = index.campus_names.get(campus, campus)
        parsed.building = building
        parsed.building_name = index.building_name(campus, building)

    if campus and building:
        floor = _match_floor(text, index, campus, building, raw)
        if floor:
            parsed.floor = floor
            parsed.floor_name = index._alias_name(index.floor_aliases.get((campus, building), []), floor)
        spot = _match_spot(text, index, campus, building)
        if spot:
            parsed.spot = spot
            parsed.spot_name = index._alias_name(index.spot_aliases.get((campus, building), []), spot)
    return parsed


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------
def _tier(index: CampusIndex, name: str) -> float:
    return float(index.tiers.get(name, 0.5))


def _label(index: CampusIndex, name: str) -> str:
    return str(index.tier_labels.get(name, name))


def _hop_distance(index: CampusIndex, campus: str, start: str, goal: str) -> Optional[int]:
    """建筑邻接图的 BFS 跳数（1 = 直接相邻）。"""
    if start == goal:
        return 0
    seen = {start}
    frontier = [start]
    hops = 0
    while frontier and hops < 6:
        hops += 1
        nxt: List[str] = []
        for node in frontier:
            for neighbour in index.adjacency.get((campus, node), set()):
                if neighbour == goal:
                    return hops
                if neighbour not in seen:
                    seen.add(neighbour)
                    nxt.append(neighbour)
        frontier = nxt
    return None


def score_locations(
    left: Any,
    right: Any,
    index: Optional[CampusIndex] = None,
    *,
    left_parsed: Optional[ParsedLocation] = None,
    right_parsed: Optional[ParsedLocation] = None,
) -> LocationScore:
    """地点合理度：同地点 1.00 / 同建筑不同层 0.85 / 相邻建筑 0.60 / 同校区较远 0.35 / 跨校区 0.10 / 未知 0.50。"""
    index = index or get_index()
    a = left_parsed or parse_location(left, index)
    b = right_parsed or parse_location(right, index)

    if not a.known or not b.known or not a.campus or not b.campus:
        return LocationScore(
            score=_tier(index, "unknown"),
            tier="unknown",
            label=_label(index, "unknown"),
            reason="地点信息缺失或无法解析，按中性分处理",
            left=a,
            right=b,
        )

    same_campus = a.campus == b.campus
    if not same_campus:
        score = _tier(index, "cross_campus")
        return LocationScore(
            score=score,
            tier="cross_campus",
            label=_label(index, "cross_campus"),
            reason=f"分属{a.campus_name}与{b.campus_name}，跨校区距离远",
            left=a,
            right=b,
            same_campus=False,
        )

    same_building = bool(a.building) and a.building == b.building
    if same_building:
        if a.floor and b.floor:
            if a.floor == b.floor:
                if a.spot and b.spot and a.spot == b.spot:
                    return LocationScore(
                        score=_tier(index, "same_spot"),
                        tier="same_spot",
                        label=_label(index, "same_spot"),
                        reason=f"同为{a.describe()}",
                        left=a, right=b, same_campus=True, same_building=True,
                    )
                return LocationScore(
                    score=_tier(index, "same_floor"),
                    tier="same_floor",
                    label=_label(index, "same_floor"),
                    reason=f"同在{a.building_name}{a.floor_name}，具体位置接近",
                    left=a, right=b, same_campus=True, same_building=True,
                )
            return LocationScore(
                score=_tier(index, "same_building_diff_floor"),
                tier="same_building_diff_floor",
                label=_label(index, "same_building_diff_floor"),
                reason=f"同在{a.building_name}，但一个是{a.floor_name}、一个是{b.floor_name}",
                left=a, right=b, same_campus=True, same_building=True,
            )
        return LocationScore(
            score=_tier(index, "same_floor"),
            tier="same_floor",
            label=_label(index, "same_floor"),
            reason=f"同在{a.building_name}内，楼层信息不完整",
            left=a, right=b, same_campus=True, same_building=True,
        )

    if a.building and b.building and index.is_adjacent(a.campus, a.building, b.building):
        return LocationScore(
            score=_tier(index, "adjacent_building"),
            tier="adjacent_building",
            label=_label(index, "adjacent_building"),
            reason=f"{a.building_name}与{b.building_name}相邻，步行可达",
            left=a, right=b, same_campus=True, same_building=False, distance_hops=1,
        )

    hops = None
    if a.building and b.building:
        hops = _hop_distance(index, a.campus, a.building, b.building)
    return LocationScore(
        score=_tier(index, "same_campus_far"),
        tier="same_campus_far",
        label=_label(index, "same_campus_far"),
        reason=f"同属{a.campus_name}但相隔较远（{a.building_name} ↔ {b.building_name}）",
        left=a, right=b, same_campus=True, same_building=False, distance_hops=hops,
    )


def parse_location_dict(value: Any, index: Optional[CampusIndex] = None) -> Dict[str, Any]:
    """便捷函数：直接返回 ``{campus, building, floor, spot}``（无法解析时全为 None）。"""
    return parse_location(value, index).to_dict()


__all__ = [
    "ParsedLocation",
    "LocationScore",
    "CampusIndex",
    "build_index",
    "get_index",
    "parse_location",
    "parse_location_dict",
    "score_locations",
]
