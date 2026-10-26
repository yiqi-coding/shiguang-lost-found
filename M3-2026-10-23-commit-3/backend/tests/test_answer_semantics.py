"""M3 · 答案语义判定回归测试（同义容忍 + 否定拦截）。

针对 `app.services.claim_service.score_answer` 的语义判定能力。
这两项是需求 §4.5.4 的明确要求，且互为张力：

- **同义容忍**：真实失主用不同措辞回答（「盒子右边刮了一道」）必须能通过，
  否则就是误拒绝 —— 对业务是致命的；
- **否定拦截**：冒领者用「没有任何划痕」这类**否认式**回答不能通过，
  否则相似度会把共享的名词（「划痕」）当作命中。

本文件同时锁住这两条，避免以后为了一边而破坏另一边。

运行：
    cd backend && python -m unittest discover -s tests -k test_answer_semantics -v
"""

from __future__ import annotations

import unittest

import conftest


def _score(user_answer: str, standard: str):
    from app.core.security import hash_answer
    from app.services.claim_service import score_answer

    return score_answer(
        question={"question_id": "q1", "question_text": "请描述充电盒上的特殊痕迹。"},
        user_answer=user_answer,
        standard_answer=standard,
        stored_hash=hash_answer(standard),
    )


class TestSynonymTolerance(unittest.TestCase):
    """同义表达必须能命中（避免误拒绝真实失主）。"""

    def test_paraphrase_scratch(self):
        result = _score("盒子右边刮了一道", "盒体右侧有一道划痕")
        self.assertTrue(result.matched, f"同义改写未命中：{result}")
        self.assertGreaterEqual(result.score, 0.5)

    def test_synonym_scrape_vs_scratch(self):
        result = _score("边上有刮痕", "边上有划痕")
        self.assertTrue(result.matched, f"刮痕/划痕 未归一：{result}")

    def test_direction_synonyms(self):
        for user, standard in (
            ("左边有磨损", "左侧有磨损"),
            ("上面有个贴纸", "上方有个贴纸"),
            ("里面脏了", "内侧脏了"),
        ):
            with self.subTest(user=user):
                result = _score(user, standard)
                self.assertTrue(result.matched, f"{user} 未命中 {standard}：{result}")

    def test_unit_synonyms(self):
        result = _score("大概 3cm 长", "大约3厘米长")
        self.assertTrue(result.matched, f"cm/厘米 未归一：{result}")

    def test_case_and_spacing_still_match(self):
        result = _score("yiqi airpods", "YiQi-AirPods")
        self.assertTrue(result.matched, f"大小写/空格差异未容忍：{result}")

    def test_fullwidth_still_match(self):
        result = _score("ＹｉＱｉ－ＡｉｒＰｏｄｓ", "YiQi-AirPods")
        self.assertTrue(result.matched, f"全角差异未容忍：{result}")


class TestNegationRejection(unittest.TestCase):
    """否认式回答必须判为不匹配（防止冒领者蒙混）。"""

    def test_explicit_denial_not_matched(self):
        result = _score("没有任何划痕，很干净", "盒盖内侧有一道3厘米划痕")
        self.assertFalse(result.matched, f"否认回答被误判为命中：{result}")
        self.assertEqual(result.score, 0.0)

    def test_denial_keeps_shared_noun_from_matching(self):
        """「没有划痕」与标准答案共享「划痕」，但语义相反，不能命中。"""
        result = _score("没有划痕", "有一道划痕")
        self.assertFalse(result.matched, f"共享名词导致误判：{result}")

    def test_intact_statement_not_matched(self):
        result = _score("完好无损，没有磨损", "左侧有明显磨损")
        self.assertFalse(result.matched, f"「完好无损」被误判为命中：{result}")

    def test_denial_with_wrong_detail_not_matched(self):
        result = _score("不是黑色的，是白色的", "黑色")
        self.assertFalse(result.matched, f"否认颜色被误判：{result}")


class TestEdgeCases(unittest.TestCase):
    """边界与安全约束。"""

    def test_empty_answer(self):
        result = _score("", "有一道划痕")
        self.assertFalse(result.matched)
        self.assertEqual(result.score, 0.0)

    def test_too_short_answer(self):
        result = _score("嗯", "有一道划痕")
        self.assertFalse(result.matched)

    def test_reason_never_leaks_standard_answer(self):
        """理由文本不得回显标准答案原文（契约 §3.9 红线 1）。"""
        standard = "盒盖内侧有一道3厘米划痕"
        for user in ("盒子右边刮了一道", "没有任何划痕", "不知道"):
            with self.subTest(user=user):
                result = _score(user, standard)
                self.assertNotIn(standard, result.reason)
                self.assertNotIn("3厘米", result.reason)

    def test_normalize_synonyms_is_idempotent(self):
        """归一化必须幂等（重复应用结果不变，保证可复现）。"""
        from app.services.claim_service import normalize_synonyms

        once = normalize_synonyms("盒子右边刮了一道")
        twice = normalize_synonyms(once)
        self.assertEqual(once, twice)

    def test_synonym_groups_have_no_self_alias(self):
        """同义词表自检：别名不应等于标准词（否则是无意义条目）。"""
        from app.services.claim_service import SYNONYM_GROUPS

        for canonical, aliases in SYNONYM_GROUPS.items():
            for alias in aliases:
                with self.subTest(canonical=canonical, alias=alias):
                    self.assertNotEqual(alias, canonical, f"{canonical} 的别名表包含自身")


if __name__ == "__main__":
    unittest.main(verbosity=2)
