"""Pydantic Schema 校验测试（契约 §3.3 / §3.9 / §3.11）。

覆盖表单与请求体的关键校验规则（错误信息必须为中文）：

- `title` 5~80 字、`description` 10~2000 字、`location` 1~128 字；
- `post_type` / `category` 枚举白名单；
- `event_time` 不得晚于「当前时间 +5 分钟」、不得早于「当前时间 -2 年」；
- `hidden_features` 非空、字符串 JSON 可解析、序列化后 ≤2000 字；
- `contact_id` **入库前脱敏**；
- `answers` 至少 1 项、`answer` ≤500 字、`question_id` 不重复；
- 管理员审核 `reject` / `request_more_info` 时 `comment` 必填且 10~500 字。
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from pydantic import ValidationError as PydanticValidationError

from app.schemas.admin import ClaimReviewRequest
from app.schemas.claim import ClaimVerifyRequest
from app.schemas.common import PageParams, parse_json_text, split_csv, to_utc_naive
from app.schemas.item import HIDDEN_FEATURES_MAX_LEN, ItemCreate

UTC = timezone.utc


def _valid_payload(**overrides):
    """一份合法的发布表单数据（字符串形式的 JSON，模拟 multipart 表单）。"""
    payload = {
        "post_type": "lost",
        "category": "无线耳机",
        "title": "丢失一副黑色无线耳机",
        "description": "在图书馆三楼自习时落在座位上，充电盒有明显划痕。",
        "location": "图书馆三楼",
        "event_time": (datetime.now(UTC) - timedelta(hours=3)).isoformat(),
        "hidden_features": json.dumps(["盒盖内侧有一道约3厘米划痕"], ensure_ascii=False),
    }
    payload.update(overrides)
    return payload


class TestPageParams(unittest.TestCase):
    """分页参数（契约 §1.1）。"""

    def test_defaults(self) -> None:
        params = PageParams()
        self.assertEqual((params.page, params.page_size), (1, 20))
        self.assertEqual(params.offset, 0)
        self.assertEqual(params.limit, 20)

    def test_offset_and_limit(self) -> None:
        params = PageParams(page=3, page_size=15)
        self.assertEqual(params.offset, 30)
        self.assertEqual(params.limit, 15)

    def test_page_must_be_positive(self) -> None:
        for page in (0, -1):
            with self.subTest(page=page):
                with self.assertRaises(PydanticValidationError):
                    PageParams(page=page)

    def test_page_size_bounds(self) -> None:
        self.assertEqual(PageParams(page_size=1).page_size, 1)
        self.assertEqual(PageParams(page_size=50).page_size, 50)
        for size in (0, 51, 1000):
            with self.subTest(page_size=size):
                with self.assertRaises(PydanticValidationError):
                    PageParams(page_size=size)

    def test_unknown_query_params_are_ignored(self) -> None:
        """查询参数模型必须忽略无关参数（如前端缓存击穿参数 `_t`），不得因此报 400。"""
        params = PageParams(page=1, page_size=20, keyword="耳机", category="无线耳机")
        self.assertEqual(params.page_size, 20)


class TestItemCreate(unittest.TestCase):
    """`POST /api/v1/items` 表单校验。"""

    def test_valid_payload(self) -> None:
        item = ItemCreate(**_valid_payload())
        self.assertEqual(item.post_type, "lost")
        self.assertEqual(item.category, "无线耳机")
        self.assertEqual(item.hidden_features, ["盒盖内侧有一道约3厘米划痕"])
        self.assertIsNone(item.contact_id)

    def test_title_length_bounds(self) -> None:
        for title in ("短", "四字啊", "很" * 81):
            with self.subTest(title=title):
                with self.assertRaises(PydanticValidationError) as ctx:
                    ItemCreate(**_valid_payload(title=title))
                self.assertIn("标题长度需在", str(ctx.exception))
        # 边界值通过
        self.assertEqual(len(ItemCreate(**_valid_payload(title="五" * 5)).title), 5)
        self.assertEqual(len(ItemCreate(**_valid_payload(title="八" * 80)).title), 80)

    def test_title_is_stripped_before_validation(self) -> None:
        item = ItemCreate(**_valid_payload(title="  丢失一副黑色无线耳机  "))
        self.assertEqual(item.title, "丢失一副黑色无线耳机")

    def test_description_length_bounds(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(description="太短了"))
        self.assertIn("描述长度需在", str(ctx.exception))
        with self.assertRaises(PydanticValidationError):
            ItemCreate(**_valid_payload(description="长" * 2001))

    def test_location_length_bounds(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(location="   "))
        self.assertIn("地点长度需在", str(ctx.exception))
        with self.assertRaises(PydanticValidationError):
            ItemCreate(**_valid_payload(location="地" * 129))

    def test_post_type_enum(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(post_type="borrowed"))
        self.assertIn("post_type 只能是", str(ctx.exception))
        self.assertEqual(ItemCreate(**_valid_payload(post_type="found")).post_type, "found")

    def test_category_enum(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(category="电脑"))
        self.assertIn("category 只能取", str(ctx.exception))

    def test_event_time_bounds(self) -> None:
        # 晚于当前时间 +5 分钟 → 拒绝
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(event_time=(datetime.now(UTC) + timedelta(minutes=30)).isoformat()))
        self.assertIn("不能晚于当前时间", str(ctx.exception))
        # 早于两年前 → 拒绝
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(event_time=(datetime.now(UTC) - timedelta(days=800)).isoformat()))
        self.assertIn("不能早于两年前", str(ctx.exception))
        # 容差范围内通过
        ItemCreate(**_valid_payload(event_time=(datetime.now(UTC) + timedelta(minutes=2)).isoformat()))

    def test_event_time_is_converted_to_naive_utc(self) -> None:
        item = ItemCreate(**_valid_payload(event_time="2026-09-28T15:00:00+08:00"))
        self.assertIsNone(item.event_time.tzinfo)
        self.assertEqual(item.event_time, datetime(2026, 9, 28, 7, 0, 0))

    def test_hidden_features_must_be_non_empty(self) -> None:
        for value in ("[]", "", "   "):
            with self.subTest(value=value):
                with self.assertRaises(PydanticValidationError) as ctx:
                    ItemCreate(**_valid_payload(hidden_features=value))
                self.assertIn("隐藏特征", str(ctx.exception))

    def test_hidden_features_invalid_json_gives_chinese_error(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(hidden_features="这不是 JSON"))
        self.assertIn("必须是合法的 JSON", str(ctx.exception))

    def test_hidden_features_must_be_array(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(hidden_features='{"a": 1}'))
        self.assertIn("必须是 JSON 数组", str(ctx.exception))

    def test_hidden_features_serialized_length_limit(self) -> None:
        long_feature = "特" * 1200
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(hidden_features=json.dumps([long_feature, long_feature], ensure_ascii=False)))
        self.assertIn("隐藏特征内容过长", str(ctx.exception))
        self.assertGreater(HIDDEN_FEATURES_MAX_LEN, 0)

    def test_public_features_optional(self) -> None:
        item = ItemCreate(**_valid_payload(public_features=json.dumps(["椭圆形充电盒"], ensure_ascii=False)))
        self.assertEqual(item.public_features, ["椭圆形充电盒"])
        item = ItemCreate(**_valid_payload(public_features=json.dumps({"shape": "椭圆"}, ensure_ascii=False)))
        self.assertEqual(item.public_features, {"shape": "椭圆"})
        self.assertIsNone(ItemCreate(**_valid_payload(public_features=None)).public_features)

    def test_contact_id_is_masked_on_validation(self) -> None:
        """红线：联系方式在进入系统的那一刻就已脱敏。"""
        item = ItemCreate(**_valid_payload(contact_id="13812345678"))
        self.assertEqual(item.contact_id, "138****5678")
        item = ItemCreate(**_valid_payload(contact_id="zhangsan@example.com"))
        assert item.contact_id is not None
        self.assertNotIn("zhangsan", item.contact_id)

    def test_contact_id_too_long(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ItemCreate(**_valid_payload(contact_id="x" * 129))
        self.assertIn("联系方式长度不能超过", str(ctx.exception))

    def test_color_and_brand_length(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ItemCreate(**_valid_payload(color="颜" * 33))
        with self.assertRaises(PydanticValidationError):
            ItemCreate(**_valid_payload(brand="品" * 65))

    def test_to_item_kwargs_contains_no_unexpected_secret(self) -> None:
        item = ItemCreate(**_valid_payload(contact_id="13812345678"))
        kwargs = item.to_item_kwargs(owner_user_id=7)
        self.assertEqual(kwargs["owner_user_id"], 7)
        self.assertEqual(kwargs["contact_id"], "138****5678")
        self.assertEqual(kwargs["status"], "published")
        self.assertEqual(kwargs["ai_status"], "pending")


class TestClaimVerifyRequest(unittest.TestCase):
    """`POST /api/v1/claims/{claim_id}/verify` 请求体校验。"""

    def test_valid(self) -> None:
        payload = ClaimVerifyRequest(
            answers=[
                {"question_id": "q1", "answer": "盒子右侧有一道划痕"},
                {"question_id": "q2", "answer": "YiQi-AirPods"},
            ]
        )
        self.assertEqual(len(payload.answers), 2)
        self.assertEqual(payload.answer_map()["q2"], "YiQi-AirPods")

    def test_answers_required(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ClaimVerifyRequest(answers=[])

    def test_answer_length_limit(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ClaimVerifyRequest(answers=[{"question_id": "q1", "answer": "长" * 501}])
        self.assertIn("500", str(ctx.exception))

    def test_blank_answer_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ClaimVerifyRequest(answers=[{"question_id": "q1", "answer": "   "}])

    def test_duplicate_question_id_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ClaimVerifyRequest(
                answers=[
                    {"question_id": "q1", "answer": "答案一"},
                    {"question_id": "q1", "answer": "答案二"},
                ]
            )
        self.assertIn("question_id 重复", str(ctx.exception))


class TestClaimReviewRequest(unittest.TestCase):
    """`POST /api/v1/admin/claims/{claim_id}/review` 请求体校验（契约 §3.11）。"""

    def test_approve_without_comment(self) -> None:
        request = ClaimReviewRequest(action="approve")
        self.assertIsNone(request.comment)

    def test_approve_with_comment(self) -> None:
        request = ClaimReviewRequest(action="approve", comment="匹配依据与隐藏特征验证均通过")
        self.assertEqual(request.comment, "匹配依据与隐藏特征验证均通过")

    def test_reject_requires_comment(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ClaimReviewRequest(action="reject")
        self.assertIn("comment 为必填项", str(ctx.exception))

    def test_request_more_info_requires_comment(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ClaimReviewRequest(action="request_more_info", comment="   ")
        self.assertIn("comment 为必填项", str(ctx.exception))

    def test_comment_min_length(self) -> None:
        with self.assertRaises(PydanticValidationError) as ctx:
            ClaimReviewRequest(action="reject", comment="太短")
        self.assertIn("comment 长度需在", str(ctx.exception))

    def test_comment_max_length(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ClaimReviewRequest(action="reject", comment="长" * 501)

    def test_invalid_action_rejected(self) -> None:
        with self.assertRaises(PydanticValidationError):
            ClaimReviewRequest(action="delete")

    def test_comment_is_stripped(self) -> None:
        request = ClaimReviewRequest(action="reject", comment="  材料不足，请补充购买凭证  ")
        self.assertEqual(request.comment, "材料不足，请补充购买凭证")


class TestCommonHelpers(unittest.TestCase):
    """`schemas/common.py` 工具函数。"""

    def test_split_csv(self) -> None:
        self.assertEqual(split_csv("published,matched"), ["published", "matched"])
        self.assertEqual(split_csv(" published , , matched "), ["published", "matched"])
        self.assertEqual(split_csv(""), [])
        self.assertEqual(split_csv(None), [])
        self.assertEqual(split_csv("a,a,b"), ["a", "b"])

    def test_parse_json_text(self) -> None:
        self.assertEqual(parse_json_text('["a"]', field="f"), ["a"])
        self.assertEqual(parse_json_text(["a"], field="f"), ["a"])
        self.assertIsNone(parse_json_text(None, field="f"))
        with self.assertRaises(ValueError):
            parse_json_text("{bad json", field="隐藏特征")
        with self.assertRaises(ValueError):
            parse_json_text('{"a":1}', field="f", expect="array")

    def test_parse_json_text_required(self) -> None:
        with self.assertRaises(ValueError):
            parse_json_text(None, field="隐藏特征", allow_none=False)

    def test_to_utc_naive(self) -> None:
        aware = datetime(2026, 9, 28, 15, 0, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(to_utc_naive(aware), datetime(2026, 9, 28, 7, 0))
        naive = datetime(2026, 9, 28, 7, 0)
        self.assertEqual(to_utc_naive(naive), naive)


if __name__ == "__main__":
    unittest.main(verbosity=2)
