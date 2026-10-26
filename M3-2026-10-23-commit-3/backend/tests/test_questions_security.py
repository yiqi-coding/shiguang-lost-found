"""验证问题安全校验测试（契约 §3.8 红线 2）。

`claim_service.validate_questions()` 必须识别并拒绝：

- 泄露答案原文的问题（含答案的**连续 4 字以上**子串）；
- 「是不是 / 有没有」这类是非问；
- 重复问题（文本重复 / question_id 重复）；
- 空问题或过短问题；
- 含敏感词（身份证、学号、密码、验证码……）的问题。

合法问题必须通过；模板回退路径也必须只产出安全题目。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.services.claim_service import (
    MIN_ANSWER_FRAGMENT_LENGTH,
    generate_questions,
    question_leaks_answer,
    validate_questions,
)
from conftest import make_item


def _questions(*texts: str) -> list[dict[str, str]]:
    return [{"question_id": f"q{index + 1}", "question_text": text} for index, text in enumerate(texts)]


class TestAnswerLeakDetection(unittest.TestCase):
    """题目不得复述答案。"""

    def setUp(self) -> None:
        self.item = make_item(
            hidden_features=["盒盖内侧有一道约3厘米划痕", "蓝牙名称 YiQi-AirPods"]
        )

    def test_question_containing_full_answer_is_rejected(self) -> None:
        questions = _questions("请问盒盖内侧有一道约3厘米划痕 是吗？")
        result = validate_questions(questions, self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("泄露" in problem for problem in result.problems))

    def test_question_containing_four_char_fragment_is_rejected(self) -> None:
        """包含答案的连续 4 字子串同样视为泄露。"""
        fragment = "盒盖内侧有一道"[:MIN_ANSWER_FRAGMENT_LENGTH]
        questions = _questions(f"请描述物品外观。{fragment}的情况请说明。")
        result = validate_questions(questions, self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("泄露" in problem for problem in result.problems))

    def test_question_leaks_answer_helper_directly(self) -> None:
        self.assertTrue(question_leaks_answer("请描述蓝牙名称 YiQi-AirPods 是什么", "蓝牙名称 YiQi-AirPods"))
        self.assertTrue(question_leaks_answer("请问盒盖内侧的情况", "盒盖内侧有一道约3厘米划痕"))
        self.assertFalse(question_leaks_answer("请描述充电盒上的特殊痕迹（位置、形状或长度）。", "盒盖内侧有一道约3厘米划痕"))
        self.assertFalse(question_leaks_answer("", "任意答案"))
        self.assertFalse(question_leaks_answer("任意问题", ""))

    def test_short_answer_requires_full_presence(self) -> None:
        """答案短于 4 字时，只有整体出现才算泄露（避免误杀）。"""
        self.assertTrue(question_leaks_answer("请问是不是蓝色的？", "蓝色"))
        self.assertFalse(question_leaks_answer("请描述物品的颜色。", "蓝色"))


class TestYesNoQuestions(unittest.TestCase):
    """是非问必须被拒绝（可猜测、无验证价值）。"""

    def setUp(self) -> None:
        self.item = make_item()

    def test_yes_no_patterns_rejected(self) -> None:
        cases = [
            "这个耳机是不是黑色的？",
            "充电盒有没有划痕？",
            "该物品是否存在配件缺失的情况？",
            "物品上有贴纸，对吗？",
            "盒子能充电吗？",
            "这样回答好不好？",
        ]
        for text in cases:
            with self.subTest(question=text):
                result = validate_questions(_questions(text), self.item)
                self.assertFalse(result.ok, msg=f"应拒绝是非问：{text}")
                self.assertTrue(any("是非问" in problem for problem in result.problems))

    def test_open_questions_pass(self) -> None:
        result = validate_questions(
            _questions(
                "请描述充电盒上的特殊痕迹（位置、形状或长度）。",
                "请填写该设备设置的蓝牙名称。",
            ),
            self.item,
        )
        self.assertTrue(result.ok, msg=f"合法问题不应被拒绝：{result.problems}")


class TestOtherRejections(unittest.TestCase):
    """空问题 / 重复问题 / 敏感词。"""

    def setUp(self) -> None:
        self.item = make_item()

    def test_empty_question_rejected(self) -> None:
        result = validate_questions(_questions("", "   "), self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("为空" in problem or "过短" in problem for problem in result.problems))

    def test_empty_list_rejected(self) -> None:
        result = validate_questions([], self.item)
        self.assertFalse(result.ok)
        self.assertIn("未生成任何问题", result.problems)

    def test_duplicate_question_text_rejected(self) -> None:
        text = "请描述物品上的特殊痕迹位置。"
        result = validate_questions(_questions(text, text), self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("重复" in problem for problem in result.problems))

    def test_duplicate_question_id_rejected(self) -> None:
        questions = [
            {"question_id": "q1", "question_text": "请描述物品上的特殊痕迹位置。"},
            {"question_id": "q1", "question_text": "请说明物品的磨损与污渍情况。"},
        ]
        result = validate_questions(questions, self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("question_id 重复" in problem for problem in result.problems))

    def test_sensitive_words_rejected(self) -> None:
        cases = [
            "请提供您的身份证号码后六位。",
            "请填写学号以及宿舍号。",
            "请输入该账号的密码。",
            "请提供收到的验证码。",
        ]
        for text in cases:
            with self.subTest(question=text):
                result = validate_questions(_questions(text), self.item)
                self.assertFalse(result.ok, msg=f"应拒绝敏感词问题：{text}")
                self.assertTrue(any("敏感词" in problem for problem in result.problems))

    def test_too_long_question_rejected(self) -> None:
        result = validate_questions(_questions("请描述" + "很" * 300 + "多细节"), self.item)
        self.assertFalse(result.ok)
        self.assertTrue(any("过长" in problem for problem in result.problems))

    def test_problems_never_echo_hidden_features(self) -> None:
        """校验失败信息本身也不得回显隐藏特征原文（避免日志泄露）。"""
        item = make_item(hidden_features=["绝密特征ABC123"])
        result = validate_questions(_questions("请问绝密特征ABC123 是什么？"), item)
        self.assertFalse(result.ok)
        joined = " ".join(result.problems)
        self.assertNotIn("绝密特征ABC123", joined)


class TestQuestionGeneration(unittest.TestCase):
    """生成链路：MaaS/模板 → 安全校验 → 兜底。"""

    def setUp(self) -> None:
        self.item = make_item(
            category="无线耳机",
            hidden_features=["盒盖内侧有一道约3厘米划痕", "蓝牙名称 YiQi-AirPods"],
        )

    def test_generated_questions_are_safe(self) -> None:
        """无论走 MaaS 还是模板回退，产出必须通过安全校验且不含标准答案。"""
        drafts = generate_questions(None, item=self.item, count=3)  # type: ignore[arg-type]
        self.assertGreaterEqual(len(drafts), 2)
        self.assertLessEqual(len(drafts), 3)

        payload = [draft.to_dict() for draft in drafts]
        result = validate_questions(payload, self.item)
        self.assertTrue(result.ok, msg=f"生成的问题未通过安全校验：{result.problems}")

        for draft in drafts:
            self.assertNotIn(draft.standard_answer, draft.question_text)
            self.assertNotIn("standard_answer", draft.to_dict())  # 答案绝不进入入库结构

    def test_draft_to_dict_contains_no_answer(self) -> None:
        drafts = generate_questions(None, item=self.item, count=2)  # type: ignore[arg-type]
        for draft in drafts:
            keys = set(draft.to_dict().keys())
            self.assertEqual(keys, {"question_id", "question_text", "source", "feature_ref", "weight"})

    def test_question_ids_are_normalized(self) -> None:
        drafts = generate_questions(None, item=self.item, count=3)  # type: ignore[arg-type]
        self.assertEqual([draft.question_id for draft in drafts], ["q1", "q2", "q3"])

    def test_template_count_clamped_to_contract_range(self) -> None:
        """契约：问题数量 2~3 个。"""
        self.assertLessEqual(len(generate_questions(None, item=self.item, count=99)), 3)  # type: ignore[arg-type]
        self.assertGreaterEqual(len(generate_questions(None, item=self.item, count=1)), 2)  # type: ignore[arg-type]


class TestAnswerScoring(unittest.TestCase):
    """答案比对：`reason` 不回显答案原文（契约 §3.9 红线 1）。"""

    def setUp(self) -> None:
        from app.core.security import hash_answer

        self.standard = "YiQi-AirPods"
        self.question = {"question_id": "q1", "question_text": "请填写该设备设置的蓝牙名称。", "type": "field"}
        self.stored_hash = hash_answer(self.standard)

    def test_exact_variant_matches(self) -> None:
        from app.services.claim_service import score_answer

        score = score_answer(
            question=self.question,
            user_answer="yiqi airpods",
            standard_answer=self.standard,
            stored_hash=self.stored_hash,
        )
        self.assertTrue(score.matched)
        self.assertAlmostEqual(score.score, 1.0, delta=1e-6)
        self.assertNotIn(self.standard, score.reason)

    def test_unanswered_is_zero(self) -> None:
        from app.services.claim_service import score_answer

        score = score_answer(
            question=self.question, user_answer="", standard_answer=self.standard, stored_hash=self.stored_hash
        )
        self.assertFalse(score.matched)
        self.assertEqual(score.score, 0.0)
        self.assertIn("未作答", score.reason)

    def test_unrelated_answer_scores_zero_without_echo(self) -> None:
        from app.services.claim_service import score_answer

        score = score_answer(
            question=self.question,
            user_answer="完全不知道",
            standard_answer=self.standard,
            stored_hash=self.stored_hash,
        )
        self.assertFalse(score.matched)
        self.assertNotIn(self.standard, score.reason)

    def test_reason_never_contains_answer_for_all_cases(self) -> None:
        from app.services.claim_service import score_answer

        for answer in ("YiQi-AirPods", "yiqi-airpods-pro", "完全不知道", "耳机", "不知道"):
            with self.subTest(answer=answer):
                score = score_answer(
                    question=self.question,
                    user_answer=answer,
                    standard_answer=self.standard,
                    stored_hash=self.stored_hash,
                )
                self.assertNotIn(self.standard, score.reason)
                self.assertLessEqual(len(score.reason), 60)
                self.assertGreaterEqual(score.score, 0.0)
                self.assertLessEqual(score.score, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
