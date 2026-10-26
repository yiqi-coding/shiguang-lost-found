"""AI 属性提取的降级与 Schema 校验测试（契约 §3.14 / §4.2 / §4.3）。

覆盖：

- 算法输出必须通过 JSON Schema 校验（缺字段 / 类型错误 / 超长数组一律判失败）；
- 规则降级提取能从描述中抽出类别 / 颜色 / 品牌 / 特征 / 配件；
- 规则结果自身也必须满足 Schema（降级不能产出脏数据）；
- 同输入必得同输出（确定性，Mock 硬性要求 §4.4）。
"""

from __future__ import annotations

import unittest

import conftest  # noqa: F401 - 必须先导入：完成环境准备与依赖打桩

from app.services.attribute_service import (
    ATTRIBUTE_JSON_SCHEMA,
    ATTRIBUTE_JSON_SCHEMA_KEYS,
    rule_extract_attributes,
    validate_attribute_payload,
)


class TestJsonSchemaValidation(unittest.TestCase):
    """算法输出的 Schema 校验。"""

    def setUp(self) -> None:
        self.valid = {
            "category": "无线耳机",
            "brand": "Apple",
            "model": None,
            "color": "黑色",
            "material": None,
            "visible_features": ["椭圆形充电盒"],
            "accessories": ["充电盒"],
            "keywords": ["蓝牙耳机", "黑色"],
            "uncertainties": ["品牌标识不清晰"],
        }

    def test_valid_payload_passes(self) -> None:
        ok, problems = validate_attribute_payload(self.valid)
        self.assertTrue(ok, msg=f"合法结果不应被判失败：{problems}")
        self.assertEqual(problems, [])

    def test_nullable_fields_accepted(self) -> None:
        payload = dict(self.valid, brand=None, color=None, model=None, material=None)
        ok, _ = validate_attribute_payload(payload)
        self.assertTrue(ok)

    def test_missing_required_field_fails(self) -> None:
        payload = dict(self.valid)
        payload.pop("keywords")
        ok, problems = validate_attribute_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("keywords" in problem for problem in problems))

    def test_wrong_type_fails(self) -> None:
        payload = dict(self.valid, visible_features="椭圆形充电盒")
        ok, problems = validate_attribute_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("visible_features" in problem for problem in problems))

    def test_array_item_type_checked(self) -> None:
        payload = dict(self.valid, keywords=[1, 2, 3])
        ok, problems = validate_attribute_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("keywords" in problem for problem in problems))

    def test_overlong_array_fails(self) -> None:
        payload = dict(self.valid, keywords=[f"k{index}" for index in range(50)])
        ok, problems = validate_attribute_payload(payload)
        self.assertFalse(ok)
        self.assertTrue(any("数量超过" in problem for problem in problems))

    def test_non_object_payload_fails(self) -> None:
        for payload in (None, [], "text", 42):
            with self.subTest(payload=payload):
                ok, _problems = validate_attribute_payload(payload)
                self.assertFalse(ok)

    def test_schema_declares_required_fields(self) -> None:
        self.assertEqual(
            set(ATTRIBUTE_JSON_SCHEMA["required"]),
            {"category", "visible_features", "keywords", "uncertainties"},
        )
        self.assertEqual(
            set(ATTRIBUTE_JSON_SCHEMA["properties"].keys()),
            set(ATTRIBUTE_JSON_SCHEMA_KEYS),
        )

    def test_extra_fields_allowed(self) -> None:
        payload = dict(self.valid, model_version="baseline-1.0", source="algorithm")
        ok, _ = validate_attribute_payload(payload)
        self.assertTrue(ok)


class TestRuleExtraction(unittest.TestCase):
    """规则降级提取（算法与 MaaS 全部不可用时的兜底）。"""

    def test_extracts_earphone_attributes(self) -> None:
        result = rule_extract_attributes(
            title="丢失一副黑色无线耳机",
            description="在图书馆三楼落下，充电盒上有一道划痕，蓝牙名称是 YiQi-AirPods，小米品牌。",
            category=None,
            color=None,
            brand=None,
        )
        self.assertEqual(result["category"], "无线耳机")
        self.assertEqual(result["color"], "黑色")
        self.assertEqual(result["brand"], "小米")
        self.assertTrue(result["visible_features"])
        self.assertIn("充电盒", result["accessories"])
        self.assertTrue(result["visible_features"] or result["keywords"])

    def test_user_values_take_priority(self) -> None:
        """用户填写的类别/颜色/品牌优先于关键词推断。"""
        result = rule_extract_attributes(
            title="丢失物品",
            description="黑色的杯子，可能是膳魔师。",
            category="水杯",
            color="白色",
            brand="富光",
        )
        self.assertEqual(result["category"], "水杯")
        self.assertEqual(result["color"], "白色")
        self.assertEqual(result["brand"], "富光")

    def test_result_passes_schema(self) -> None:
        """降级结果也必须满足 JSON Schema（不允许产出脏数据）。"""
        result = rule_extract_attributes(title="丢失雨伞", description="一把折叠伞，伞柄有磨损。")
        ok, problems = validate_attribute_payload(result)
        self.assertTrue(ok, msg=f"规则提取结果未通过 Schema：{problems}")

    def test_deterministic(self) -> None:
        """同输入必得同输出（Mock 确定性要求）。"""
        kwargs = {
            "title": "在图书馆捡到黑色耳机",
            "description": "椭圆形充电盒，盒盖内侧有划痕，配件齐全。",
            "category": "无线耳机",
            "color": None,
            "brand": None,
        }
        first = rule_extract_attributes(**kwargs)
        second = rule_extract_attributes(**kwargs)
        self.assertEqual(first, second)

    def test_uncertainty_markers_are_recorded(self) -> None:
        result = rule_extract_attributes(
            title="丢失水杯", description="好像是一个黑色的保温杯，具体记不清了。"
        )
        self.assertTrue(any("不确定" in note or "措辞" in note for note in result["uncertainties"]))

    def test_empty_description_does_not_crash(self) -> None:
        result = rule_extract_attributes(title="", description="")
        ok, _ = validate_attribute_payload(result)
        self.assertTrue(ok)
        self.assertEqual(result["visible_features"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
