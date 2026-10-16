"""``app.matcher`` / ``app.locations`` / ``app.extractor`` 的单元测试（纯标准库）。

覆盖点（对应验收要求）：
* 权重配置加载正确、总分 == 各维度加权和（手工算好的样例，容差 1e-6）
* 时间分：缺失时间不崩溃且取中性分；时间差越大分越低（单调性）
* 地点分：同地点 1.0、不同校区 0.1、未知 0.5
* 图像 Mock embedder 对同一输入可重复（幂等）
* 属性提取：空输入降级且不抛异常
"""

from __future__ import annotations

import os
import sys
import unittest

# --- 路径兜底：保证 `import app.*` 可用（推荐用 `-t .`，见 README） -----------
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TESTS_DIR)
for _candidate in (_ROOT, _TESTS_DIR):
    if _candidate not in sys.path:
        sys.path.insert(0, _candidate)
try:  # 正常路径：tests 包可导入
    from tests import _bootstrap  # noqa: F401
except ImportError:  # `discover -s tests` 不带 -t 时测试模块是顶层模块
    import _bootstrap  # type: ignore[no-redef]  # noqa: F401
# ---------------------------------------------------------------------------

from app import config_loader
from app.extractor import ATTRIBUTE_SCHEMA, AttributeExtractor, validate_attributes
from app.locations import get_index, parse_location, score_locations
from app.matcher import (
    DIMENSIONS,
    ItemView,
    MatchEngine,
    MockImageEmbedder,
    cosine_similarity,
    get_image_embedder,
    parse_time_window,
    register_image_embedder,
    score_time,
    time_score_from_diff,
)

# 契约里声明的总分权重（唯一真值来源仍是 config/matching.yaml，这里只做核对）
DOCUMENTED_WEIGHTS = {
    "category": 0.20,
    "text": 0.25,
    "image": 0.20,
    "time": 0.15,
    "location": 0.15,
    "brand_model": 0.05,
}

ITEM_LOST = {
    "id": 1001,
    "image_url": "https://cdn.example-campus.invalid/demo/earbuds/lost-1001-v0.jpg",
    "category": "无线耳机",
    "title": "无线耳机",
    "description": "黑色AppleAirPods Pro 2无线耳机，椭圆形充电盒，盒体右侧有划痕",
    "attributes": {"color": "黑色", "brand": "Apple", "model": "AirPods Pro 2",
                   "features": ["椭圆形充电盒", "盒体右侧有划痕"]},
    "location": "东湖校区图书馆三楼阅览室",
    "event_time": "2026-09-28T15:00:00",
}
ITEM_FOUND = {
    "id": 2001,
    "image_url": "https://cdn.example-campus.invalid/demo/earbuds/found-2001-v0.jpg",
    "category": "无线耳机",
    "title": "无线耳机",
    "description": "黑色AppleAirPods Pro 2无线耳机，椭圆形充电盒，盒体右侧有划痕",
    "attributes": {"color": "黑色", "brand": "Apple", "model": "AirPods Pro 2",
                   "features": ["椭圆形充电盒", "盒体右侧有划痕"]},
    "location": "东湖校区图书馆三楼阅览室",
    "event_time": "2026-09-28T15:20:00",
}


def _with(**overrides):
    """基于 ITEM_FOUND 造一个候选物品（深拷贝一层，避免用例间互相污染）。

    颜色 / 材质 / 品牌 / 型号属于属性，统一写进 ``attributes``（与接口契约一致）。
    """
    item = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in ITEM_FOUND.items()}
    item["attributes"] = dict(item.get("attributes") or {})
    for key, value in overrides.items():
        if key in ("color", "brand", "model", "material"):
            item["attributes"][key] = value
        elif key == "attributes":
            item["attributes"].update(value)
        else:
            item[key] = value
    return item


class TestConfigAndWeights(unittest.TestCase):
    """权重必须来自配置且与文档一致。"""

    @classmethod
    def setUpClass(cls):
        cls.config = config_loader.load_config(reload=True)

    def test_config_file_is_the_source(self):
        for name in ("matching", "locations", "questions"):
            self.assertTrue(self.config.sources.get(name, "").endswith(".yaml"),
                            f"{name} 未从 YAML 加载：{self.config.sources.get(name)}")
        self.assertEqual(self.config.warnings, [], f"配置加载出现告警：{self.config.warnings}")

    def test_weights_match_documented_formula(self):
        weights = self.config.weights
        self.assertEqual(set(weights), set(DOCUMENTED_WEIGHTS))
        for dimension, expected in DOCUMENTED_WEIGHTS.items():
            self.assertAlmostEqual(weights[dimension], expected, places=6,
                                   msg=f"{dimension} 权重与文档不一致")

    def test_weights_sum_to_one(self):
        total = sum(self.config.weights.values())
        self.assertAlmostEqual(total, 1.0, delta=1e-9)

    def test_neutral_scores_are_configured(self):
        neutral = self.config.neutral_scores
        self.assertEqual(set(neutral), set(DIMENSIONS))
        self.assertAlmostEqual(neutral["time"], 0.5, places=6)

    def test_normalized_weights_are_noop_when_correct(self):
        self.assertEqual(self.config.normalized_weights(), self.config.weights)


class TestTotalScoreFormula(unittest.TestCase):
    """总分 == 各维度加权和（容差 1e-6）。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = MatchEngine()
        cls.weights = cls.engine.weights

    def _outcome_and_manual_sum(self, lost, found):
        outcome = self.engine.match_pair(lost, found)
        manual = sum(outcome.scores[name] * self.weights[name] for name in DIMENSIONS)
        return outcome, manual

    def test_same_item_total_equals_weighted_sum(self):
        outcome, manual = self._outcome_and_manual_sum(ITEM_LOST, ITEM_FOUND)
        self.assertAlmostEqual(outcome.total_score, manual, delta=1e-6)
        self.assertAlmostEqual(outcome.applied_adjustments, 0.0, delta=1e-12)

    def test_hand_computed_weighted_sum(self):
        """用完全确定的手工权重组合验证加权公式。"""
        scores = {"category": 1.0, "text": 0.85, "image": 0.62,
                  "time": 0.95, "location": 0.85, "brand_model": 0.5}
        expected = (0.20 * 1.0 + 0.25 * 0.85 + 0.20 * 0.62
                    + 0.15 * 0.95 + 0.15 * 0.85 + 0.05 * 0.5)
        # 手工展开：0.2000 + 0.2125 + 0.1240 + 0.1425 + 0.1275 + 0.0250
        self.assertAlmostEqual(expected, 0.8315, delta=1e-9)
        engine_sum = sum(scores[name] * self.weights[name] for name in DIMENSIONS)
        self.assertAlmostEqual(engine_sum, expected, delta=1e-6)

    def test_every_dimension_score_in_unit_interval(self):
        for found in (ITEM_FOUND, _with(category="书籍"), _with(color="白色"),
                      _with(location="西山校区实验楼三楼实验室"),
                      _with(event_time="2026-10-20T09:00:00")):
            outcome = self.engine.match_pair(ITEM_LOST, found)
            self.assertGreaterEqual(outcome.total_score, 0.0)
            self.assertLessEqual(outcome.total_score, 1.0)
            for name in DIMENSIONS:
                self.assertGreaterEqual(outcome.scores[name], 0.0, name)
                self.assertLessEqual(outcome.scores[name], 1.0, name)

    def test_perfect_match_scores_higher_than_conflicting_one(self):
        good = self.engine.match_pair(ITEM_LOST, ITEM_FOUND)
        bad = self.engine.match_pair(ITEM_LOST, _with(category="书籍", color="红色",
                                                      location="西山校区实验楼三楼实验室",
                                                      event_time="2026-11-30T09:00:00",
                                                      attributes={"brand": "漫步者",
                                                                  "model": "LolliPods"}))
        self.assertGreater(good.total_score, bad.total_score)

    def test_scores_dict_has_all_dimensions(self):
        outcome = self.engine.match_pair(ITEM_LOST, ITEM_FOUND)
        self.assertEqual(set(outcome.scores), set(DIMENSIONS))


class TestTimeScore(unittest.TestCase):
    """时间合理度：分段函数、单调性、缺失兜底。"""

    @classmethod
    def setUpClass(cls):
        cls.config = config_loader.load_config()

    def test_within_two_hours_is_full_score(self):
        self.assertAlmostEqual(time_score_from_diff(0.0, self.config).score, 1.0, delta=1e-6)
        self.assertAlmostEqual(time_score_from_diff(2.0, self.config).score, 1.0, delta=1e-6)

    def test_two_hours_linear_down_to_08(self):
        # 12 小时处应恰好为 0.8（线性段终点）
        self.assertAlmostEqual(time_score_from_diff(12.0, self.config).score, 0.8, delta=1e-6)
        middle = time_score_from_diff(7.0, self.config).score
        self.assertGreater(middle, 0.8)
        self.assertLess(middle, 1.0)

    def test_step_segments(self):
        self.assertAlmostEqual(time_score_from_diff(18.0, self.config).score, 0.6, delta=1e-6)
        self.assertAlmostEqual(time_score_from_diff(24.0, self.config).score, 0.6, delta=1e-6)
        self.assertAlmostEqual(time_score_from_diff(48.0, self.config).score, 0.3, delta=1e-6)
        self.assertAlmostEqual(time_score_from_diff(72.0, self.config).score, 0.3, delta=1e-6)

    def test_beyond_72_hours_is_penalized(self):
        self.assertAlmostEqual(time_score_from_diff(73.0, self.config).score, 0.1, delta=1e-6)
        self.assertAlmostEqual(time_score_from_diff(1000.0, self.config).score, 0.1, delta=1e-6)

    def test_missing_time_uses_neutral_score_without_error(self):
        for value in (None, "", "   ", "昨天下午", {}, [], {"weird": 1}):
            window = parse_time_window(value, self.config)
            self.assertFalse(window.known, f"{value!r} 不应解析成已知时间")
            result = score_time(value, value, self.config)
            self.assertAlmostEqual(result.score, 0.5, delta=1e-6)
            self.assertFalse(result.known)

    def test_missing_one_side_uses_neutral(self):
        result = score_time("2026-09-28T15:00:00", None, self.config)
        self.assertAlmostEqual(result.score, 0.5, delta=1e-6)

    def test_monotonic_decreasing_in_time_difference(self):
        diffs = [0.0, 0.5, 1.0, 2.0, 5.0, 11.9, 12.0, 23.9, 24.0, 71.9, 72.0, 100.0, 500.0]
        scores = [time_score_from_diff(d, self.config).score for d in diffs]
        for previous, current in zip(scores, scores[1:]):
            self.assertLessEqual(current, previous + 1e-9, f"时间分未单调下降：{scores}")

    def test_range_vs_point_uses_shortest_distance(self):
        """兼容「失物时间范围 vs 拾取单点时间」。"""
        lost_range = {"start": "2026-09-28T10:00:00", "end": "2026-09-28T18:00:00"}
        inside = score_time(lost_range, "2026-09-28T15:00:00", self.config)
        self.assertAlmostEqual(inside.diff_hours, 0.0, delta=1e-6)
        self.assertAlmostEqual(inside.score, 1.0, delta=1e-6)

        after = score_time(lost_range, "2026-09-28T20:00:00", self.config)
        self.assertAlmostEqual(after.diff_hours, 2.0, delta=1e-6)

        before = score_time(lost_range, "2026-09-28T04:00:00", self.config)
        self.assertAlmostEqual(before.diff_hours, 6.0, delta=1e-6)

    def test_time_accepts_several_formats(self):
        for value in ("2026-09-28T15:00:00", "2026-09-28 15:00", "2026/09/28 15:00", "2026-09-28"):
            self.assertTrue(parse_time_window(value, self.config).known, value)

    def test_negative_diff_is_handled(self):
        self.assertIsInstance(time_score_from_diff(-3.0, self.config).score, float)


class TestLocationScore(unittest.TestCase):
    """地点合理度：同地点 1.0 / 不同校区 0.1 / 未知 0.5 等档位。"""

    @classmethod
    def setUpClass(cls):
        cls.index = get_index()
        cls.tiers = config_loader.load_config().location_tiers

    def test_tiers_from_config(self):
        self.assertAlmostEqual(self.tiers["same_spot"], 1.00, places=6)
        self.assertAlmostEqual(self.tiers["same_building_diff_floor"], 0.85, places=6)
        self.assertAlmostEqual(self.tiers["adjacent_building"], 0.60, places=6)
        self.assertAlmostEqual(self.tiers["same_campus_far"], 0.35, places=6)
        self.assertAlmostEqual(self.tiers["cross_campus"], 0.10, places=6)
        self.assertAlmostEqual(self.tiers["unknown"], 0.50, places=6)

    def test_same_spot_is_full_score(self):
        result = score_locations("东湖校区图书馆三楼阅览室", "东湖校区图书馆三楼阅览室", self.index)
        self.assertEqual(result.tier, "same_spot")
        self.assertAlmostEqual(result.score, 1.0, delta=1e-6)

    def test_same_floor_without_spot_is_full_score(self):
        """同一建筑同一楼层但未指明具体地点 → 同样给满分（同 spot 规则的等价档）。"""
        result = score_locations("图书馆三楼", "图书馆三楼", self.index)
        self.assertEqual(result.tier, "same_floor")
        self.assertAlmostEqual(result.score, 1.0, delta=1e-6)

    def test_same_building_different_floor(self):
        result = score_locations("东湖校区图书馆三楼阅览室", "东湖校区图书馆四楼", self.index)
        self.assertEqual(result.tier, "same_building_diff_floor")
        self.assertAlmostEqual(result.score, 0.85, delta=1e-6)

    def test_adjacent_building(self):
        result = score_locations("东湖校区图书馆一楼", "东湖校区第一教学楼一楼", self.index)
        self.assertEqual(result.tier, "adjacent_building")
        self.assertAlmostEqual(result.score, 0.60, delta=1e-6)

    def test_same_campus_far(self):
        result = score_locations("东湖校区图书馆三楼", "东湖校区田径场入口", self.index)
        self.assertEqual(result.tier, "same_campus_far")
        self.assertAlmostEqual(result.score, 0.35, delta=1e-6)

    def test_cross_campus(self):
        for left, right in (("图书馆三楼", "西山校区实验楼三楼"),
                            ("东湖校区图书馆三楼", "西山校区第一食堂一楼")):
            result = score_locations(left, right, self.index)
            self.assertEqual(result.tier, "cross_campus")
            self.assertAlmostEqual(result.score, 0.10, delta=1e-6)

    def test_unknown_location_uses_neutral(self):
        for left, right in ((None, "图书馆三楼"), ("", "图书馆三楼"),
                            ("图书馆三楼", "某个不存在的地方"), ("???", "!!!"),
                            ("不知道", None), (None, None)):
            result = score_locations(left, right, self.index)
            self.assertEqual(result.tier, "unknown", f"{left!r} / {right!r}")
            self.assertAlmostEqual(result.score, 0.50, delta=1e-6)

    def test_parse_returns_four_keys(self):
        parsed = parse_location("图书馆三楼", self.index).to_dict()
        self.assertEqual(set(parsed), {"campus", "building", "floor", "spot"})
        self.assertIsNotNone(parsed["campus"])
        self.assertEqual(parsed["floor"], "三楼")

    def test_parse_store_and_food_shop_alias(self):
        parsed = parse_location("三食堂门口", self.index)
        self.assertEqual(parsed.building_name, "第三食堂")
        self.assertEqual(parsed.spot, "entrance")

    def test_parse_west_campus(self):
        parsed = parse_location("西山校区实验楼三楼实验室", self.index)
        self.assertEqual(parsed.campus_name, "西山校区")
        self.assertEqual(parsed.building_name, "实验楼")
        self.assertEqual(parsed.floor, "3")
        self.assertEqual(parsed.spot, "lab_room")

    def test_adjacency_is_symmetric_in_config(self):
        self.assertTrue(self.index.is_adjacent("east", "library", "teaching_a"))
        self.assertTrue(self.index.is_adjacent("east", "teaching_a", "library"))

    def test_hierarchy_has_two_campuses(self):
        campuses = {campus["code"] for campus in self.index.config.locations["campuses"]}
        self.assertEqual(campuses, {"east", "west"})
        for campus in self.index.config.locations["campuses"]:
            self.assertGreaterEqual(len(campus["buildings"]), 3)


class TestMockImageEmbedder(unittest.TestCase):
    """图像向量接口：确定性、幂等、可插拔。"""

    def setUp(self):
        self.embedder = MockImageEmbedder(dimension=64, seed="test-seed")

    def test_embedding_is_idempotent(self):
        ref = "https://cdn.example.com/a/black-earbuds-01.jpg"
        first = self.embedder.embed(ref)
        for _ in range(3):
            self.assertEqual(first, self.embedder.embed(ref))
        self.assertEqual(len(first), 64)

    def test_embedding_is_deterministic_across_instances(self):
        ref = "https://cdn.example.com/a/black-earbuds-01.jpg"
        other = MockImageEmbedder(dimension=64, seed="test-seed")
        self.assertEqual(self.embedder.embed(ref), other.embed(ref))

    def test_different_inputs_give_different_vectors(self):
        vector_a = self.embedder.embed("https://x/black-earbuds.jpg")
        vector_b = self.embedder.embed("https://x/white-bottle.jpg")
        self.assertNotEqual(vector_a, vector_b)
        self.assertLess(cosine_similarity(vector_a, vector_b), 0.99)

    def test_seed_changes_vector(self):
        ref = "https://x/black-earbuds.jpg"
        self.assertNotEqual(self.embedder.embed(ref), MockImageEmbedder(seed="other").embed(ref))

    def test_empty_input_returns_zero_vector(self):
        vector = self.embedder.embed(None)
        self.assertEqual(len(vector), 64)
        self.assertEqual(sum(vector), 0.0)

    def test_cosine_similarity_bounds(self):
        vector_a = self.embedder.embed("https://x/a.jpg")
        self.assertAlmostEqual(cosine_similarity(vector_a, vector_a), 1.0, delta=1e-9)
        self.assertAlmostEqual(cosine_similarity([], []), 0.0, delta=1e-9)
        self.assertAlmostEqual(cosine_similarity([0.0, 0.0], [1.0, 1.0]), 0.0, delta=1e-9)

    def test_engine_uses_configured_backend(self):
        embedder = get_image_embedder()
        self.assertTrue(hasattr(embedder, "embed"))

    def test_engine_accepts_custom_backend_by_registration(self):
        """真实模型替换点：注册后按名称即可注入，matcher 不依赖具体类名。"""

        class KeywordEmbedder:
            def __init__(self, keywords):
                self.keywords = keywords

            def embed(self, image_ref, extra_tokens=None):
                text = str(image_ref or "") + str(extra_tokens or "")
                return [1.0 if word in text else 0.0 for word in self.keywords]

        register_image_embedder("unit_test_backend", KeywordEmbedder(["无线耳机", "水杯"]))
        engine = MatchEngine(image_embedder="unit_test_backend")
        score = engine.score_image(
            ItemView.from_payload(ITEM_LOST, engine.extractor),
            ItemView.from_payload(ITEM_FOUND, engine.extractor),
        )
        self.assertGreaterEqual(score.score, 0.0)
        self.assertLessEqual(score.score, 1.0)

    def test_broken_backend_degrades_to_neutral(self):
        class BrokenEmbedder:
            def embed(self, image_ref, extra_tokens=None):
                raise RuntimeError("后端不可用")

        register_image_embedder("unit_test_broken", BrokenEmbedder())
        engine = MatchEngine(image_embedder="unit_test_broken")
        score = engine.score_image(
            ItemView.from_payload(ITEM_LOST, engine.extractor),
            ItemView.from_payload(ITEM_FOUND, engine.extractor),
        )
        self.assertAlmostEqual(score.score, 0.5, delta=1e-6)
        self.assertTrue(score.uncertainties)

    def test_same_item_gets_higher_image_score_than_different_item(self):
        engine = MatchEngine()
        same = engine.score_image(
            ItemView.from_payload(ITEM_LOST, engine.extractor),
            ItemView.from_payload(ITEM_FOUND, engine.extractor),
        )
        different = engine.score_image(
            ItemView.from_payload(ITEM_LOST, engine.extractor),
            ItemView.from_payload(_with(category="水杯", color="白色",
                                        attributes={"brand": "膳魔师", "model": "JNL-402"},
                                        image_url="https://x/demo/bottle/found-9999-v4.jpg"),
                                  engine.extractor),
        )
        self.assertGreater(same.score, different.score)


class TestAttributeExtractor(unittest.TestCase):
    """属性提取：Schema 校验 + 空输入降级。"""

    @classmethod
    def setUpClass(cls):
        cls.extractor = AttributeExtractor()

    def test_schema_keys_are_fixed(self):
        result = self.extractor.extract(
            description="黑色的无线蓝牙耳机，盒盖内侧疑似有一道划痕，品牌看不清",
            title="无线耳机",
            image_url="https://x/demo/earbuds/lost-1001-v0.jpg",
        ).to_dict()
        self.assertEqual(set(result), set(ATTRIBUTE_SCHEMA["required"]))
        self.assertEqual(validate_attributes(result), [])

    def test_extracts_expected_attributes(self):
        result = self.extractor.extract(
            description="黑色的无线蓝牙耳机，椭圆形充电盒，盒盖内侧有一道划痕",
            title="无线耳机",
            image_url="https://x/demo/earbuds/lost-1001-v0.jpg",
        ).to_dict()
        self.assertEqual(result["category"], "无线耳机")
        self.assertEqual(result["color"], "黑色")
        self.assertTrue(result["visible_features"])
        self.assertTrue(result["keywords"])
        self.assertIsNone(result["brand"])
        self.assertTrue(result["uncertainties"])

    def test_empty_input_degrades_without_exception(self):
        for payload in ({}, {"description": None}, {"description": ""},
                        {"description": "   ", "image_url": None},
                        {"description": 12345}, {"description": "！@#￥%……&*（）"},
                        {"description": [], "image_url": {}}):
            result = self.extractor.extract(
                description=payload.get("description"),
                image_url=payload.get("image_url"),
            ).to_dict()
            self.assertEqual(validate_attributes(result), [], f"{payload!r} 未通过 Schema")
            self.assertTrue(result["uncertainties"], f"{payload!r} 缺少 uncertainties")
            self.assertIsNone(result["category"])

    def test_uncertainty_marker_is_preserved(self):
        result = self.extractor.extract(description="好像是黑色的蓝牙耳机").to_dict()
        self.assertTrue(any("好像" in item for item in result["uncertainties"]))

    def test_brand_alias_normalized(self):
        result = self.extractor.extract(description="华为FreeBuds 5i 白色蓝牙耳机").to_dict()
        self.assertEqual(result["brand"], "huawei")
        self.assertEqual(result["category"], "无线耳机")

    def test_campus_card_keywords(self):
        result = self.extractor.extract(description="校园卡，蓝色卡套，卡面有划痕").to_dict()
        self.assertEqual(result["category"], "校园卡")
        self.assertEqual(result["color"], "蓝色")

    def test_keywords_are_deterministic(self):
        text = "黑色无线耳机，椭圆形充电盒，盒体右侧有划痕"
        self.assertEqual(self.extractor.extract_keywords(text), self.extractor.extract_keywords(text))

    def test_non_string_inputs_do_not_raise(self):
        result = self.extractor.extract(description={"a": 1}, title=[1, 2, 3], image_url=42).to_dict()
        self.assertEqual(validate_attributes(result), [])


class TestMatchEngineEntrypoint(unittest.TestCase):
    """``MatchEngine.match`` 的边界行为。"""

    @classmethod
    def setUpClass(cls):
        cls.engine = MatchEngine()

    def test_empty_candidates_returns_empty_results(self):
        result = self.engine.match(ITEM_LOST, [])
        self.assertEqual(result["results"], [])
        self.assertEqual(result["model_version"], config_loader.load_config().model_version)

    def test_missing_query_item_is_handled(self):
        for query in (None, {}, "not-a-dict", 0):
            result = self.engine.match(query, [ITEM_FOUND])
            self.assertEqual(result["results"], [])
            self.assertTrue(result.get("warnings"))

    def test_results_sorted_by_total_score_desc(self):
        candidates = [
            _with(id=1, category="书籍", color="红色", location="西山校区实验楼三楼"),
            _with(id=2),
            _with(id=3, location="东湖校区图书馆四楼", event_time="2026-09-29T15:00:00"),
        ]
        results = self.engine.match(ITEM_LOST, candidates)["results"]
        totals = [item["total_score"] for item in results]
        self.assertEqual(totals, sorted(totals, reverse=True))

    def test_top_k_limits_results(self):
        candidates = [_with(id=index) for index in range(1, 8)]
        self.assertEqual(len(self.engine.match(ITEM_LOST, candidates, top_k=3)["results"]), 3)

    def test_broken_candidate_does_not_break_batch(self):
        candidates = [ITEM_FOUND, "not-a-dict", 42, None, _with(id=7)]
        results = self.engine.match(ITEM_LOST, candidates)["results"]
        self.assertGreaterEqual(len(results), 1)

    def test_evidence_and_uncertainties_are_strings(self):
        outcome = self.engine.match_pair(ITEM_LOST, _with(attributes={"brand": None}, color="白色"))
        self.assertTrue(all(isinstance(x, str) for x in outcome.evidence))
        self.assertTrue(all(isinstance(x, str) for x in outcome.uncertainties))
        self.assertTrue(any("颜色" in x for x in outcome.uncertainties))
        conflicting = self.engine.match_pair(
            ITEM_LOST, _with(attributes={"brand": "漫步者", "model": "LolliPods Plus"}))
        self.assertTrue(any("品牌" in x for x in conflicting.uncertainties))
        self.assertLess(conflicting.scores["brand_model"], outcome.scores["brand_model"])

    def test_brand_spelling_variants_are_not_conflicts(self):
        """Apple / apple 属同一品牌，不应被判为品牌冲突。"""
        outcome = self.engine.match_pair(ITEM_LOST, _with(attributes={"brand": "apple"}))
        self.assertGreater(outcome.scores["brand_model"], 0.9)

    def test_hidden_features_do_not_leak_into_response(self):
        payload = self.engine.match(ITEM_LOST, [ITEM_FOUND])
        self.assertNotIn("hidden", str(payload).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
