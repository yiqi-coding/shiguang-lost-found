"""安全工具测试（`app/core/security.py`）。

覆盖：

- `hash_password` / `verify_password` 正确与错误路径；
- `hash_answer` 幂等 + `verify_answer` 对大小写/空格/全角差异的容忍
  （`YiQi-AirPods` vs `yiqi airpods` vs `ＹｉＱｉ－ＡｉｒＰｏｄｓ`）；
- `mask_contact` 对手机号 / 邮箱 / 身份证的脱敏；
- `generate_object_key` 不含原始文件名、格式为 `items/yyyy/mm/<hex>.ext`。

⚠ 本机若未安装 passlib/bcrypt，测试会使用 `tests/_stubs.py` 的 PBKDF2 替身
（语义一致：随机盐 + 常量时间比对），完整运行请先 `pip install -r requirements.txt`。
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timezone

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.security import (
    generate_object_key,
    hash_answer,
    hash_password,
    mask_contact,
    mask_ip,
    normalize_answer,
    safe_compare,
    verify_answer,
    verify_password,
)


class TestPasswordHashing(unittest.TestCase):
    """密码哈希（禁止 MD5/SHA1 裸哈希）。"""

    def test_hash_and_verify_ok(self) -> None:
        hashed = hash_password("Passw0rd!2026")
        self.assertTrue(hashed)
        self.assertNotIn("Passw0rd!2026", hashed)  # 不得明文回显
        self.assertTrue(verify_password("Passw0rd!2026", hashed))

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("correct-horse")
        self.assertFalse(verify_password("wrong-horse", hashed))
        self.assertFalse(verify_password("", hashed))

    def test_verify_with_none_or_garbage_hash(self) -> None:
        self.assertFalse(verify_password("whatever", None))
        self.assertFalse(verify_password("whatever", ""))
        self.assertFalse(verify_password("whatever", "not-a-valid-hash"))

    def test_same_password_produces_different_hash(self) -> None:
        """加盐：同一密码两次哈希结果不同。"""
        first = hash_password("same-password")
        second = hash_password("same-password")
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("same-password", first))
        self.assertTrue(verify_password("same-password", second))


class TestAnswerHashing(unittest.TestCase):
    """隐藏特征答案（加盐哈希 + 常量时间比对）。"""

    def test_hash_answer_is_deterministic(self) -> None:
        """同一输入幂等（同一密钥下哈希稳定）。"""
        first = hash_answer("盒盖内侧有一道约3厘米划痕")
        second = hash_answer("盒盖内侧有一道约3厘米划痕")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))

    def test_hash_answer_differs_for_different_input(self) -> None:
        self.assertNotEqual(hash_answer("答案一"), hash_answer("答案二"))

    def test_verify_answer_normalization_variants(self) -> None:
        """大小写 / 空格 / 连字符 / 全角差异必须能匹配上。"""
        stored = hash_answer("YiQi-AirPods")
        variants = [
            "YiQi-AirPods",
            "yiqi airpods",
            "YIQI-AIRPODS",
            "  yiqi   airpods  ",
            "ＹｉＱｉ－ＡｉｒＰｏｄｓ",  # 全角
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertTrue(verify_answer(variant, stored))

    def test_verify_answer_rejects_unrelated_input(self) -> None:
        stored = hash_answer("YiQi-AirPods")
        self.assertFalse(verify_answer("SomeoneElse-Buds", stored))
        self.assertFalse(verify_answer("", stored))
        self.assertFalse(verify_answer("YiQi-AirPods", None))

    def test_normalize_answer(self) -> None:
        self.assertEqual(normalize_answer("  YiQi-AirPods "), "yiqiairpods")
        self.assertEqual(normalize_answer("ＡＢＣ　１２３"), "abc123")
        self.assertEqual(normalize_answer(""), "")

    def test_safe_compare(self) -> None:
        self.assertTrue(safe_compare("abc", "abc"))
        self.assertFalse(safe_compare("abc", "abd"))


class TestMaskContact(unittest.TestCase):
    """联系方式脱敏。"""

    def test_phone_masked(self) -> None:
        self.assertEqual(mask_contact("13812345678"), "138****5678")
        self.assertEqual(mask_contact("联系我 13912345678 谢谢"), "联系我 139****5678 谢谢")

    def test_email_masked(self) -> None:
        masked = mask_contact("zhangsan@example.com")
        self.assertIsNotNone(masked)
        self.assertIn("***", masked)
        self.assertIn("@example.com", masked)
        self.assertNotIn("zhangsan", masked)

    def test_id_card_masked(self) -> None:
        masked = mask_contact("110101199003071234")
        self.assertIsNotNone(masked)
        self.assertIn("**********", masked)
        self.assertNotIn("19900307", masked)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(mask_contact(None))
        self.assertIsNone(mask_contact(""))

    def test_mask_ip(self) -> None:
        self.assertEqual(mask_ip("10.20.30.40"), "10.20.*.*")
        self.assertEqual(mask_ip(None), None)
        self.assertEqual(mask_ip("::1"), "***")


class TestObjectKey(unittest.TestCase):
    """对象 Key 生成规则（需求 §4.2.4：禁止使用原始文件名）。"""

    def test_key_format(self) -> None:
        key = generate_object_key("items", ".JPG", now=datetime(2026, 9, 28, tzinfo=timezone.utc))
        self.assertRegex(key, r"^items/2026/09/[0-9a-f]{32}\.jpg$")

    def test_key_contains_no_original_filename(self) -> None:
        key = generate_object_key("items", "png", now=datetime(2026, 1, 5, tzinfo=timezone.utc))
        for forbidden in ("照片", "IMG", "用户上传", "身份证", "..", "/../"):
            self.assertNotIn(forbidden, key)
        self.assertRegex(key, r"^items/2026/01/[0-9a-f]{32}\.png$")

    def test_key_is_unique(self) -> None:
        keys = {generate_object_key("items", "jpg") for _ in range(50)}
        self.assertEqual(len(keys), 50)

    def test_odd_extension_is_sanitized(self) -> None:
        key = generate_object_key("items", ".php/../../etc/passwd")
        self.assertNotIn("/", key.rsplit("/", 1)[-1])
        self.assertRegex(key, r"^items/\d{4}/\d{2}/[0-9a-f]{32}\.[a-z0-9]+$")

    def test_prefix_is_respected(self) -> None:
        key = generate_object_key("custom/prefix", "webp")
        self.assertTrue(key.startswith("custom/prefix/"))
        self.assertRegex(key, r"\.webp$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
