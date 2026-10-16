"""``app.credibility`` 单元测试（纯标准库）。

覆盖点（对应验收要求）：
* 可信度分 = 0.60×答案匹配 + 0.20×候选综合分 + 0.10×完整度 + 0.10×行为分
* 全对 > 部分对 > 全错
* 同义表达能匹配上（划痕/刮痕、右侧/右边）
* 关键字段（蓝牙名称/学号）大小写、空格、全角差异能匹配上
* risk_level ∈ {low, medium, high}，规则来自配置
* **绝不回显标准答案**（响应里不出现参考答案原文）
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
from app.credibility import CredibilityScorer, compute_credibility

QUESTIONS = [
    {"id": "q_scratch_detail", "question": "物品表面是否有划痕？位置在哪？",
     "reference_answer": "盒体右侧有一道划痕", "match_mode": "fuzzy_with_synonyms", "weight": 1.0},
    {"id": "q_bluetooth_name", "question": "蓝牙显示名称是什么？",
     "reference_answer": "YiQi-AirPods", "match_mode": "field_exact", "weight": 1.5},
    {"id": "q_accessory", "question": "附带哪些配件？",
     "reference_answer": "左右耳机和充电盒", "match_mode": "fuzzy_with_synonyms", "weight": 0.8},
]

ANSWERS_ALL_RIGHT = ["盒子右边刮了一道", "yiqi airpods", "耳机和充电盒都在"]
ANSWERS_PARTIAL = ["右边有划痕", "别的名字", "只有耳机"]
ANSWERS_ALL_WRONG = ["没有划痕", "HUAWEI-FreeBuds-8821", "没有配件"]

TARGET_ITEM = {
    "title": "黑色无线耳机",
    "description": "黑色无线耳机，带充电盒，盒体右侧有划痕",
    "category": "无线耳机",
    "color": "黑色",
    "brand": "Apple",
    "location": "东湖校区图书馆三楼",
    "event_time": "2026-09-28T15:00:00",
    "image_url": "https://cdn.example-campus.invalid/demo/earbuds/lost-1001-v0.jpg",
    "public_features": ["盒体右侧有划痕"],
}


class TestCredibilityWeights(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = config_loader.load_config()
        cls.scorer = CredibilityScorer(cls.config)

    def test_weights_from_config(self):
        weights = self.scorer.weights
        self.assertAlmostEqual(weights["answer_match"], 0.60, places=6)
        self.assertAlmostEqual(weights["candidate_match"], 0.20, places=6)
        self.assertAlmostEqual(weights["completeness"], 0.10, places=6)
        self.assertAlmostEqual(weights["behavior"], 0.10, places=6)
        self.assertAlmostEqual(sum(weights.values()), 1.0, delta=1e-9)

    def test_total_equals_weighted_breakdown(self):
        result = self.scorer.score(QUESTIONS, ANSWERS_ALL_RIGHT, {"total_score": 0.90}, TARGET_ITEM)
        expected = (0.60 * result.breakdown["answer_match"]
                    + 0.20 * result.breakdown["candidate_match"]
                    + 0.10 * result.breakdown["completeness"]
                    + 0.10 * result.breakdown["behavior"])
        self.assertAlmostEqual(result.credibility_score, expected, delta=1e-6)

    def test_risk_levels_from_config(self):
        self.assertEqual(self.scorer.risk_level_of(0.95), "low")
        self.assertEqual(self.scorer.risk_level_of(self.scorer.low_min), "low")
        self.assertEqual(self.scorer.risk_level_of(0.60), "medium")
        self.assertEqual(self.scorer.risk_level_of(0.10), "high")

    def test_missing_everything_uses_neutral_scores(self):
        result = self.scorer.score([], [], None, None, None)
        self.assertAlmostEqual(result.breakdown["answer_match"], 0.50, delta=1e-6)
        self.assertAlmostEqual(result.breakdown["candidate_match"], 0.50, delta=1e-6)
        self.assertAlmostEqual(result.breakdown["completeness"], 0.30, delta=1e-6)
        self.assertAlmostEqual(result.breakdown["behavior"], 0.85, delta=1e-6)
        self.assertIn(result.risk_level, {"low", "medium", "high"})


class TestAnswerMatching(unittest.TestCase):
    """答案匹配：同义容忍 + 关键字段规范化 + 矛盾识别。"""

    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def _matched(self, answer, reference, mode=None):
        question = {"reference_answer": reference}
        if mode:
            question["match_mode"] = mode
        result = self.scorer.score_answer(question, answer)
        return result["matched"], result["score"]

    def test_synonym_scratch_is_matched(self):
        for answer in ("盒体右侧有划痕", "盒体右侧有刮痕", "右边刮了一道", "右侧有一道划伤"):
            matched, score = self._matched(answer, "盒体右侧有一道划痕")
            self.assertTrue(matched, f"{answer!r} 应命中同义表达")
            self.assertAlmostEqual(score, 1.0, delta=1e-6)

    def test_synonym_side_is_matched(self):
        for answer in ("右边有划痕", "右面有划痕", "靠右有划痕"):
            matched, _ = self._matched(answer, "盒体右侧有一道划痕")
            self.assertTrue(matched, f"{answer!r} 应命中「右侧」的同义表达")

    def test_contradiction_is_not_matched(self):
        for answer in ("没有划痕", "左侧有划痕", "物品完好没有损伤"):
            matched, score = self._matched(answer, "盒体右侧有一道划痕")
            self.assertFalse(matched, f"{answer!r} 不应命中")
            self.assertLess(score, 1.0)

    def test_opposite_color_is_not_matched(self):
        matched, score = self._matched("红色保温杯", "蓝色保温杯")
        self.assertFalse(matched)
        self.assertAlmostEqual(score, 0.0, delta=1e-6)

    def test_key_field_ignores_case_space_fullwidth(self):
        reference = "YiQi-AirPods"
        for answer in ("yiqi-airpods", "YiQi AirPods", "YIQI AIRPODS", "ｙｉｑｉ－ａｉｒｐｏｄｓ",
                       " yiqi_airpods ", "YiQiAirPods"):
            matched, score = self._matched(answer, reference, mode="field_exact")
            self.assertTrue(matched, f"{answer!r} 应命中关键字段")
            self.assertAlmostEqual(score, 1.0, delta=1e-6)

    def test_student_id_normalization(self):
        reference = "学号 2023XXXXXX，姓名 张某某"
        for answer in ("学号2023XXXXXX，姓名张某某", "学号 2023xxxxxx 姓名 张某某"):
            matched, _ = self._matched(answer, reference, mode="field_exact")
            self.assertTrue(matched, f"{answer!r} 应命中证件信息")

    def test_wrong_key_field_is_not_matched(self):
        matched, score = self._matched("HUAWEI-FreeBuds-5i-8821", "YiQi-AirPods", mode="field_exact")
        self.assertFalse(matched)
        self.assertAlmostEqual(score, 0.0, delta=1e-6)

    def test_unanswered_question_scores_zero(self):
        for answer in (None, "", "  ", "?"):
            result = self.scorer.score_answer({"reference_answer": "盒体右侧有一道划痕"}, answer)
            self.assertFalse(result["matched"])
            self.assertAlmostEqual(result["score"], 0.0, delta=1e-6)

    def test_question_without_reference_degrades(self):
        result = self.scorer.score_answer({"question": "随便问问"}, "随便答答")
        self.assertIn("score", result)
        self.assertFalse(result["matched"])

    def test_weights_affect_weighted_average(self):
        score, per_question = self.scorer.score_answers(QUESTIONS, ANSWERS_ALL_RIGHT)
        self.assertEqual(len(per_question), 3)
        self.assertAlmostEqual(score, 1.0, delta=1e-6)

    def test_no_questions_uses_neutral(self):
        score, per_question = self.scorer.score_answers([], [])
        self.assertAlmostEqual(score, 0.50, delta=1e-6)
        self.assertEqual(per_question, [])


class TestCredibilityOrdering(unittest.TestCase):
    """全对 > 部分对 > 全错。"""

    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def _score(self, answers, behavior=None):
        return self.scorer.score(QUESTIONS, answers, {"total_score": 0.90}, TARGET_ITEM, behavior)

    def test_all_right_beats_partial_beats_wrong(self):
        all_right = self._score(ANSWERS_ALL_RIGHT)
        partial = self._score(ANSWERS_PARTIAL)
        all_wrong = self._score(ANSWERS_ALL_WRONG)
        self.assertGreater(all_right.credibility_score, partial.credibility_score)
        self.assertGreater(partial.credibility_score, all_wrong.credibility_score)
        self.assertGreater(all_right.answer_match_score, all_wrong.answer_match_score)

    def test_risk_level_reflects_answer_quality(self):
        self.assertEqual(self._score(ANSWERS_ALL_RIGHT).risk_level, "low")
        self.assertEqual(self._score(ANSWERS_ALL_WRONG).risk_level, "high")

    def test_per_question_structure(self):
        result = self._score(ANSWERS_ALL_RIGHT)
        self.assertEqual(len(result.per_question), len(QUESTIONS))
        for item in result.per_question:
            self.assertTrue({"question_id", "matched", "score", "reason"} <= set(item))
            self.assertIsInstance(item["matched"], bool)
            self.assertIsInstance(item["score"], float)
            self.assertIsInstance(item["reason"], str)

    def test_risk_tips_present(self):
        self.assertTrue(self._score(ANSWERS_ALL_WRONG).risk_tips)
        self.assertTrue(all(isinstance(tip, str) for tip in self._score(ANSWERS_ALL_WRONG).risk_tips))


class TestRiskThresholdsAlignWithContract(unittest.TestCase):
    """风险等级阈值必须与冻结契约 docs/api-contract.md §2.6 一致。

    背景：questions.yaml 曾标定为 0.80/0.55，与契约的 0.75/0.45 冲突。
    契约是唯一权威，此处锁定，避免再次漂移。
    """

    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def test_thresholds_match_contract(self):
        self.assertAlmostEqual(self.scorer.low_min, 0.75, delta=1e-9)
        self.assertAlmostEqual(self.scorer.medium_min, 0.45, delta=1e-9)

    def test_boundary_values(self):
        self.assertEqual(self.scorer.risk_level_of(0.75), "low")
        self.assertEqual(self.scorer.risk_level_of(0.7499), "medium")
        self.assertEqual(self.scorer.risk_level_of(0.45), "medium")
        self.assertEqual(self.scorer.risk_level_of(0.4499), "high")


class TestLowAnswerMatchRaisesRisk(unittest.TestCase):
    """「隐藏特征几乎全答错」必须落到 high 风险。

    这是防冒领的核心保障：答案匹配分极低是强负信号，若因缺少行为信号而取中性分，
    可信度会被抬高到 medium，导致冒领申请看起来「风险可控」。

    依赖 questions.yaml → behavior.rules → id: very_low_answer_match
    （condition 必须为完整信号名 `answer_match_score_lt`，简写为 `answer_match_lt`
     会因规则引擎按后缀剥离而查不到信号，导致规则永不触发）。
    """

    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def test_all_wrong_is_high_risk(self):
        result = self.scorer.score(QUESTIONS, ANSWERS_ALL_WRONG, {"total_score": 0.90}, TARGET_ITEM)
        self.assertEqual(result.risk_level, "high", "全部答错必须判为高风险")
        self.assertLess(result.behavior_score, 1.0, "低答案匹配分应触发行为分扣减")

    def test_low_answer_rule_is_configured_and_fires(self):
        rule = {"condition": "answer_match_score_lt", "threshold": 0.30}
        self.assertTrue(self.scorer._evaluate_rule(rule, {"answer_match_score": 0.15}))
        self.assertFalse(self.scorer._evaluate_rule(rule, {"answer_match_score": 0.90}))

    def test_wrong_answers_are_not_reported_as_low_risk(self):
        for answers in (ANSWERS_ALL_WRONG, ANSWERS_PARTIAL):
            result = self.scorer.score(QUESTIONS, answers, {"total_score": 0.90}, TARGET_ITEM)
            self.assertNotEqual(result.risk_level, "low", "答错不应被判为低风险")

    def test_no_questions_uses_neutral_and_does_not_fire_rule(self):
        """没有题目可判 ≠ 答得差：应取中性分，不得误触发冒领规则。"""
        result = self.scorer.score([], [], {"total_score": 0.90}, TARGET_ITEM)
        self.assertAlmostEqual(result.breakdown["behavior"], 0.85, delta=1e-6)
        self.assertAlmostEqual(result.answer_match_score, 0.50, delta=1e-6)


class TestNoAnswerLeak(unittest.TestCase):
    """**绝不返回标准答案**：任何理由文本都不得包含参考答案原文。"""

    SECRETS = ["YiQi-AirPods", "盒体右侧有一道划痕", "左右耳机和充电盒", "2023XXXXXX", "张某某"]

    def test_reasons_never_echo_reference(self):
        scorer = CredibilityScorer()
        for answers in (ANSWERS_ALL_RIGHT, ANSWERS_PARTIAL, ANSWERS_ALL_WRONG):
            result = scorer.score(QUESTIONS, answers, {"total_score": 0.9}, TARGET_ITEM)
            blob = str(result.to_dict())
            for secret in self.SECRETS:
                self.assertNotIn(secret, blob, f"响应泄露了标准答案：{secret}")
            for item in result.per_question:
                self.assertNotIn("reference", item)
                self.assertNotIn("standard_answer", item)

    def test_public_response_has_only_contract_fields(self):
        scorer = CredibilityScorer()
        payload = scorer.score(QUESTIONS, ANSWERS_ALL_RIGHT, {"total_score": 0.9}, TARGET_ITEM).to_dict()
        self.assertEqual(set(payload), {"credibility_score", "risk_level", "per_question",
                                        "risk_tips", "breakdown"})
        for item in payload["per_question"]:
            self.assertTrue(set(item) <= {"question_id", "matched", "score", "reason", "diagnostic"})

    def test_convenience_function_does_not_leak(self):
        payload = compute_credibility(QUESTIONS, ANSWERS_ALL_RIGHT, {"total_score": 0.9}, TARGET_ITEM)
        for secret in self.SECRETS:
            self.assertNotIn(secret, str(payload))


class TestCompletenessAndBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def test_completeness_full_item(self):
        score, missing = self.scorer.score_completeness(TARGET_ITEM)
        self.assertAlmostEqual(score, 1.0, delta=1e-6)
        self.assertEqual(missing, [])

    def test_completeness_missing_fields(self):
        score, missing = self.scorer.score_completeness({"title": "无线耳机"})
        self.assertLess(score, 1.0)
        self.assertTrue(missing)

    def test_completeness_handles_empty(self):
        score, missing = self.scorer.score_completeness(None)
        self.assertAlmostEqual(score, 0.30, delta=1e-6)
        self.assertEqual(missing, [])

    def test_behavior_no_signals_is_low_risk_neutral(self):
        score, tips, failed = self.scorer.score_behavior(None)
        self.assertAlmostEqual(score, 0.85, delta=1e-6)
        self.assertEqual(tips, [])
        self.assertEqual(failed, [])

    def test_behavior_rules_trigger_penalties(self):
        signals = {"claim_elapsed_minutes": 1, "claim_count_recent": 5,
                   "device_name_conflict": True, "identity_verified": False}
        score, tips, failed = self.scorer.score_behavior(signals)
        self.assertLess(score, 0.85)
        codes = {tip.code for tip in tips}
        self.assertIn("too_fast_claim", codes)
        self.assertIn("repeated_claims", codes)
        self.assertIn("device_name_mismatch", codes)
        self.assertEqual(failed, [])

    def test_behavior_malformed_signals_do_not_raise(self):
        for signals in ({"claim_elapsed_minutes": "abc"}, {"claim_count_recent": None},
                        {"claim_elapsed_minutes": []}, {"unknown_key": 1}):
            score, _tips, _failed = self.scorer.score_behavior(signals)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_behavior_penalty_lowers_total(self):
        risky = self.scorer.score(QUESTIONS, ANSWERS_ALL_RIGHT, {"total_score": 0.9}, TARGET_ITEM,
                                  {"claim_elapsed_minutes": 1, "claim_count_recent": 6,
                                   "device_name_conflict": True})
        normal = self.scorer.score(QUESTIONS, ANSWERS_ALL_RIGHT, {"total_score": 0.9}, TARGET_ITEM,
                                   {"claim_elapsed_minutes": 120, "claim_count_recent": 1})
        self.assertLess(risky.credibility_score, normal.credibility_score)


class TestRobustness(unittest.TestCase):
    """异常输入不得抛错。"""

    def setUp(self):
        self.scorer = CredibilityScorer()

    def test_malformed_payloads(self):
        cases = [
            (None, None, None, None, None),
            ("not-a-list", {"a": 1}, "nope", [], {"claim_count_recent": "x"}),
            ([{}], [{}], {}, {}, {}),
            ([{"id": "q"}], ["answer"], {"total_score": "abc"}, {"title": 42},
             {"identity_verified": "no"}),
        ]
        for questions, answers, candidate, item, behavior in cases:
            result = self.scorer.score(questions, answers, candidate, item, behavior)
            self.assertGreaterEqual(result.credibility_score, 0.0)
            self.assertLessEqual(result.credibility_score, 1.0)
            self.assertIn(result.risk_level, {"low", "medium", "high"})

    def test_answers_as_dicts(self):
        answers = [{"question_id": "q_scratch_detail", "answer": "右边有划痕"},
                   {"question_id": "q_bluetooth_name", "answer": "YiQi AirPods"}]
        score, per_question = self.scorer.score_answers(QUESTIONS[:2], answers)
        self.assertGreater(score, 0.5)
        self.assertEqual(per_question[1]["question_id"], "q_bluetooth_name")
        self.assertTrue(per_question[0]["matched"])

    def test_candidate_match_variants(self):
        for payload in ({"total_score": 0.8}, {"score": 0.6}, {"match_score": 0.4},
                        {"total_score": "0.7"}, {}, None, "bad"):
            value = self.scorer.score_candidate_match(payload)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
