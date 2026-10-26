"""接口契约 + 演示数据集完整性测试（纯标准库）。

覆盖点：
* 三个端点的请求/响应字段与契约一致（``main.handle_*`` 纯逻辑层，
  没有 fastapi 也能跑；装了 fastapi 时额外校验 OpenAPI 路由）
* 演示数据集规模：物品 ≥60（lost ≥30 / found ≥30）、正样本 ≥20、负样本 ≥20、
  hard_negative ≥8、必填字段齐全、校园卡为虚构信息
* ``expected_total_score_range`` 与实际算出的分数自洽，且正样本显著高于困难负样本
"""

from __future__ import annotations

import json
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

from app import main as main_module
from app import mock_data
from app.config_loader import load_config
from app.matcher import MatchEngine

ROOT = _ROOT
DATA_DIR = os.path.join(ROOT, "data")

SCORE_KEYS = {
    "image_score", "text_score", "category_score",
    "time_score", "location_score", "brand_model_score",
}

ITEM_REQUIRED_FIELDS = (
    "id", "post_type", "category", "title", "description", "color", "brand",
    "location", "event_time", "public_features", "hidden_features", "ai_attributes",
)

CATEGORIES = {"无线耳机", "水杯", "书籍", "校园卡", "雨伞", "背包"}

MATCH_REQUIRED_FIELDS = (
    "sample_id", "label", "lost_item_id", "found_item_id",
    "expected_total_score_range", "time_diff_hours", "notes",
)

MATCH_LABELS = {"positive", "hard_negative", "negative"}

#: 校园卡/证件类必须出现的虚构写法
FICTIONAL_PATTERNS = ("2021XXXXXX", "2022XXXXXX", "2023XXXXXX", "2024XXXXXX",
                      "张某某", "王某某", "陈某某", "刘某某", "赵某某")


def _load(name):
    with open(os.path.join(DATA_DIR, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class TestMatchEndpointContract(unittest.TestCase):
    """POST /algorithm/v1/match 的返回结构。"""

    @classmethod
    def setUpClass(cls):
        cls.query = {
            "id": 1001,
            "image_url": "https://cdn.example-campus.invalid/demo/earbuds/lost-1001-v0.jpg",
            "category": "无线耳机",
            "description": "黑色无线耳机，椭圆形充电盒，盒体右侧有划痕",
            "attributes": {"color": "黑色", "brand": None, "features": ["盒体右侧有划痕"]},
            "location": "东湖校区图书馆三楼阅览室",
            "event_time": "2026-09-28T15:00:00",
        }
        cls.candidates = [
            {"id": 2001, "category": "无线耳机",
             "description": "黑色无线耳机，椭圆形充电盒，盒体右侧有划痕",
             "attributes": {"color": "黑色", "brand": "Apple", "features": ["盒体右侧有划痕"]},
             "location": "东湖校区图书馆三楼阅览室", "event_time": "2026-09-28T15:20:00"},
            {"id": 2002, "category": "无线耳机",
             "description": "黑色无线耳机，方形充电盒，盒角有磕碰",
             "attributes": {"color": "黑色", "brand": "漫步者", "features": ["盒角有磕碰"]},
             "location": "东湖校区田径场入口", "event_time": "2026-09-28T15:30:00"},
        ]
        cls.payload = main_module.handle_match({"query_item": cls.query,
                                                "candidate_items": cls.candidates})

    def test_top_level_fields(self):
        self.assertIn("model_version", self.payload)
        self.assertIn("results", self.payload)
        self.assertIsInstance(self.payload["results"], list)
        self.assertEqual(self.payload["model_version"], load_config().model_version)

    def test_result_item_shape(self):
        self.assertTrue(self.payload["results"])
        for item in self.payload["results"]:
            self.assertEqual(set(item), {"candidate_id", "total_score", "scores",
                                         "evidence", "uncertainties"})
            self.assertEqual(set(item["scores"]), SCORE_KEYS)
            self.assertIsInstance(item["evidence"], list)
            self.assertIsInstance(item["uncertainties"], list)
            self.assertTrue(all(isinstance(x, str) for x in item["evidence"]))

    def test_scores_are_normalized(self):
        for item in self.payload["results"]:
            self.assertGreaterEqual(item["total_score"], 0.0)
            self.assertLessEqual(item["total_score"], 1.0)
            for key, value in item["scores"].items():
                self.assertGreaterEqual(value, 0.0, key)
                self.assertLessEqual(value, 1.0, key)

    def test_total_equals_weighted_sum_of_reported_scores(self):
        weights = load_config().weights
        for item in self.payload["results"]:
            manual = (
                weights["image"] * item["scores"]["image_score"]
                + weights["text"] * item["scores"]["text_score"]
                + weights["category"] * item["scores"]["category_score"]
                + weights["time"] * item["scores"]["time_score"]
                + weights["location"] * item["scores"]["location_score"]
                + weights["brand_model"] * item["scores"]["brand_model_score"]
            )
            self.assertAlmostEqual(item["total_score"], manual, delta=2e-4)

    def test_best_candidate_is_the_identical_item(self):
        self.assertEqual(self.payload["results"][0]["candidate_id"], 2001)
        self.assertGreater(self.payload["results"][0]["total_score"], 0.8)

    def test_empty_candidates(self):
        payload = main_module.handle_match({"query_item": self.query, "candidate_items": []})
        self.assertEqual(payload["results"], [])

    def test_missing_body_does_not_raise(self):
        for body in (None, {}, {"candidate_items": None}, {"query_item": "bad"}):
            payload = main_module.handle_match(body)
            self.assertIn("results", payload)
            self.assertIsInstance(payload["results"], list)

    def test_example_from_spec_is_accepted(self):
        """题目给出的示例请求体必须能被接受（candidate_items 为空）。"""
        body = {
            "query_item": {
                "id": 1001,
                "image_url": "https://example.com/1.jpg",
                "category": "无线耳机",
                "description": "黑色无线耳机",
                "attributes": {"color": "黑色", "brand": None, "features": ["盒体右侧有划痕"]},
                "location": "图书馆三楼",
                "event_time": "2026-09-28T15:00:00",
            },
            "candidate_items": [],
        }
        payload = main_module.handle_match(body)
        self.assertEqual(payload["model_version"], "baseline-1.0")
        self.assertEqual(payload["results"], [])


class TestExtractEndpointContract(unittest.TestCase):
    """POST /algorithm/v1/extract-attributes 的返回结构。"""

    def test_full_payload(self):
        payload = main_module.handle_extract({
            "image_url": "https://cdn.example-campus.invalid/demo/earbuds/lost-1001-v0.jpg",
            "description": "黑色的无线蓝牙耳机，椭圆形充电盒，盒盖内侧疑似有一道划痕。品牌标识不清晰。",
        })
        for field in ("category", "brand", "model", "color", "material",
                      "visible_features", "accessories", "keywords", "uncertainties"):
            self.assertIn(field, payload)
        self.assertEqual(payload["category"], "无线耳机")
        self.assertEqual(payload["color"], "黑色")
        self.assertIsInstance(payload["visible_features"], list)
        self.assertIsInstance(payload["uncertainties"], list)
        self.assertTrue(payload["uncertainties"])

    def test_empty_body_degrades(self):
        for body in (None, {}, {"description": None, "image_url": None},
                     {"description": ""}, {"description": "   "}):
            payload = main_module.handle_extract(body)
            self.assertIsNone(payload["category"])
            self.assertEqual(payload["keywords"], [])
            self.assertTrue(payload["uncertainties"])

    def test_non_dict_body(self):
        for body in ("bad", 42, []):
            payload = main_module.handle_extract(body)  # type: ignore[arg-type]
            self.assertIn("uncertainties", payload)


class TestClaimEndpointContract(unittest.TestCase):
    """POST /algorithm/v1/claim-credibility 的返回结构。"""

    def setUp(self):
        self.body = {
            "match_result": {"total_score": 0.9, "candidate_id": 2001},
            "questions": [
                {"question_id": "q_scratch_detail", "question": "表面有划痕吗？",
                 "reference_answer": "盒体右侧有一道划痕", "match_mode": "fuzzy_with_synonyms"},
                {"question_id": "q_bluetooth_name", "question": "蓝牙名称？",
                 "standard_answer": "YiQi-AirPods", "match_mode": "field_exact"},
            ],
            "answers": ["盒子右边刮了一道", "yiqi airpods"],
            "target_item": {
                "title": "黑色无线耳机", "description": "黑色无线耳机，带充电盒，盒体右侧有划痕",
                "category": "无线耳机", "color": "黑色", "brand": "Apple",
                "location": "东湖校区图书馆三楼", "event_time": "2026-09-28T15:00:00",
                "image_url": "https://x/y.jpg", "public_features": ["盒体右侧有划痕"],
            },
        }

    def test_response_shape(self):
        payload = main_module.handle_claim_credibility(self.body)
        self.assertIn("credibility_score", payload)
        self.assertIn("risk_level", payload)
        self.assertIn("per_question", payload)
        self.assertIn("risk_tips", payload)
        self.assertIn(payload["risk_level"], {"low", "medium", "high"})

    def test_per_question_shape(self):
        payload = main_module.handle_claim_credibility(self.body)
        self.assertEqual(len(payload["per_question"]), 2)
        for item in payload["per_question"]:
            self.assertTrue({"question_id", "matched", "score", "reason"} <= set(item))
            self.assertIsInstance(item["question_id"], str)
            self.assertIsInstance(item["matched"], bool)
            self.assertIsInstance(item["score"], float)
            self.assertIsInstance(item["reason"], str)

    def test_never_returns_standard_answer(self):
        payload = main_module.handle_claim_credibility(self.body)
        blob = json.dumps(payload, ensure_ascii=False)
        for secret in ("YiQi-AirPods", "盒体右侧有一道划痕"):
            self.assertNotIn(secret, blob)

    def test_missing_body(self):
        for body in (None, {}, {"questions": []}, {"questions": None, "answers": None}):
            payload = main_module.handle_claim_credibility(body)
            self.assertIn(payload["risk_level"], {"low", "medium", "high"})
            self.assertIsInstance(payload["per_question"], list)


class TestRouteTable(unittest.TestCase):
    """三个契约端点必须存在且路径不可更改。"""

    def test_three_contract_routes_registered(self):
        for path in ("/algorithm/v1/match",
                     "/algorithm/v1/extract-attributes",
                     "/algorithm/v1/claim-credibility"):
            self.assertIn(("POST", path), main_module.ROUTE_TABLE)

    def test_route_handlers_are_callable(self):
        for handler in main_module.ROUTE_TABLE.values():
            self.assertTrue(callable(handler))

    def test_fastapi_app_exposes_routes_when_available(self):
        if not main_module.FASTAPI_AVAILABLE:
            self.assertIsNone(main_module.app)
            self.skipTest("未安装 fastapi：已按设计降级为纯逻辑调用 + 标准库 HTTP 服务")
        paths = {route.path for route in main_module.app.routes}
        self.assertIn("/algorithm/v1/match", paths)
        self.assertIn("/algorithm/v1/extract-attributes", paths)
        self.assertIn("/algorithm/v1/claim-credibility", paths)

    def test_core_modules_do_not_import_fastapi(self):
        """核心算法模块不得依赖 fastapi（降级设计的硬性要求）。"""
        from app import credibility, extractor, locations, matcher, mock_data, schemas

        for module in (credibility, extractor, locations, matcher, mock_data, schemas):
            with open(module.__file__, "r", encoding="utf-8") as handle:
                source = handle.read()
            self.assertNotIn("import fastapi", source, module.__name__)
            self.assertNotIn("from fastapi", source, module.__name__)


class TestDemoItemsDataset(unittest.TestCase):
    """data/demo_items.json 的规模与字段完整性。"""

    @classmethod
    def setUpClass(cls):
        cls.items = _load("demo_items.json")

    def test_scale(self):
        self.assertGreaterEqual(len(self.items), 60)
        lost = [item for item in self.items if item["post_type"] == "lost"]
        found = [item for item in self.items if item["post_type"] == "found"]
        self.assertGreaterEqual(len(lost), 30)
        self.assertGreaterEqual(len(found), 30)
        self.assertEqual(len(lost) + len(found), len(self.items))

    def test_categories_covered(self):
        self.assertTrue(CATEGORIES <= {item["category"] for item in self.items})

    def test_required_fields_present(self):
        for item in self.items:
            for field in ITEM_REQUIRED_FIELDS:
                self.assertIn(field, item, f"item {item.get('id')} 缺少字段 {field}")
            self.assertIsInstance(item["public_features"], list)
            self.assertIsInstance(item["hidden_features"], list)
            self.assertTrue(item["hidden_features"], f"item {item['id']} 缺少 hidden_features")
            self.assertIsInstance(item["ai_attributes"], dict)

    def test_ids_unique_and_typed(self):
        ids = [item["id"] for item in self.items]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(isinstance(value, int) for value in ids))

    def test_post_type_values(self):
        self.assertTrue({item["post_type"] for item in self.items} <= {"lost", "found"})

    def test_campus_cards_use_fictional_identity(self):
        cards = [item for item in self.items if item["category"] in ("校园卡", "证件卡")]
        self.assertTrue(cards, "数据集中应包含校园卡类样本")
        for card in cards:
            blob = json.dumps(card, ensure_ascii=False)
            self.assertIn("学号", blob)
            self.assertTrue(any(pattern in blob for pattern in FICTIONAL_PATTERNS),
                            f"校园卡 {card['id']} 未使用虚构身份信息")

    def test_hidden_features_look_realistic(self):
        for item in self.items:
            for feature in item["hidden_features"]:
                self.assertIsInstance(feature, str)
                self.assertGreaterEqual(len(feature), 4, f"{item['id']} 的隐藏特征过短：{feature}")

    def test_dataset_is_deterministic(self):
        self.assertEqual(
            json.dumps(mock_data.build_demo_items(), ensure_ascii=False, sort_keys=True),
            json.dumps(_load("demo_items.json"), ensure_ascii=False, sort_keys=True),
            "mock_data 重新生成的结果与已落盘的数据不一致（确定性被破坏）",
        )


class TestDemoMatchesDataset(unittest.TestCase):
    """data/demo_matches.json 的规模、标签与分值自洽性。"""

    @classmethod
    def setUpClass(cls):
        cls.items = _load("demo_items.json")
        cls.matches = _load("demo_matches.json")
        cls.by_id = {int(item["id"]): item for item in cls.items}
        cls.engine = MatchEngine()

    def _label(self, label):
        return [sample for sample in self.matches if sample["label"] == label]

    def test_scale(self):
        self.assertGreaterEqual(len(self.matches), 40)
        self.assertGreaterEqual(len(self._label("positive")), 20)
        negatives = self._label("hard_negative") + self._label("negative")
        self.assertGreaterEqual(len(negatives), 20)
        self.assertGreaterEqual(len(self._label("hard_negative")), 8)

    def test_labels_are_valid(self):
        for sample in self.matches:
            self.assertIn(sample["label"], MATCH_LABELS)

    def test_required_fields_present(self):
        for sample in self.matches:
            for field in MATCH_REQUIRED_FIELDS:
                self.assertIn(field, sample, f"样本 {sample.get('sample_id')} 缺少字段 {field}")
            self.assertEqual(len(sample["expected_total_score_range"]), 2)

    def test_item_ids_exist(self):
        for sample in self.matches:
            self.assertIn(int(sample["lost_item_id"]), self.by_id)
            self.assertIn(int(sample["found_item_id"]), self.by_id)

    def test_positive_pairs_share_category(self):
        for sample in self._label("positive"):
            lost = self.by_id[int(sample["lost_item_id"])]
            found = self.by_id[int(sample["found_item_id"])]
            self.assertEqual(lost["category"], found["category"])

    def test_hard_negatives_are_same_category_and_same_color(self):
        for sample in self._label("hard_negative"):
            lost = self.by_id[int(sample["lost_item_id"])]
            found = self.by_id[int(sample["found_item_id"])]
            self.assertEqual(lost["category"], found["category"], sample["sample_id"])
            lost_view = sample.get("lost_view") or {}
            found_view = sample.get("found_view") or {}
            self.assertEqual(lost_view.get("color"), found_view.get("color"),
                             f"{sample['sample_id']} 困难负样本颜色应相同")
            self.assertNotEqual(lost_view.get("hidden_features"), found_view.get("hidden_features"),
                                f"{sample['sample_id']} 困难负样本的隐藏特征应不同")

    def test_negatives_are_not_same_item(self):
        for sample in self.matches:
            if sample["label"] == "negative":
                self.assertNotEqual(sample["lost_item_id"], sample["found_item_id"])

    def test_time_distribution_covers_high_and_low(self):
        diffs = [float(sample["time_diff_hours"]) for sample in self.matches]
        self.assertTrue(any(value <= 1.0 for value in diffs), "缺少 1 小时内的高分样本")
        self.assertTrue(any(value >= 72.0 for value in diffs), "缺少时间差数天的低分样本")

    def test_expected_range_contains_actual_score(self):
        """expected_total_score_range 必须覆盖算法真实分值（数据自洽）。"""
        from scripts.generate_demo_data import _score_sample

        for sample in self.matches:
            low, high = sample["expected_total_score_range"]
            score = _score_sample(self.engine, sample, self.by_id)
            self.assertGreaterEqual(score, low - 0.02,
                                    f"{sample['sample_id']} 实际分 {score:.4f} 低于区间下界 {low}")
            self.assertLessEqual(score, high + 0.02,
                                 f"{sample['sample_id']} 实际分 {score:.4f} 高于区间上界 {high}")

    def test_positives_score_above_hard_negatives(self):
        from scripts.generate_demo_data import _score_sample

        positives = [_score_sample(self.engine, sample, self.by_id) for sample in self._label("positive")]
        hard = [_score_sample(self.engine, sample, self.by_id) for sample in self._label("hard_negative")]
        plain = [_score_sample(self.engine, sample, self.by_id) for sample in self._label("negative")]
        self.assertGreater(min(positives), max(hard),
                           f"正样本最低分 {min(positives):.4f} 未高于困难负样本最高分 {max(hard):.4f}")
        self.assertGreater(min(hard), max(plain),
                           f"困难负样本最低分 {min(hard):.4f} 未高于普通负样本最高分 {max(plain):.4f}")

    def test_dataset_is_deterministic(self):
        regenerated = mock_data.build_demo_matches(self.items)
        for sample, fresh in zip(self.matches, regenerated):
            self.assertEqual(sample["sample_id"], fresh["sample_id"])
            self.assertEqual(sample["label"], fresh["label"])
            self.assertEqual(sample["lost_item_id"], fresh["lost_item_id"])
            self.assertEqual(sample["found_item_id"], fresh["found_item_id"])


class TestDataFilesExist(unittest.TestCase):
    def test_expected_files(self):
        for name in ("demo_items.json", "demo_matches.json", "README.md"):
            path = os.path.join(DATA_DIR, name)
            self.assertTrue(os.path.exists(path), f"缺少 {path}")
            self.assertGreater(os.path.getsize(path), 100, f"{path} 内容过小")

    def test_config_files_exist(self):
        config_dir = os.path.join(ROOT, "config")
        for name in ("matching.yaml", "locations.yaml", "questions.yaml"):
            self.assertTrue(os.path.exists(os.path.join(config_dir, name)), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
