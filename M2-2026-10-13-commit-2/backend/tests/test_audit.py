"""审计日志脱敏测试（`audit_service.sanitize_detail`）。

覆盖：

- 剔除 `hidden_features` / `answer` / `password` / `token` 等敏感键，
  并保留 `xxx_present: bool` 形式的「是否有值」标记；
- 超长字符串替换为 `{"length": n}`；
- 列表替换为 `[n items]`；
- 嵌套字典递归脱敏。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.services.audit_service import _MAX_DETAIL_STR_LEN, sanitize_detail


class TestSanitizeDetail(unittest.TestCase):
    """`sanitize_detail()` 行为。"""

    def test_none_and_empty(self) -> None:
        self.assertIsNone(sanitize_detail(None))
        self.assertIsNone(sanitize_detail({}))

    def test_forbidden_keys_are_replaced_by_presence_flag(self) -> None:
        detail = {
            "hidden_features": ["盒盖内侧有一道3厘米划痕"],
            "answer": "YiQi-AirPods",
            "answers": {"q1": "secret"},
            "answers_plain": {"q1": "secret"},
            "standard_answer": "secret",
            "password": "p@ssw0rd",
            "password_hash": "$2b$12$abcdef",
            "token": "eyJhbGciOi",
            "access_token": "eyJhbGciOi",
            "authorization": "Bearer abc",
            "secret": "s3cr3t",
            "api_key": "ak-123",
            "contact_id": "13812345678",
        }
        cleaned = sanitize_detail(detail)
        assert cleaned is not None

        # 原始键全部消失
        for key in detail:
            self.assertNotIn(key, cleaned, msg=f"敏感键 {key} 未被剔除")
        # 保留「是否存在」的布尔标记
        self.assertTrue(cleaned["hidden_features_present"])
        self.assertTrue(cleaned["answer_present"])
        self.assertTrue(cleaned["password_present"])
        self.assertTrue(cleaned["token_present"])
        self.assertTrue(cleaned["contact_id_present"])
        # 值绝不出现在结果里
        dumped = str(cleaned)
        for secret in ("盒盖内侧有一道3厘米划痕", "YiQi-AirPods", "p@ssw0rd", "Bearer abc", "13812345678"):
            self.assertNotIn(secret, dumped)

    def test_empty_forbidden_value_yields_false(self) -> None:
        cleaned = sanitize_detail({"answer": ""})
        assert cleaned is not None
        self.assertFalse(cleaned["answer_present"])

    def test_long_string_replaced_by_length(self) -> None:
        long_text = "长" * (_MAX_DETAIL_STR_LEN + 1)
        cleaned = sanitize_detail({"comment": long_text})
        assert cleaned is not None
        self.assertEqual(cleaned["comment"], {"length": len(long_text)})
        self.assertNotIn(long_text, str(cleaned))

    def test_short_string_kept(self) -> None:
        cleaned = sanitize_detail({"action": "approve", "comment_length": 18})
        assert cleaned is not None
        self.assertEqual(cleaned["action"], "approve")
        self.assertEqual(cleaned["comment_length"], 18)

    def test_list_replaced_by_item_count(self) -> None:
        cleaned = sanitize_detail({"changed_fields": ["title", "description", "location"]})
        assert cleaned is not None
        self.assertEqual(cleaned["changed_fields"], "[3 items]")

    def test_nested_dict_is_sanitized_recursively(self) -> None:
        detail = {
            "before": {"title": "旧标题", "hidden_features": ["秘密"]},
            "after": {"answer": "YiQi-AirPods", "status": "matched"},
        }
        cleaned = sanitize_detail(detail)
        assert cleaned is not None
        before = cleaned["before"]
        after = cleaned["after"]
        self.assertNotIn("hidden_features", before)
        self.assertTrue(before["hidden_features_present"])
        self.assertEqual(before["title"], "旧标题")
        self.assertNotIn("answer", after)
        self.assertEqual(after["status"], "matched")
        self.assertNotIn("秘密", str(cleaned))
        self.assertNotIn("YiQi-AirPods", str(cleaned))

    def test_key_match_is_case_insensitive(self) -> None:
        cleaned = sanitize_detail({"Answer": "secret", "TOKEN": "abc"})
        assert cleaned is not None
        self.assertNotIn("Answer", cleaned)
        self.assertNotIn("TOKEN", cleaned)

    def test_plain_values_are_preserved(self) -> None:
        detail = {
            "from": "pending_review",
            "to": "claimed",
            "request_id": "req_7f3a9c21b0d4",
            "degraded": False,
            "count": 0,
        }
        self.assertEqual(sanitize_detail(detail), detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
