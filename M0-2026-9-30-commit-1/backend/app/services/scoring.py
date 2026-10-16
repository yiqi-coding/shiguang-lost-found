"""评分模型（需求 §4.4.2 / 数据库设计 §3.3）。

**权重与规则的唯一来源是配置文件 `config/matching.yaml`（算法服务侧）与本模块的兜底默认值**
（来自 `settings.MATCH_WEIGHT_*`）。**严禁**把权重散落在多个文件里。

总分公式（冻结）：
    总分 = 0.20×类别分 + 0.25×属性/文本分 + 0.20×图像相似度
         + 0.15×时间合理度 + 0.15×地点合理度 + 0.05×品牌/型号分
所有维度分与总分归一化到 [0, 1]。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.core.enums import MatchLevel

# 匹配等级阈值（可由配置覆盖）
MATCH_LEVEL_HIGH = 0.75
MATCH_LEVEL_MEDIUM = 0.50

# 时间分中性值（时间缺失时使用，需求 §4.4.3）
TIME_NEUTRAL_SCORE = 0.50
LOCATION_UNKNOWN_SCORE = 0.50

# 时间分分段（小时 → 分数），与算法服务 config/matching.yaml 保持一致
TIME_BANDS: list[tuple[float, float]] = [
    (2.0, 1.00),
    (12.0, 0.80),
    (24.0, 0.60),
    (72.0, 0.30),
    (math.inf, 0.10),
]

# 地点分表（需求 §4.4.4）
LOCATION_SCORES = {
    "same_spot": 1.00,
    "same_building_diff_floor": 0.85,
    "adjacent_building": 0.60,
    "same_campus_far": 0.35,
    "different_campus": 0.10,
    "unknown": LOCATION_UNKNOWN_SCORE,
}

# 品牌/型号分
BRAND_BOTH_SAME = 1.00
BRAND_BOTH_DIFFERENT = 0.10
BRAND_ONE_SIDE_ONLY = 0.50
BRAND_BOTH_MISSING = 0.50

SCORE_MIN = 0.0
SCORE_MAX = 1.0


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------


def clamp(value: float, low: float = SCORE_MIN, high: float = SCORE_MAX) -> float:
    """归一化到 [low, high]，并屏蔽 NaN。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return low
    return max(low, min(high, float(value)))


def get_weights() -> dict[str, float]:
    """获取六维权重。总和必须为 1.0（有单元测试断言）。"""
    return dict(settings.match_weights)


def weighted_total(scores: dict[str, float], weights: dict[str, float] | None = None) -> float:
    """按权重计算总分。

    :param scores: 键为 category/attribute/image/time/location/brand_model
    """
    weights = weights or get_weights()
    total = (
        weights["category"] * clamp(scores.get("category_score", 0.0))
        + weights["attribute"] * clamp(scores.get("attribute_score", 0.0))
        + weights["image"] * clamp(scores.get("image_score", 0.0))
        + weights["time"] * clamp(scores.get("time_score", 0.0))
        + weights["location"] * clamp(scores.get("location_score", 0.0))
        + weights["brand_model"] * clamp(scores.get("brand_model_score", 0.0))
    )
    return clamp(round(total, 5))


# ----------------------------------------------------------------------
# 单维度评分
# ----------------------------------------------------------------------


def category_score(cat_a: str | None, cat_b: str | None) -> float:
    """类别分：完全一致 1.0；一方为「其他」但没有更具体信息时给中性分；否则 0。"""
    if not cat_a or not cat_b:
        return LOCATION_UNKNOWN_SCORE
    if cat_a == cat_b:
        return 1.0
    # 「其他」与其他具体类别之间给一个较低但非零的相似度
    if "其他" in (cat_a, cat_b):
        return 0.30
    return 0.0


def time_score(
    time_a: datetime | None,
    time_b: datetime | None,
    *,
    bands: list[tuple[float, float]] | None = None,
) -> tuple[float, str]:
    """时间合理度（需求 §4.4.3）。

    规则：
    - 时间缺失 → 取可配置中性分，并标记 unknown（**绝不抛异常**）；
    - 拾取时间应晚于丢失时间：若拾取早于丢失，按绝对差计算但打上顺序异常提示；
    - 差值越小分越高；超过 72 小时降至 0.10。

    :return: (分数, 说明文本)——说明文本用于 `evidence` / `uncertainties`
    """
    bands = bands or TIME_BANDS

    if time_a is None or time_b is None:
        return TIME_NEUTRAL_SCORE, "时间信息缺失，按中性分处理"

    delta_hours = abs((time_b - time_a).total_seconds()) / 3600.0

    score = bands[-1][1]
    for threshold, value in bands:
        if delta_hours <= threshold:
            score = value
            break

    # 差值落在区间内时做线性插值，避免分数跳变
    score = _interpolate(delta_hours, bands, score)

    if delta_hours < 1:
        note = f"时间相差约 {int(delta_hours * 60)} 分钟，非常接近"
    elif delta_hours < 24:
        hours = int(delta_hours)
        minutes = int((delta_hours - hours) * 60)
        note = f"时间相差约 {hours} 小时 {minutes} 分钟"
    else:
        note = f"时间相差约 {delta_hours / 24:.1f} 天"

    if time_b < time_a:
        note += "（拾取时间早于丢失时间，存在顺序异常）"

    return clamp(score), note


def _interpolate(delta_hours: float, bands: list[tuple[float, float]], fallback: float) -> float:
    """在相邻分段之间线性插值，使时间分连续变化。"""
    prev_threshold = 0.0
    prev_value = 1.0
    for threshold, value in bands:
        if delta_hours <= threshold:
            if threshold == prev_threshold or math.isinf(threshold):
                return value
            ratio = (delta_hours - prev_threshold) / (threshold - prev_threshold)
            return prev_value + (value - prev_value) * ratio
        prev_threshold, prev_value = threshold, value
    return fallback


def location_score(parsed_a: dict[str, Any] | None, parsed_b: dict[str, Any] | None) -> tuple[float, str]:
    """地点合理度（需求 §4.4.4）。

    依赖 `location_parsed`（由算法服务的地点解析函数写入），
    未解析或信息不足时返回未知分 0.5。

    :return: (分数, 说明文本)
    """
    if not parsed_a or not parsed_b:
        return LOCATION_UNKNOWN_SCORE, "地点信息不完整，按中性分处理"

    campus_a, campus_b = parsed_a.get("campus"), parsed_b.get("campus")
    building_a, building_b = parsed_a.get("building"), parsed_b.get("building")
    floor_a, floor_b = parsed_a.get("floor"), parsed_b.get("floor")
    spot_a, spot_b = parsed_a.get("spot"), parsed_b.get("spot")

    if not campus_a or not campus_b:
        return LOCATION_UNKNOWN_SCORE, "地点未能识别到校区，按中性分处理"

    if campus_a != campus_b:
        return LOCATION_SCORES["different_campus"], "位于不同校区，地点关联度低"

    if building_a and building_b and building_a == building_b:
        if floor_a and floor_b and floor_a == floor_b:
            if spot_a and spot_b and spot_a == spot_b:
                return LOCATION_SCORES["same_spot"], f"同一具体地点（{building_a}{floor_a}{spot_a or ''}）"
            return LOCATION_SCORES["same_spot"], f"同一建筑同一楼层（{building_a}{floor_a}）"
        return LOCATION_SCORES["same_building_diff_floor"], f"同一建筑不同楼层（{building_a}）"

    # 相邻建筑判断：由算法服务在解析结果中给出 adjacent_buildings 列表
    adjacent = set(parsed_a.get("adjacent_buildings") or []) | set(parsed_b.get("adjacent_buildings") or [])
    if building_a and building_b and (building_b in adjacent or building_a in adjacent):
        return LOCATION_SCORES["adjacent_building"], f"相邻建筑（{building_a} ↔ {building_b}）"

    return LOCATION_SCORES["same_campus_far"], "同一校区但相距较远"


def brand_model_score(brand_a: str | None, brand_b: str | None) -> tuple[float, str]:
    """品牌/型号分。"""
    a, b = (brand_a or "").strip().lower(), (brand_b or "").strip().lower()
    if not a and not b:
        return BRAND_BOTH_MISSING, "双方均未填写品牌型号"
    if not a or not b:
        return BRAND_ONE_SIDE_ONLY, "仅一方填写品牌型号，无法确认"
    if a == b:
        return BRAND_BOTH_SAME, f"品牌型号一致（{brand_a}）"
    # 包含关系视为部分匹配（如 "小米" vs "小米 Air2"）
    if a in b or b in a:
        return 0.75, f"品牌型号部分匹配（{brand_a} / {brand_b}）"
    return BRAND_BOTH_DIFFERENT, f"品牌型号不同（{brand_a} / {brand_b}）"


def match_level_for(total: float) -> str:
    """总分 → 匹配等级。低分**不得**展示为「高匹配」。"""
    if total >= MATCH_LEVEL_HIGH:
        return MatchLevel.HIGH
    if total >= MATCH_LEVEL_MEDIUM:
        return MatchLevel.MEDIUM
    return MatchLevel.LOW


def is_above_display_threshold(total: float, threshold: float | None = None) -> bool:
    threshold = settings.MATCH_MIN_DISPLAY_SCORE if threshold is None else threshold
    return total >= threshold


# ----------------------------------------------------------------------
# 风险提示
# ----------------------------------------------------------------------

HIGH_RISK_CATEGORIES = {"校园卡", "无线耳机"}


def build_risk_tips(
    *,
    category: str | None,
    total: float,
    evidence: list[str],
    uncertainties: list[str],
) -> list[str]:
    """生成风险提示（供审核台与匹配页展示）。"""
    tips: list[str] = []
    if category in HIGH_RISK_CATEGORIES:
        tips.append(f"「{category}」属于高仿冒风险类别，建议核对隐藏特征")
    if total >= MATCH_LEVEL_HIGH and uncertainties:
        tips.append("高匹配但存在未确认项，建议由管理员重点核验")
    if not evidence:
        tips.append("未找到明确的正向匹配依据")
    return tips
