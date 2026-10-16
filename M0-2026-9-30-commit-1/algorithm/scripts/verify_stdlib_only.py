"""校验「无第三方依赖」的降级路径（模拟未安装 PyYAML / pydantic）。

做法：在解释器启动时用 import hook 让 ``import yaml`` / ``import pydantic`` 抛
``ImportError``，然后跑一遍核心逻辑（配置加载 → 匹配 → 属性提取 → 可信度 → 数据集）。

用途::

    python scripts/verify_stdlib_only.py

退出码 0 表示降级路径可用。
"""

from __future__ import annotations

import builtins
import importlib.abc
import importlib.machinery
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BLOCKED = ("yaml", "pydantic")


class _Blocker(importlib.abc.MetaPathFinder):
    """把指定模块的导入变成 ImportError，模拟未安装。"""

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked for test: {fullname}")
        return None


def main() -> int:
    sys.meta_path.insert(0, _Blocker())

    from app.config_loader import CONFIG_DIR, load_config, load_yaml  # noqa: E402
    from app.matcher import MatchEngine  # noqa: E402
    from app.extractor import AttributeExtractor  # noqa: E402
    from app.credibility import CredibilityScorer  # noqa: E402
    from app.mock_data import build_demo_items, build_demo_matches  # noqa: E402
    import app.schemas as schemas  # noqa: E402

    problems = []

    # 1) YAML 解析降级
    try:
        data = load_yaml(os.path.join(CONFIG_DIR, "matching.yaml"))
        if data.get("weights", {}).get("text") != 0.25:
            problems.append("极简 YAML 解析器读到的 weights.text 不正确")
    except ImportError as exc:
        problems.append(f"load_yaml 在无 PyYAML 时失败：{exc}")

    config = load_config(reload=True)
    if any("PyYAML" in warning for warning in config.warnings):
        problems.append("配置加载仍尝试使用 PyYAML")

    # 2) 匹配、提取、可信度
    try:
        engine = MatchEngine(config)
        items = build_demo_items()
        matches = build_demo_matches(items)
        outcome = engine.match_pair(items[0], items[len(items) // 2])
        if not 0.0 <= outcome.total_score <= 1.0:
            problems.append(f"总分越界：{outcome.total_score}")
        extractor = AttributeExtractor(config)
        extracted = extractor.extract(description="黑色的无线蓝牙耳机，盒盖内侧有一道划痕").to_dict()
        if extracted["category"] != "无线耳机":
            problems.append("属性提取在降级路径下结果不正确")
        scorer = CredibilityScorer(config)
        result = scorer.score(
            [{"id": "q1", "reference_answer": "盒体右侧有一道划痕"}],
            ["右边刮了一道"], {"total_score": 0.9}, items[0],
        )
        if result.risk_level not in {"low", "medium", "high"}:
            problems.append("风险等级取值异常")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"核心逻辑在降级路径下抛异常：{exc.__class__.__name__}: {exc}")

    # 3) schemas 降级为 dataclass
    if schemas.PYDANTIC_AVAILABLE:
        problems.append("pydantic 仍被判定为可用（模拟未生效）")
    try:
        response = schemas.build_match_response({"model_version": "baseline-1.0", "results": [
            {"candidate_id": 1, "total_score": 0.5,
             "scores": {"image_score": 0.5, "text_score": 0.5, "category_score": 1.0,
                        "time_score": 1.0, "location_score": 0.5, "brand_model_score": 0.5},
             "evidence": [], "uncertainties": []},
        ]})
        plain = schemas.to_plain_dict(response)
        if plain.get("model_version") != "baseline-1.0":
            problems.append("dataclass 版 schemas 转换结果不正确")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"dataclass 版 schemas 失败：{exc.__class__.__name__}: {exc}")

    # 4) main 模块仍可导入（只是没有 fastapi 应用对象）
    try:
        from app import main as main_module
        payload = main_module.handle_extract({"description": "蓝色保温杯，杯底刻有名字"})
        if "uncertainties" not in payload:
            problems.append("handle_extract 在降级路径下返回结构异常")
        if main_module.FASTAPI_AVAILABLE:
            problems.append("fastapi 被判定为可用（模拟未生效）")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"app.main 导入失败：{exc.__class__.__name__}: {exc}")

    print(f"被屏蔽的模块: {', '.join(BLOCKED)}")
    print(f"演示数据: {len(build_demo_items())} 条物品 / {len(build_demo_matches())} 组匹配样本")
    print(f"配置来源: {config.sources}")
    if problems:
        print("\n降级路径存在问题：")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("\n降级路径自检通过：无 PyYAML / 无 pydantic / 无 fastapi 时核心功能全部可用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
