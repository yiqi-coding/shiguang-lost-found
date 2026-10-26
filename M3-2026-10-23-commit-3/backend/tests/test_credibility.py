"""可信度评分测试（契约 §3.9 冻结公式 + §2.6 风险阈值）。

    可信度分 = 0.60 × 隐藏特征答案匹配 + 0.20 × 候选物品综合匹配分
             + 0.10 × 发布信息完整度 + 0.10 × 行为/风险规则分

风险等级：`≥0.75 低` / `0.45 ~ 0.75 中` / `<0.45 高`（**以冻结的 API 契约 §2.6 为准**，
算法侧 `questions.yaml` 写的是 0.50，契约优先，见 `claim_service` 中的说明）。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.enums import ClaimStatus, RiskLevel
from app.services import claim_service
from app.services.claim_service import (
    CREDIBILITY_WEIGHTS,
    NEUTRAL_COMPLETENESS,
    RISK_LOW_MIN,
    RISK_MEDIUM_MIN,
    behavior_score,
    completeness_score,
    compute_credibility,
    risk_level_for,
)
from conftest import make_claim, make_item, make_match


class TestCredibilityWeights(unittest.TestCase):
    """权重冻结值。"""

    def test_weights_are_frozen(self) -> None:
        self.assertAlmostEqual(CREDIBILITY_WEIGHTS["answer_match"], 0.60)
        self.assertAlmostEqual(CREDIBILITY_WEIGHTS["candidate_match"], 0.20)
        self.assertAlmostEqual(CREDIBILITY_WEIGHTS["completeness"], 0.10)
        self.assertAlmostEqual(CREDIBILITY_WEIGHTS["behavior"], 0.10)

    def test_weights_sum_to_one(self) -> None:
        self.assertAlmostEqual(sum(CREDIBILITY_WEIGHTS.values()), 1.0, places=9)

    def test_weighted_total_matches_manual_calculation(self) -> None:
        """契约示例：0.86 / 0.88 / 完整度与行为分给定时的加权和。"""
        cases = [
            (0.86, 0.88, 0.80, 0.90),
            (0.0, 0.0, 0.0, 0.0),
            (1.0, 1.0, 1.0, 1.0),
            (0.5, 0.5, 0.5, 0.5),
        ]
        for answer, candidate, completeness, behavior in cases:
            expected = 0.60 * answer + 0.20 * candidate + 0.10 * completeness + 0.10 * behavior
            with self.subTest(expected=expected):
                self.assertAlmostEqual(
                    compute_credibility(
                        answer_match=answer,
                        candidate_match=candidate,
                        completeness=completeness,
                        behavior=behavior,
                    ),
                    expected,
                    delta=1e-6,
                )

    def test_out_of_range_inputs_are_clamped(self) -> None:
        self.assertEqual(
            compute_credibility(answer_match=5.0, candidate_match=5.0, completeness=5.0, behavior=5.0),
            1.0,
        )
        self.assertEqual(
            compute_credibility(answer_match=-1.0, candidate_match=-1.0, completeness=-1.0, behavior=-1.0),
            0.0,
        )

    def test_contract_example_score(self) -> None:
        """§3.9 示例里 verification=0.86 / candidate=0.88 ⇒ 典型区间应落在 low/medium 之间。"""
        score = compute_credibility(
            answer_match=0.86, candidate_match=0.88, completeness=0.55, behavior=1.0
        )
        self.assertAlmostEqual(score, 0.60 * 0.86 + 0.20 * 0.88 + 0.10 * 0.55 + 0.10 * 1.0, delta=1e-6)


class TestRiskLevel(unittest.TestCase):
    """风险等级阈值边界（契约 §2.6）。"""

    def test_thresholds_are_contract_values(self) -> None:
        self.assertAlmostEqual(RISK_LOW_MIN, 0.75)
        self.assertAlmostEqual(RISK_MEDIUM_MIN, 0.45)

    def test_boundaries(self) -> None:
        cases = [
            (1.0, RiskLevel.LOW),
            (0.75, RiskLevel.LOW),  # 边界：含
            (0.749999, RiskLevel.MEDIUM),
            (0.45, RiskLevel.MEDIUM),  # 边界：含
            (0.449999, RiskLevel.HIGH),
            (0.0, RiskLevel.HIGH),
        ]
        for score, expected in cases:
            with self.subTest(score=score):
                self.assertEqual(risk_level_for(score), expected)

    def test_high_risk_is_never_low(self) -> None:
        self.assertNotEqual(risk_level_for(0.3), RiskLevel.LOW)


class TestCompleteness(unittest.TestCase):
    """发布信息完整度（0~1）。"""

    def test_full_item_scores_one(self) -> None:
        item = make_item(
            images=[conftest_image()],
            public_features=["椭圆形充电盒"],
            color="黑色",
            brand="小米",
        )
        self.assertAlmostEqual(completeness_score(item), 1.0, delta=1e-6)

    def test_empty_item_is_low(self) -> None:
        item = make_item(
            title="",
            description="",
            category="",
            color=None,
            brand=None,
            location="",
            event_time=None,
            public_features=[],
            images=[],
        )
        score = completeness_score(item)
        self.assertGreaterEqual(score, 0.0)
        self.assertLess(score, 0.5)

    def test_partial_item_between_zero_and_one(self) -> None:
        item = make_item(images=[], color=None, brand=None)
        score = completeness_score(item)
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_short_description_not_counted(self) -> None:
        short = make_item(description="太短", images=[], public_features=[])
        full = make_item(description="这是一段足够长的描述内容用于判定充分性。", images=[], public_features=[])
        self.assertLess(completeness_score(short), completeness_score(full))


def conftest_image():
    """局部导入避免顶部噪声。"""
    from conftest import make_item_image

    return make_item_image()


class TestBehaviorScore(unittest.TestCase):
    """行为 / 风险规则分（从 1.0 起扣分）。"""

    def setUp(self) -> None:
        self.match = make_match(total_score=0.88)

    def test_clean_behaviour_scores_one(self) -> None:
        claim = make_claim(status=ClaimStatus.PENDING_REVIEW, attempts_used=0)
        score, tips = behavior_score(claim=claim, match=self.match, recent_claim_count=1, description_length=40)
        self.assertAlmostEqual(score, 1.0, delta=1e-6)
        self.assertEqual(tips, [])

    def test_too_fast_claim_penalised(self) -> None:
        claim = make_claim()
        claim.created_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 刚刚发起
        score, tips = behavior_score(claim=claim, match=self.match, recent_claim_count=1, description_length=40)
        self.assertAlmostEqual(score, 0.75, delta=1e-6)
        self.assertTrue(any("提交过快" in tip for tip in tips))

    def test_repeated_claims_penalised(self) -> None:
        claim = make_claim()
        score, tips = behavior_score(claim=claim, match=self.match, recent_claim_count=3, description_length=40)
        self.assertAlmostEqual(score, 0.80, delta=1e-6)
        self.assertTrue(any("多次认领" in tip for tip in tips))

    def test_many_failed_attempts_penalised(self) -> None:
        claim = make_claim(attempts_used=3)
        score, _tips = behavior_score(claim=claim, match=self.match, recent_claim_count=1, description_length=40)
        self.assertAlmostEqual(score, 0.80, delta=1e-6)

    def test_vague_description_penalised(self) -> None:
        claim = make_claim()
        score, tips = behavior_score(claim=claim, match=self.match, recent_claim_count=1, description_length=5)
        self.assertAlmostEqual(score, 0.90, delta=1e-6)
        self.assertTrue(any("模糊" in tip for tip in tips))

    def test_low_candidate_score_adds_tip(self) -> None:
        claim = make_claim()
        weak_match = make_match(total_score=0.10)
        _score, tips = behavior_score(
            claim=claim, match=weak_match, recent_claim_count=1, description_length=40
        )
        self.assertTrue(any("匹配分偏低" in tip for tip in tips))

    def test_score_never_negative(self) -> None:
        claim = make_claim(attempts_used=9)
        claim.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        score, _tips = behavior_score(claim=claim, match=self.match, recent_claim_count=9, description_length=3)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class TestNeutralScores(unittest.TestCase):
    """缺数据时的中性分（绝不抛异常）。"""

    def test_neutral_constants(self) -> None:
        self.assertAlmostEqual(claim_service.NEUTRAL_ANSWER_MATCH, 0.50)
        self.assertAlmostEqual(claim_service.NEUTRAL_CANDIDATE_MATCH, 0.50)
        self.assertAlmostEqual(NEUTRAL_COMPLETENESS, 0.30)
        self.assertAlmostEqual(claim_service.NEUTRAL_BEHAVIOR, 0.60)

    def test_completeness_never_raises_on_empty_item(self) -> None:
        item = make_item(title="", description="", public_features=[], images=[])
        self.assertGreaterEqual(completeness_score(item), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
