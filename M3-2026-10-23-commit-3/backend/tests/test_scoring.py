"""评分模型测试（契约 §3.6 / 需求 §4.4）。

覆盖：

- 六维权重和为 1.0；
- `weighted_total` 等于手算加权和（容差 1e-6），3 组不同分数；
- 时间分：缺失取中性分 0.5 且不抛异常、单调递减、>72h 得 0.10、拾取早于丢失不崩溃；
- 地点分：同地点 1.0 / 同建筑不同楼层 0.85 / 不同校区 0.10 / 未知 0.5；
- `match_level_for` 阈值边界。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.enums import MatchLevel
from app.services import scoring
from app.services.scoring import (
    LOCATION_SCORES,
    LOCATION_UNKNOWN_SCORE,
    TIME_NEUTRAL_SCORE,
    brand_model_score,
    category_score,
    clamp,
    get_weights,
    is_above_display_threshold,
    location_score,
    match_level_for,
    time_score,
    weighted_total,
)

BASE_TIME = datetime(2026, 9, 28, 15, 0, 0)


class TestWeights(unittest.TestCase):
    """权重契约（0.20 / 0.25 / 0.20 / 0.15 / 0.15 / 0.05）。"""

    def test_weights_sum_to_one(self) -> None:
        weights = get_weights()
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=9)

    def test_weights_are_frozen_values(self) -> None:
        weights = get_weights()
        self.assertAlmostEqual(weights["category"], 0.20)
        self.assertAlmostEqual(weights["attribute"], 0.25)
        self.assertAlmostEqual(weights["image"], 0.20)
        self.assertAlmostEqual(weights["time"], 0.15)
        self.assertAlmostEqual(weights["location"], 0.15)
        self.assertAlmostEqual(weights["brand_model"], 0.05)

    def test_weighted_total_matches_manual_calculation(self) -> None:
        """三组分数：总分必须等于手算加权和（容差 1e-6）。"""
        weights = get_weights()
        cases = [
            {
                "category_score": 1.0,
                "attribute_score": 0.92,
                "image_score": 0.81,
                "time_score": 0.95,
                "location_score": 1.0,
                "brand_model_score": 0.50,
            },
            {
                "category_score": 0.0,
                "attribute_score": 0.0,
                "image_score": 0.0,
                "time_score": 0.0,
                "location_score": 0.0,
                "brand_model_score": 0.0,
            },
            {
                "category_score": 0.30,
                "attribute_score": 0.62,
                "image_score": 0.18,
                "time_score": 0.10,
                "location_score": 0.35,
                "brand_model_score": 0.75,
            },
        ]
        for index, scores in enumerate(cases):
            expected = (
                weights["category"] * scores["category_score"]
                + weights["attribute"] * scores["attribute_score"]
                + weights["image"] * scores["image_score"]
                + weights["time"] * scores["time_score"]
                + weights["location"] * scores["location_score"]
                + weights["brand_model"] * scores["brand_model_score"]
            )
            with self.subTest(case=index):
                self.assertAlmostEqual(weighted_total(scores), expected, delta=1e-6)

    def test_weighted_total_clamps_out_of_range_input(self) -> None:
        """越界输入被夹取到 [0,1]，不会溢出总分。"""
        score = weighted_total({"category_score": 5.0, "attribute_score": -3.0})
        self.assertLessEqual(score, 1.0)
        self.assertGreaterEqual(score, 0.0)

    def test_clamp_handles_nan_and_none(self) -> None:
        self.assertEqual(clamp(None), 0.0)
        self.assertEqual(clamp(float("nan")), 0.0)
        self.assertEqual(clamp(2.0), 1.0)
        self.assertEqual(clamp(-1.0), 0.0)


class TestTimeScore(unittest.TestCase):
    """时间合理度（需求 §4.4.3）。"""

    def test_missing_time_returns_neutral_without_exception(self) -> None:
        self.assertEqual(time_score(None, BASE_TIME)[0], TIME_NEUTRAL_SCORE)
        self.assertEqual(time_score(BASE_TIME, None)[0], TIME_NEUTRAL_SCORE)
        self.assertEqual(time_score(None, None)[0], TIME_NEUTRAL_SCORE)
        self.assertIn("缺失", time_score(None, BASE_TIME)[1])

    def test_monotonic_decreasing(self) -> None:
        """时间差越大，分数不增（多组采样点验证单调性）。"""
        deltas = [0.5, 1.0, 2.0, 6.0, 12.0, 20.0, 24.0, 48.0, 72.0, 100.0, 300.0]
        values = [time_score(BASE_TIME, BASE_TIME + timedelta(hours=delta))[0] for delta in deltas]
        for previous, current in zip(values, values[1:]):
            self.assertLessEqual(current, previous + 1e-9)
        # 严格单调至少体现在端点上
        self.assertGreater(values[0], values[-1])

    def test_beyond_72_hours_scores_lowest_band(self) -> None:
        score, note = time_score(BASE_TIME, BASE_TIME + timedelta(hours=73))
        self.assertAlmostEqual(score, 0.10, delta=1e-6)
        self.assertIn("天", note)

    def test_within_two_hours_scores_one(self) -> None:
        self.assertAlmostEqual(time_score(BASE_TIME, BASE_TIME + timedelta(minutes=30))[0], 1.0, delta=1e-6)

    def test_pickup_before_loss_does_not_crash(self) -> None:
        """拾取早于丢失：按绝对差计分并给出顺序异常提示。"""
        score, note = time_score(BASE_TIME, BASE_TIME - timedelta(hours=3))
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertIn("顺序异常", note)

    def test_note_text_is_chinese_and_non_empty(self) -> None:
        for delta in (0.2, 5, 30, 100):
            _score, note = time_score(BASE_TIME, BASE_TIME + timedelta(hours=delta))
            self.assertTrue(note)
            self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in note))


class TestLocationScore(unittest.TestCase):
    """地点合理度（需求 §4.4.4）。"""

    def test_same_spot(self) -> None:
        parsed = {"campus": "main", "building": "图书馆", "floor": "3", "spot": "自习区"}
        score, note = location_score(parsed, dict(parsed))
        self.assertAlmostEqual(score, 1.0, delta=1e-6)
        self.assertIn("同一", note)

    def test_same_building_different_floor(self) -> None:
        a = {"campus": "main", "building": "图书馆", "floor": "3", "spot": "自习区"}
        b = {"campus": "main", "building": "图书馆", "floor": "1", "spot": "大厅"}
        score, note = location_score(a, b)
        self.assertAlmostEqual(score, LOCATION_SCORES["same_building_diff_floor"], delta=1e-6)
        self.assertAlmostEqual(score, 0.85, delta=1e-6)
        self.assertIn("不同楼层", note)

    def test_different_campus(self) -> None:
        a = {"campus": "main", "building": "图书馆", "floor": "3"}
        b = {"campus": "east", "building": "图书馆", "floor": "3"}
        score, _note = location_score(a, b)
        self.assertAlmostEqual(score, 0.10, delta=1e-6)

    def test_unknown_location(self) -> None:
        self.assertEqual(location_score(None, {"campus": "main"})[0], LOCATION_UNKNOWN_SCORE)
        self.assertEqual(location_score({"campus": "main"}, {})[0], LOCATION_UNKNOWN_SCORE)
        # 未识别到校区同样按未知处理
        self.assertEqual(location_score({"building": "图书馆"}, {"building": "图书馆"})[0], LOCATION_UNKNOWN_SCORE)
        self.assertAlmostEqual(LOCATION_UNKNOWN_SCORE, 0.5, delta=1e-6)

    def test_adjacent_building(self) -> None:
        a = {"campus": "main", "building": "图书馆", "adjacent_buildings": ["二教"]}
        b = {"campus": "main", "building": "二教"}
        self.assertAlmostEqual(location_score(a, b)[0], LOCATION_SCORES["adjacent_building"], delta=1e-6)


class TestCategoryAndBrand(unittest.TestCase):
    """类别分与品牌/型号分。"""

    def test_category_score(self) -> None:
        self.assertAlmostEqual(category_score("无线耳机", "无线耳机"), 1.0, delta=1e-6)
        self.assertAlmostEqual(category_score("无线耳机", "其他"), 0.30, delta=1e-6)
        self.assertAlmostEqual(category_score("无线耳机", "书籍"), 0.0, delta=1e-6)
        self.assertAlmostEqual(category_score(None, "书籍"), 0.5, delta=1e-6)

    def test_brand_model_score(self) -> None:
        self.assertAlmostEqual(brand_model_score("小米", "小米")[0], 1.0, delta=1e-6)
        self.assertAlmostEqual(brand_model_score(None, None)[0], 0.5, delta=1e-6)
        self.assertAlmostEqual(brand_model_score("小米", None)[0], 0.5, delta=1e-6)
        self.assertAlmostEqual(brand_model_score("小米", "华为")[0], 0.10, delta=1e-6)
        self.assertAlmostEqual(brand_model_score("小米", "小米 Air2")[0], 0.75, delta=1e-6)
        # 大小写与空格不影响判定
        self.assertAlmostEqual(brand_model_score("Apple", " apple ")[0], 1.0, delta=1e-6)


class TestMatchLevel(unittest.TestCase):
    """匹配等级阈值边界（契约 §3.6：≥0.75 high / ≥0.5 medium / <0.5 low）。"""

    def test_boundaries(self) -> None:
        cases = [
            (1.0, MatchLevel.HIGH),
            (0.75, MatchLevel.HIGH),  # 边界：含
            (0.749999, MatchLevel.MEDIUM),
            (0.50, MatchLevel.MEDIUM),  # 边界：含
            (0.499999, MatchLevel.LOW),
            (0.0, MatchLevel.LOW),
        ]
        for total, expected in cases:
            with self.subTest(total=total):
                self.assertEqual(match_level_for(total), expected)

    def test_low_is_never_reported_as_high(self) -> None:
        self.assertNotEqual(match_level_for(0.3), MatchLevel.HIGH)

    def test_display_threshold(self) -> None:
        self.assertTrue(is_above_display_threshold(0.35, 0.35))
        self.assertFalse(is_above_display_threshold(0.3499, 0.35))


class TestRiskTips(unittest.TestCase):
    """风险提示（供审核台展示）。"""

    def test_high_risk_category_tip(self) -> None:
        tips = scoring.build_risk_tips(
            category="无线耳机", total=0.9, evidence=["类别一致"], uncertainties=["品牌未确认"]
        )
        self.assertTrue(any("高仿冒风险" in tip for tip in tips))

    def test_no_evidence_tip(self) -> None:
        tips = scoring.build_risk_tips(category="书籍", total=0.2, evidence=[], uncertainties=[])
        self.assertTrue(any("未找到明确的正向匹配依据" in tip for tip in tips))


if __name__ == "__main__":
    unittest.main(verbosity=2)
