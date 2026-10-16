"""序列化层安全红线测试（**最重要的一组测试**）。

用**纯内存对象**调用 `serializers.serialize_item()` / `serialize_claim_for_applicant()`，
断言：

- 游客与非发布者普通用户的响应中**不含** `hidden_features` / `contact_id` /
  `owner_user_id` / `image_keys` / `password_hash`；
- 发布者本人视角**含** `hidden_features`；
- 管理员视角含 `hidden_features` 与 `owner`；
- 认领响应不含 `answers_hash` / `answers_plain` / `standard_answer`；
- `assert_no_sensitive_leak()` / `assert_no_answer_leak()` 自检通过。

⚠ 测试全程不连接数据库：`Item.images` 直接赋值，不触发关系懒加载。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.core.enums import ClaimStatus, ItemStatus, PostType, UserRole
from app.services import serializers
from app.services.serializers import (
    FORBIDDEN_KEYS_FOR_APPLICANT,
    FORBIDDEN_KEYS_FOR_PUBLIC,
    assert_no_answer_leak,
    assert_no_sensitive_leak,
    serialize_claim_for_admin,
    serialize_claim_for_applicant,
    serialize_item,
    serialize_item_for_admin,
    serialize_match,
)
from conftest import make_admin, make_claim, make_item, make_item_image, make_match, make_user, make_verified_claim


class TestItemPermissionMatrix(unittest.TestCase):
    """契约 §3.5 字段级权限矩阵。"""

    def setUp(self) -> None:
        self.owner = make_user(user_id=12, username="demo_student")
        self.other = make_user(user_id=77, username="someone_else")
        self.admin = make_admin()
        self.item = make_item(item_id=1001, owner=self.owner)

    def test_guest_view_has_no_sensitive_fields(self) -> None:
        payload = serialize_item(self.item, viewer=None, detail=True)
        for key in ("hidden_features", "contact_id", "owner_user_id", "image_keys", "password_hash"):
            self.assertNotIn(key, payload, msg=f"游客视角不应包含 {key}")
        assert_no_sensitive_leak(payload)
        # 公开字段仍然可用
        self.assertEqual(payload["id"], 1001)
        self.assertEqual(payload["title"], self.item.title)
        self.assertIn("status_text", payload)

    def test_other_user_view_has_no_sensitive_fields(self) -> None:
        payload = serialize_item(self.item, viewer=self.other, detail=True)
        self.assertNotIn("hidden_features", payload)
        self.assertNotIn("owner_user_id", payload)
        assert_no_sensitive_leak(payload)

    def test_list_view_never_leaks_either(self) -> None:
        for viewer in (None, self.other):
            with self.subTest(viewer=getattr(viewer, "username", "guest")):
                payload = serialize_item(self.item, viewer=viewer, detail=False)
                assert_no_sensitive_leak(payload)
                # 列表视图用缩略图替代图片列表，且不暴露 image_keys
                self.assertIn("thumbnail_url", payload)
                self.assertNotIn("images", payload)
                self.assertNotIn("image_keys", payload)

    def test_owner_view_has_hidden_features(self) -> None:
        payload = serialize_item(self.item, viewer=self.owner, detail=True)
        self.assertEqual(payload["hidden_features"], self.item.hidden_features)
        self.assertEqual(payload["owner_user_id"], self.owner.id)
        self.assertIn("contact_id", payload)
        # 本人视角允许出现敏感字段，但密码哈希永远不出现
        self.assertNotIn("password_hash", payload)

    def test_admin_view_has_hidden_features_and_owner(self) -> None:
        payload = serialize_item(self.item, viewer=self.admin, detail=True)
        self.assertEqual(payload["hidden_features"], self.item.hidden_features)
        self.assertIn("owner", payload)
        self.assertEqual(payload["owner"]["username"], self.owner.username)
        self.assertIn("updated_at", payload)
        self.assertIn("allowed_next_statuses", payload)
        self.assertNotIn("password_hash", payload)
        self.assertNotIn("password_hash", payload.get("owner") or {})

    def test_admin_serializer_includes_full_private_fields(self) -> None:
        payload = serialize_item_for_admin(self.item)
        for key in ("hidden_features", "contact_id", "owner_user_id", "image_keys", "ai_attributes"):
            self.assertIn(key, payload)
        self.assertNotIn("password_hash", payload)
        self.assertNotIn("password_hash", payload.get("owner") or {})

    def test_ai_attributes_are_trimmed_for_guests(self) -> None:
        payload = serialize_item(self.item, viewer=None, detail=True)
        self.assertIsNotNone(payload["ai_attributes"])
        self.assertEqual(
            set(payload["ai_attributes"].keys()), {"visible_features", "keywords", "uncertainties"}
        )
        # 游客只看公开部分，不得看到 model/material 等内部推断字段
        self.assertNotIn("brand", payload["ai_attributes"])

    def test_image_keys_hidden_but_urls_available(self) -> None:
        item = make_item(item_id=1002, owner=self.owner, images=[make_item_image()])
        guest_payload = serialize_item(item, viewer=None, detail=True)
        self.assertNotIn("image_keys", guest_payload)
        owner_payload = serialize_item(item, viewer=self.owner, detail=True)
        self.assertIn("image_keys", owner_payload)
        self.assertEqual(owner_payload["image_keys"][0]["key"], "items/2026/09/abc123.jpg")


class TestMatchSerialization(unittest.TestCase):
    """匹配结果序列化（六维分数 + 等级 + 降级标记）。"""

    def setUp(self) -> None:
        self.owner = make_user(user_id=12)
        self.match = make_match(
            lost_item=make_item(item_id=1001, owner=self.owner),
            found_item=make_item(item_id=2001, post_type=PostType.FOUND),
            total_score=0.88,
        )

    def test_match_payload_fields(self) -> None:
        payload = serialize_match(self.match)
        self.assertEqual(payload["match_id"], 3001)
        self.assertAlmostEqual(payload["total_score"], 0.88, delta=1e-6)
        self.assertEqual(
            set(payload["score_detail"].keys()),
            {
                "category_score",
                "attribute_score",
                "image_score",
                "time_score",
                "location_score",
                "brand_model_score",
            },
        )
        self.assertEqual(payload["match_level"], "high")
        self.assertFalse(payload["degraded"])

    def test_score_detail_total_consistency(self) -> None:
        """`total_score` 必须等于六维加权和（容差 1e-6）。

        同时验证「模型侧 `Match.recompute_total()`」与「服务侧 `scoring.weighted_total()`」
        两条实现路径给出同一个数（避免两处公式漂移）。
        """
        from app.services.scoring import get_weights, weighted_total

        payload = serialize_match(self.match)
        expected = weighted_total(payload["score_detail"])
        self.assertAlmostEqual(float(self.match.recompute_total(get_weights())), expected, delta=1e-6)


class TestClaimSerialization(unittest.TestCase):
    """认领序列化：绝不泄露标准答案。"""

    def setUp(self) -> None:
        self.applicant = make_user(user_id=13, username="demo_finder")
        self.claim = make_claim(applicant=self.applicant)

    def test_applicant_payload_has_no_answer_fields(self) -> None:
        payload = serialize_claim_for_applicant(self.claim)
        assert_no_answer_leak(payload)
        for key in ("answers_hash", "answers_plain", "standard_answer", "answer_summary"):
            self.assertNotIn(key, payload)
        # questions 只含 question_id + question_text
        for question in payload["questions"]:
            self.assertEqual(set(question.keys()), {"question_id", "question_text"})

    def test_unverified_claim_hides_scores(self) -> None:
        """未提交验证：分数与风险字段不出现（而非 null）。"""
        payload = serialize_claim_for_applicant(self.claim)
        for key in ("verification_score", "credibility_score", "risk_level", "per_question"):
            self.assertNotIn(key, payload)

    def test_verified_claim_exposes_scores_but_not_answers(self) -> None:
        claim = make_verified_claim(applicant=self.applicant)
        payload = serialize_claim_for_applicant(claim)
        self.assertIn("verification_score", payload)
        self.assertIn("credibility_score", payload)
        self.assertIn("risk_level", payload)
        self.assertIn("per_question", payload)
        assert_no_answer_leak(payload)
        # per_question 的结构：是否命中 + 得分 + 理由，不含答案
        for entry in payload["per_question"]:
            self.assertEqual(set(entry.keys()), {"question_id", "matched", "score", "reason"})

    def test_admin_claim_payload_has_questions_with_text_but_no_answers(self) -> None:
        claim = make_verified_claim(applicant=self.applicant)
        payload = serialize_claim_for_admin(claim)
        assert_no_answer_leak(payload)
        self.assertIn("questions", payload)
        self.assertIn("applicant", payload)
        for entry in payload["per_question"]:
            self.assertIn("question_text", entry)
            self.assertIn("reason", entry)
            self.assertNotIn("answer", entry)

    def test_reason_never_echoes_standard_answer(self) -> None:
        """`reason` 文案必须是模糊理由，不得包含隐藏特征原文。"""
        claim = make_verified_claim(applicant=self.applicant)
        payload = serialize_claim_for_applicant(claim)
        reasons = " ".join(str(entry.get("reason", "")) for entry in payload.get("per_question", []))
        for secret in claim.match.lost_item.hidden_features:
            self.assertNotIn(str(secret), reasons)

    def test_forbidden_key_sets_are_stable(self) -> None:
        """红名单本身是契约的一部分，被误改会导致上游测试失效，这里显式锁定。"""
        self.assertEqual(
            FORBIDDEN_KEYS_FOR_PUBLIC,
            {"hidden_features", "contact_id", "owner_user_id", "image_keys", "password_hash"},
        )
        self.assertEqual(
            FORBIDDEN_KEYS_FOR_APPLICANT,
            {"answers_hash", "answers_plain", "standard_answer", "standard_answers"},
        )

    def test_assert_helpers_raise_on_leak(self) -> None:
        """自检工具必须真的能拦住泄露。"""
        with self.assertRaises(AssertionError):
            assert_no_sensitive_leak({"hidden_features": ["x"]})
        with self.assertRaises(AssertionError):
            assert_no_answer_leak({"answers_hash": {"q1": "sha256:x"}})
        # allow_private=True 时允许（管理员/本人视角）
        assert_no_sensitive_leak({"hidden_features": ["x"]}, allow_private=True)

    def test_serialize_item_never_contains_user_password(self) -> None:
        """物品响应中不得出现关联用户的密码哈希（含 owner 子对象）。"""
        item = make_item(item_id=1003, owner=make_user(user_id=55, password_hash="top-secret-hash"))
        admin_payload = serialize_item_for_admin(item)
        self.assertNotIn("password_hash", str(admin_payload))


class TestStatusTexts(unittest.TestCase):
    """状态中文文案（前端 StatusTag 直接使用）。"""

    def test_item_status_text(self) -> None:
        item = make_item(status=ItemStatus.CLAIMING)
        payload = serialize_item(item, viewer=None)
        self.assertEqual(payload["status_text"], "认领中")

    def test_claim_status_text(self) -> None:
        claim = make_claim(status=ClaimStatus.PENDING_REVIEW)
        self.assertEqual(claim.status, "pending_review")
        payload = serialize_claim_for_applicant(claim)
        self.assertIn(payload["status"], {status.value for status in ClaimStatus})

    def test_post_type_text(self) -> None:
        item = make_item(post_type=PostType.FOUND)
        payload = serialize_item(item, viewer=None)
        self.assertEqual(payload["post_type_text"], "拾物")


if __name__ == "__main__":
    unittest.main(verbosity=2)
