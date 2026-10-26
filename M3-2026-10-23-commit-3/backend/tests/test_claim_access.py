"""认领详情 / 管理员待办列表的权限与结构测试（契约 §3.10.1 / §3.10.2）。

重点覆盖**异常用例（403）**：

- `GET /api/v1/claims/{claim_id}`：申请人本人或管理员；他人 → `403 FORBIDDEN`；
- `GET /api/v1/admin/claims`：非管理员 → `403 FORBIDDEN`（`require_admin` 依赖）。

同时校验两个新接口的响应结构红线：

- §3.10.1 未提交验证前 `verification_score` / `credibility_score` / `risk_level` **不出现**；
- 两者都绝不含标准答案 / `answers_hash` / `answers_plain` / 隐藏特征。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.api.deps import require_admin
from app.core.enums import ClaimStatus, RiskLevel
from app.core.errors import ErrorCode, ForbiddenError
from app.schemas.admin import AdminClaimQuery
from app.services import admin_service, claim_service
from app.services.serializers import assert_no_answer_leak, assert_no_sensitive_leak
from conftest import make_admin, make_claim, make_lost_and_found, make_user, make_verified_claim


class TestClaimDetailPermission(unittest.TestCase):
    """`GET /api/v1/claims/{claim_id}` 权限（契约 §3.10.1）。"""

    def setUp(self) -> None:
        self.lost, self.found, self.match = make_lost_and_found()
        self.applicant = self.found.owner
        self.claim = make_claim(match=self.match, applicant=self.applicant)
        self.stranger = make_user(user_id=88, username="stranger")
        self.admin = make_admin()

    def test_applicant_can_read_own_claim(self) -> None:
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.applicant)
        self.assertEqual(data["claim_id"], self.claim.id)
        self.assertEqual(data["match_id"], self.match.id)

    def test_admin_can_read_any_claim(self) -> None:
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.admin)
        self.assertEqual(data["claim_id"], self.claim.id)

    def test_other_user_gets_403(self) -> None:
        """他人访问 → 403 FORBIDDEN（错误码与契约 §1.5 一致）。"""
        with self.assertRaises(ForbiddenError) as ctx:
            claim_service.claim_detail_for_applicant(self.claim, viewer=self.stranger)
        self.assertEqual(ctx.exception.http_status, 403)
        self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)

    def test_guest_gets_403(self) -> None:
        """未登录（viewer 为游客占位对象）同样不得读取。"""
        guest = make_user(user_id=0, username="guest", contact_id=None)
        with self.assertRaises(ForbiddenError):
            claim_service.claim_detail_for_applicant(self.claim, viewer=guest)


class TestClaimDetailStructure(unittest.TestCase):
    """`GET /api/v1/claims/{claim_id}` 响应结构（契约 §3.10.1）。"""

    def setUp(self) -> None:
        self.lost, self.found, self.match = make_lost_and_found()
        self.applicant = self.found.owner
        self.claim = make_claim(match=self.match, applicant=self.applicant, status=ClaimStatus.CLAIMING)

    def test_required_fields_present(self) -> None:
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.applicant)
        for key in (
            "claim_id",
            "match_id",
            "status",
            "status_text",
            "questions",
            "attempts_used",
            "max_attempts",
            "attempts_left",
            "created_at",
            "match_summary",
            "can_submit",
            "disclaimer",
        ):
            self.assertIn(key, data, msg=f"缺少契约字段 {key}")
        self.assertEqual(data["status_text"], "认领中")
        self.assertTrue(data["can_submit"])
        self.assertIn("辅助审核依据", data["disclaimer"])

    def test_questions_only_contain_id_and_text(self) -> None:
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.applicant)
        self.assertTrue(data["questions"])
        for question in data["questions"]:
            self.assertEqual(set(question.keys()), {"question_id", "question_text"})

    def test_unverified_claim_hides_scores_entirely(self) -> None:
        """红线 2：未提交验证前分数与风险字段**不出现**（而不是 null）。"""
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.applicant)
        for key in ("verification_score", "credibility_score", "risk_level", "per_question"):
            self.assertNotIn(key, data, msg=f"{key} 不应出现在未验证的认领详情中")

    def test_verified_claim_returns_scores_and_per_question(self) -> None:
        claim = make_verified_claim(match=self.match, applicant=self.applicant)
        data = claim_service.claim_detail_for_applicant(claim, viewer=self.applicant)
        self.assertAlmostEqual(data["verification_score"], 0.86, delta=1e-6)
        self.assertAlmostEqual(data["credibility_score"], 0.79, delta=1e-6)
        self.assertEqual(data["risk_level"], RiskLevel.LOW)
        self.assertTrue(data["per_question"])
        for entry in data["per_question"]:
            self.assertEqual(set(entry.keys()), {"question_id", "matched", "score", "reason"})

    def test_match_summary_fields(self) -> None:
        data = claim_service.claim_detail_for_applicant(self.claim, viewer=self.applicant)
        summary = data["match_summary"]
        self.assertEqual(set(summary.keys()), {"item_title", "post_type", "total_score", "match_level"})
        # 契约 §3.10.1 示例：摘要取自申请人所属的 found 记录
        self.assertEqual(summary["item_title"], self.found.title)
        self.assertEqual(summary["post_type"], self.found.post_type)
        self.assertAlmostEqual(summary["total_score"], 0.88, delta=1e-6)
        self.assertEqual(summary["match_level"], "high")

    def test_no_answer_or_hidden_feature_leak(self) -> None:
        """认领详情绝不含答案线索，也不含他人隐藏特征。"""
        claim = make_verified_claim(match=self.match, applicant=self.applicant)
        data = claim_service.claim_detail_for_applicant(claim, viewer=self.applicant)
        assert_no_answer_leak(data)
        dumped = str(data)
        for secret in self.lost.hidden_features + self.found.hidden_features:
            self.assertNotIn(str(secret), dumped)
        self.assertNotIn("answers_hash", dumped)

    def test_can_submit_false_when_exhausted(self) -> None:
        claim = make_claim(
            match=self.match,
            applicant=self.applicant,
            status=ClaimStatus.CLAIMING,
            attempts_used=3,
            max_attempts=3,
        )
        self.assertFalse(claim_service.can_submit(claim))

    def test_can_submit_false_when_closed(self) -> None:
        for status in (ClaimStatus.APPROVED, ClaimStatus.REJECTED):
            with self.subTest(status=status):
                claim = make_claim(
                    match=self.match, applicant=self.applicant, status=status, attempts_used=1
                )
                self.assertFalse(claim_service.can_submit(claim))

    def test_can_submit_true_after_request_more_info(self) -> None:
        claim = make_claim(
            match=self.match,
            applicant=self.applicant,
            status=ClaimStatus.REQUEST_MORE_INFO,
            attempts_used=1,
        )
        self.assertTrue(claim_service.can_submit(claim))


class TestAdminClaimListPermission(unittest.TestCase):
    """`GET /api/v1/admin/claims` 权限（契约 §3.10.2 异常：403）。"""

    def test_student_is_rejected(self) -> None:
        student = make_user(user_id=13, username="demo_finder")
        with self.assertRaises(ForbiddenError) as ctx:
            require_admin(student)
        self.assertEqual(ctx.exception.http_status, 403)
        self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)

    def test_admin_passes(self) -> None:
        admin = make_admin()
        self.assertIs(require_admin(admin), admin)

    def test_admin_flag_follows_role(self) -> None:
        self.assertTrue(make_admin().is_admin)
        self.assertFalse(make_user().is_admin)


class TestAdminClaimListQuery(unittest.TestCase):
    """`GET /api/v1/admin/claims` 查询参数解析（契约 §3.10.2）。"""

    def test_defaults_to_pending_queue(self) -> None:
        query = AdminClaimQuery()
        self.assertEqual(query.statuses, ["pending_review", "request_more_info"])
        self.assertEqual(query.risk_levels, [])
        self.assertEqual(query.sort_by, "risk_level")
        self.assertEqual(query.order, "desc")
        self.assertTrue(query.pending_only)

    def test_status_all_means_no_filter(self) -> None:
        query = AdminClaimQuery(status="all")
        self.assertEqual(query.statuses, [])
        self.assertFalse(query.pending_only)

    def test_multi_values(self) -> None:
        query = AdminClaimQuery(status="pending_review, approved", risk_level="high,medium")
        self.assertEqual(query.statuses, ["pending_review", "approved"])
        self.assertEqual(query.risk_levels, ["high", "medium"])

    def test_invalid_status_rejected(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with self.assertRaises(PydanticValidationError):
            AdminClaimQuery(status="not_a_status")

    def test_invalid_risk_level_rejected(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with self.assertRaises(PydanticValidationError):
            AdminClaimQuery(risk_level="critical")

    def test_invalid_sort_and_order_rejected(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with self.assertRaises(PydanticValidationError):
            AdminClaimQuery(sort_by="title")
        with self.assertRaises(PydanticValidationError):
            AdminClaimQuery(order="upward")


class TestAdminClaimListItem(unittest.TestCase):
    """待审核列表元素结构（契约 §3.10.2）。"""

    def setUp(self) -> None:
        self.lost, self.found, self.match = make_lost_and_found()
        self.admin = make_admin()
        self.claim = make_verified_claim(match=self.match, applicant=self.found.owner)

    def test_item_fields(self) -> None:
        payload = admin_service.serialize_claim_list_item(self.claim, viewer=self.admin)
        for key in (
            "claim_id",
            "status",
            "status_text",
            "risk_level",
            "risk_level_text",
            "verification_score",
            "credibility_score",
            "attempts_used",
            "applicant",
            "item",
            "total_score",
            "match_level",
            "created_at",
        ):
            self.assertIn(key, payload, msg=f"缺少契约字段 {key}")
        self.assertEqual(payload["status_text"], "待审核")
        self.assertEqual(payload["risk_level_text"], "低风险")
        self.assertEqual(payload["applicant"]["username"], self.found.owner.username)
        self.assertEqual(payload["item"]["id"], self.found.id)
        self.assertEqual(payload["match_level"], "high")

    def test_item_block_matches_contract_shape(self) -> None:
        payload = admin_service.serialize_claim_list_item(self.claim, viewer=self.admin)
        self.assertEqual(
            set(payload["item"].keys()), {"id", "title", "category", "post_type", "thumbnail_url"}
        )
        self.assertEqual(set(payload["applicant"].keys()), {"id", "username", "contact_id"})

    def test_list_item_leaks_nothing(self) -> None:
        """列表不含隐藏特征与答案线索（detail=False 的列表视图天然不含）。"""
        payload = admin_service.serialize_claim_list_item(self.claim, viewer=self.admin)
        assert_no_sensitive_leak(payload)
        assert_no_answer_leak(payload)
        dumped = str(payload)
        for secret in self.lost.hidden_features + self.found.hidden_features:
            self.assertNotIn(str(secret), dumped)

    def test_unverified_claim_scores_are_null_in_list(self) -> None:
        """列表场景允许 null（与详情接口「不出现」的规则不同）。"""
        claim = make_claim(match=self.match, applicant=self.found.owner)
        payload = admin_service.serialize_claim_list_item(claim, viewer=self.admin)
        self.assertIsNone(payload["verification_score"])
        self.assertIsNone(payload["credibility_score"])


class TestRouteRegistration(unittest.TestCase):
    """两个新接口必须真的注册在契约路径上（防止只写服务不挂路由）。

    ⚠ 这里用 `conftest.openapi_paths()` 而不是遍历 `app.routes`：
       FastAPI 较新版本会把 `include_router()` 的子路由包进 `_IncludedRouter`，
       直接遍历 `app.routes` 只能看到文档路由与挂载点，业务路由全部「消失」，
       于是该测试会误报失败（或反过来，在替身环境下误报通过）。
    """

    def test_routes_exist(self) -> None:
        from app.main import app

        paths = conftest.openapi_paths(app)
        self.assertIn("/api/v1/claims/{claim_id}", paths)
        self.assertIn("GET", paths["/api/v1/claims/{claim_id}"])
        self.assertIn("/api/v1/admin/claims", paths)
        self.assertIn("GET", paths["/api/v1/admin/claims"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
