"""M2 指标回归测试：测试集划分 / 9 项指标 / A/B/C/D 消融 / 安全红线。

运行方式（与其余用例一致）::

    cd E:\\ICT\\algorithm
    python -X utf8 -m unittest discover -s tests -t . -v
    python -X utf8 -m unittest tests.test_metrics -v      # 只跑本文件

覆盖范围：
  1. 测试集划分：确定性、60/20/20 比例、无重叠、并集为全集；
  2. 指标函数：小样例手工断言 Recall@1 / Recall@K / MRR / Precision@1；
  3. 消融权重归一化：各实验权重和 == 1.0；D 组重算与 engine.total_score 一致（1e-9）；
  4. 消融四组都能跑出结果，且分数在 [0,1]；
  5. 安全红线：任何输出（eval / ablation / split）都不含 hidden_features 原文。

本文件只读现有 ``app`` 模块，不修改任何算法代码。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._bootstrap import ensure_project_root  # noqa: E402

_ROOT = ensure_project_root()
if os.path.join(_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import evaluate as E  # noqa: E402
import generate_demo_data as G  # noqa: E402
from app import mock_data  # noqa: E402
from app.config_loader import DIMENSIONS, RuntimeConfig, load_config  # noqa: E402
from app.credibility import CredibilityScorer  # noqa: E402
from app.matcher import MatchEngine  # noqa: E402

SEED = E.DEFAULT_SEED
_DATASET = E.load_dataset()
_ITEMS = _DATASET["items"]
_MATCHES = _DATASET["matches"]
_SPLIT = E.build_split(_ITEMS, SEED)
#: 复用一次完整评估（评测较重：24 次查询 × 60 候选 × 6 维 + 可信度构造）
_RESULT = E.evaluate(split_name="all", ablation="all", top_k=E.DEFAULT_TOP_K, seed=SEED,
                     dataset=_DATASET, write_split_file=False)
_EVAL = _RESULT["eval"]
_ABLATION = _RESULT["ablation"]

_ALLOWED_ERROR_KEYS = {
    "sample_id", "lost_item_id", "expected_found_item_id", "got_top1_id",
    "rank", "top1_score", "correct_score", "note",
}


def _hidden_feature_texts() -> list:
    values = []
    for item in _ITEMS:
        for feature in item.get("hidden_features") or []:
            text = str(feature).strip()
            if len(text) >= 3:
                values.append(text)
    return values


def _iter_strings(node, path="") -> list:
    """递归收集结构里所有字符串（含 key），返回 [(路径, 字符串)]。"""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((f"{path}.{key}", str(key)))
            found.extend(_iter_strings(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(_iter_strings(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        found.append((path, node))
    return found


# ===========================================================================
# 1. 测试集划分
# ===========================================================================
class TestSplit(unittest.TestCase):
    """需求 §9.2：120 条物品按 60/20/20 确定性划分，无重叠、并集为全集。"""

    def test_deterministic_same_seed(self):
        again = E.build_split(_ITEMS, SEED)
        self.assertEqual(_SPLIT["assignment"], again["assignment"])
        self.assertEqual(_SPLIT["summary"], again["summary"])
        # 物品顺序被打乱也不应改变划分（划分只看 id）
        shuffled = list(reversed(_ITEMS))
        self.assertEqual(E.build_split(shuffled, SEED)["assignment"], _SPLIT["assignment"])

    def test_ratio_60_20_20(self):
        summary = _SPLIT["summary"]
        total = len(_ITEMS)
        self.assertEqual(total, 120)
        self.assertEqual(sum(summary.values()), total)
        self.assertEqual(summary["train"], 72)
        self.assertEqual(summary["val"], 24)
        self.assertEqual(summary["test"], 24)
        for name, expected in (("train", 0.60), ("val", 0.20), ("test", 0.20)):
            self.assertLessEqual(abs(summary[name] - total * expected), 1.0,
                                 f"{name} 比例偏离 60/20/20 超过 ±1")

    def test_no_overlap_and_full_coverage(self):
        buckets = {name: set() for name, _ in E.SPLIT_RATIOS}
        for item_id, name in _SPLIT["assignment"].items():
            self.assertIn(name, buckets)
            buckets[name].add(item_id)
        names = list(buckets)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                self.assertEqual(buckets[left] & buckets[right], set(),
                                 f"{left} 与 {right} 存在重叠")
        union = set().union(*buckets.values())
        self.assertEqual(union, {str(item["id"]) for item in _ITEMS})
        self.assertEqual(len(_SPLIT["assignment"]), len(_ITEMS))

    def test_stratified_by_category_and_post_type(self):
        by_id = {str(item["id"]): item for item in _ITEMS}
        seen = {}
        for item_id, name in _SPLIT["assignment"].items():
            item = by_id[item_id]
            seen.setdefault((item["category"], item["post_type"]), set()).add(name)
        self.assertEqual(len(seen), 12)  # 6 类别 × 2 种 post_type
        for key, names in seen.items():
            self.assertEqual(names, {"train", "val", "test"}, f"{key} 未覆盖三个子集")

    def test_different_seed_changes_assignment(self):
        other = E.build_split(_ITEMS, SEED + 1)
        self.assertNotEqual(other["assignment"], _SPLIT["assignment"])


class TestDeterministicShuffle(unittest.TestCase):
    def test_shuffle_is_pure_and_stable(self):
        values = list(range(20))
        first = E.deterministic_shuffle(values, SEED, "g")
        second = E.deterministic_shuffle(values, SEED, "g")
        self.assertEqual(first, second)
        self.assertEqual(sorted(first), values)           # 元素不增不减
        self.assertEqual(values, list(range(20)))          # 不改动入参
        self.assertNotEqual(first, E.deterministic_shuffle(values, SEED, "h"))


# ===========================================================================
# 2. 指标函数（手工小样例）
# ===========================================================================
class TestRankMetrics(unittest.TestCase):
    def test_hand_computed_example(self):
        # rank = [1, 3, 10] → Recall@1 = 1/3，Recall@5 = 2/3，MRR = (1 + 1/3 + 1/10)/3
        ranks = [1, 3, 10]
        result = E.rank_metrics(ranks, top_k=5)
        self.assertAlmostEqual(result["Recall@1"], 1 / 3, places=12)
        self.assertAlmostEqual(result["Recall@5"], 2 / 3, places=12)
        self.assertAlmostEqual(result["Precision@1"], 1 / 3, places=12)
        self.assertAlmostEqual(result["MRR"], (1 + 1 / 3 + 1 / 10) / 3, places=12)
        self.assertEqual(result["queries"], 3)
        self.assertEqual(result["hit_count"], 3)
        self.assertEqual(result["miss_count"], 0)
        self.assertEqual(result["median_rank"], 3.0)

    def test_all_top1(self):
        result = E.rank_metrics([1, 1, 1, 1], top_k=5)
        self.assertEqual(result["Recall@1"], 1.0)
        self.assertEqual(result["MRR"], 1.0)
        self.assertEqual(result["Precision@1"], 1.0)

    def test_misses_count_as_zero(self):
        # None = 正确答案不在候选池内（未召回）
        result = E.rank_metrics([1, None, 7], top_k=5)
        self.assertAlmostEqual(result["Recall@1"], 1 / 3, places=12)
        self.assertAlmostEqual(result["Recall@5"], 1 / 3, places=12)
        self.assertAlmostEqual(result["MRR"], (1 + 0 + 1 / 7) / 3, places=12)
        self.assertEqual(result["miss_count"], 1)
        self.assertEqual(result["hit_count"], 2)

    def test_top_k_is_respected(self):
        ranks = [1, 4, 9]
        self.assertAlmostEqual(E.rank_metrics(ranks, top_k=3)["Recall@3"], 1 / 3, places=12)
        self.assertAlmostEqual(E.rank_metrics(ranks, top_k=9)["Recall@9"], 1.0, places=12)

    def test_empty_input_is_zero_not_error(self):
        result = E.rank_metrics([], top_k=5)
        self.assertEqual(result["queries"], 0)
        self.assertEqual(result["Recall@1"], 0.0)
        self.assertEqual(result["MRR"], 0.0)


class TestDiscriminationAccuracy(unittest.TestCase):
    def test_perfect_separation(self):
        result = E.discrimination_accuracy([0.9, 0.8], [0.4, 0.3])
        self.assertEqual(result["pairs"], 4)
        self.assertEqual(result["wins"], 4)
        self.assertEqual(result["accuracy"], 1.0)

    def test_half_separation(self):
        # 正样本 [0.9, 0.5]，负样本 [0.6, 0.2] → 仅 (0.9>0.6)(0.9>0.2)(0.5>0.2) 计 3 胜
        result = E.discrimination_accuracy([0.9, 0.5], [0.6, 0.2])
        self.assertEqual(result["pairs"], 4)
        self.assertEqual(result["wins"], 3)
        self.assertAlmostEqual(result["accuracy"], 0.75, places=12)

    def test_ties_are_not_wins(self):
        result = E.discrimination_accuracy([0.5], [0.5])
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["accuracy"], 0.0)


class TestLatencyMetrics(unittest.TestCase):
    def test_nearest_rank_percentiles(self):
        values = [float(x) for x in range(1, 21)]           # 1..20 ms
        result = E.latency_metrics(values)
        self.assertEqual(result["samples"], 20)
        self.assertEqual(result["p50_ms"], 11.0)            # round(0.50*19)=10 → 第 11 个
        self.assertEqual(result["p95_ms"], 19.0)            # round(0.95*19)=18 → 第 19 个
        self.assertEqual(result["max_ms"], 20.0)
        self.assertAlmostEqual(result["mean_ms"], 10.5, places=6)

    def test_empty_is_zero(self):
        self.assertEqual(E.latency_metrics([])["mean_ms"], 0.0)


# ===========================================================================
# 3. 消融实验：权重归一化 / D 组一致性 / 取值范围
# ===========================================================================
class TestAblationWeights(unittest.TestCase):
    def setUp(self):
        self.weights = load_config().weights

    def test_dimension_sets_match_spec(self):
        self.assertEqual(E.ABLATIONS["A"]["dimensions"], ("category",))
        self.assertEqual(E.ABLATIONS["B"]["dimensions"], ("category", "text"))
        self.assertEqual(E.ABLATIONS["C"]["dimensions"], ("category", "text", "time", "location"))
        self.assertEqual(E.ABLATIONS["D"]["dimensions"], DIMENSIONS)

    def test_normalized_weights_sum_to_one(self):
        for key, spec in E.ABLATIONS.items():
            normalized = E.normalized_ablation_weights(self.weights, spec["dimensions"])
            self.assertAlmostEqual(sum(normalized.values()), 1.0, places=12, msg=f"{key} 组权重和不为 1")
            self.assertEqual(set(normalized), set(spec["dimensions"]))
            for dim, value in normalized.items():
                self.assertGreaterEqual(value, 0.0)
                if self.weights.get(dim, 0.0) > 0:
                    self.assertGreater(value, 0.0)

    def test_expected_normalized_values(self):
        # A = 仅 category → 1.0；B = 0.20/0.45、0.25/0.45；C 组 0.20/0.75、0.25/0.75、0.15/0.75
        a = E.normalized_ablation_weights(self.weights, E.ABLATIONS["A"]["dimensions"])
        b = E.normalized_ablation_weights(self.weights, E.ABLATIONS["B"]["dimensions"])
        c = E.normalized_ablation_weights(self.weights, E.ABLATIONS["C"]["dimensions"])
        self.assertAlmostEqual(a["category"], 1.0, places=12)
        self.assertAlmostEqual(b["category"], 0.20 / 0.45, places=12)
        self.assertAlmostEqual(b["text"], 0.25 / 0.45, places=12)
        self.assertAlmostEqual(c["category"], 0.20 / 0.75, places=12)
        self.assertAlmostEqual(c["time"], 0.15 / 0.75, places=12)
        self.assertAlmostEqual(c["location"], 0.15 / 0.75, places=12)

    def test_ablation_score_bounds(self):
        scores = {dim: 1.0 for dim in DIMENSIONS}
        for key, spec in E.ABLATIONS.items():
            self.assertAlmostEqual(E.ablation_score(scores, self.weights, spec["dimensions"]), 1.0,
                                   places=12, msg=f"{key} 组满分未归一化到 1.0")
            zero = {dim: 0.0 for dim in DIMENSIONS}
            self.assertAlmostEqual(E.ablation_score(zero, self.weights, spec["dimensions"]), 0.0, places=12)

    def test_all_zero_weight_dimensions_are_safe(self):
        normalized = E.normalized_ablation_weights({dim: 0.0 for dim in DIMENSIONS}, ("category",))
        self.assertEqual(normalized, {"category": 0.0})

    def test_d_group_equals_engine_total_score(self):
        """D 组（六维、契约冻结权重）重算总分必须等于 engine.total_score，容差 1e-9。"""
        items_by_id = {int(item["id"]): item for item in _ITEMS}
        engine = MatchEngine()
        positives = [m for m in _MATCHES if m.get("label") == "positive"]
        self.assertGreaterEqual(len(positives), 20)
        worst = 0.0
        for sample in positives:
            lost, found = E.sample_payloads(sample, items_by_id)
            outcome = engine.match_pair(lost, found)
            recomputed = E.ablation_score(outcome.scores, self.weights, E.ABLATIONS["D"]["dimensions"])
            worst = max(worst, abs(recomputed - outcome.total_score))
        self.assertLessEqual(worst, 1e-9, f"D 组重算与 total_score 最大偏差 {worst:.3e} 超过 1e-9")

    def test_consistency_flag_reported(self):
        consistency = _ABLATION["consistency"]
        self.assertTrue(consistency["D_recomputed_equals_engine_total"],
                        "脚本报告 D 组重算与总分不一致（说明存在非零 adjustment）")
        self.assertLessEqual(consistency["max_abs_delta"], consistency["tolerance"])


class TestAblationRuns(unittest.TestCase):
    def test_four_experiments_present_and_scores_in_range(self):
        experiments = _ABLATION["experiments"]
        self.assertEqual(_ABLATION["order"], ["A", "B", "C", "D"])
        for key in ("A", "B", "C", "D"):
            self.assertIn(key, experiments)
            block = experiments[key]
            self.assertAlmostEqual(block["weight_sum"], 1.0, places=12)
            for metric in ("Recall@1", "Recall@5", "Precision@1", "MRR", "区分准确率"):
                value = block[metric]
                self.assertIsInstance(value, float)
                self.assertGreaterEqual(value, 0.0, f"{key}.{metric} < 0")
                self.assertLessEqual(value, 1.0, f"{key}.{metric} > 1")
            self.assertGreater(block["queries"], 0)
            # 维度子集必须单调扩大（A ⊂ B ⊂ C ⊂ D）
        order = ["A", "B", "C", "D"]
        for left, right in zip(order, order[1:]):
            self.assertTrue(set(experiments[left]["dimensions"]).issubset(set(experiments[right]["dimensions"])))

    def test_improvement_vs_a_reported(self):
        experiments = _ABLATION["experiments"]
        self.assertEqual(experiments["A"]["MRR_vs_A_abs"], 0.0)
        for key in ("B", "C", "D"):
            for metric in ("Recall@1", "Recall@5", "MRR"):
                self.assertIn(f"{metric}_vs_A_abs", experiments[key])
                self.assertIn(f"{metric}_vs_A_rel", experiments[key])
                expected = round(experiments[key][metric] - experiments["A"][metric], 6)
                self.assertAlmostEqual(experiments[key][f"{metric}_vs_A_abs"], expected, places=9)

    def test_monotonicity_not_asserted_but_values_are_real(self):
        """不强制 A<B<C<D；只要求四组都跑出真实结果（可能非单调）。"""
        experiments = _ABLATION["experiments"]
        mrrs = [experiments[key]["MRR"] for key in ("A", "B", "C", "D")]
        self.assertEqual(len(mrrs), 4)
        for value in mrrs:
            self.assertTrue(0.0 <= value <= 1.0)
        self.assertTrue(all(experiments[key]["queries"] > 0 for key in ("A", "B", "C", "D")))


# ===========================================================================
# 4. 评估主流程
# ===========================================================================
class TestEvaluationOutput(unittest.TestCase):
    def test_metrics_keys_and_ranges(self):
        metrics = _EVAL["metrics"]
        for name in ("Recall@1", "Recall@5", "Precision@1", "MRR", "正负样本区分准确率",
                     "可信认领验证准确率", "误通过率", "误拒绝率"):
            self.assertIn(name, metrics, f"缺少指标 {name}")
            self.assertGreaterEqual(metrics[name], 0.0)
            self.assertLessEqual(metrics[name], 1.0)
        latency = metrics["平均响应时间"]
        for key in ("mean_ms", "p50_ms", "p95_ms"):
            self.assertIn(key, latency)
            self.assertGreater(latency[key], 0.0)
        self.assertLessEqual(latency["p50_ms"], latency["p95_ms"])

    def test_meta_required_fields(self):
        meta = _EVAL["meta"]
        for key in ("model_version", "dataset_version", "dataset_size", "split", "weights",
                    "threshold", "top_k", "executed_at", "seed", "engine", "image_backend"):
            self.assertIn(key, meta, f"meta 缺少 {key}")
        self.assertEqual(meta["dataset_size"], {"items": 120, "matches": 60})
        self.assertEqual(meta["split"], {"train": 72, "val": 24, "test": 24})
        self.assertEqual(meta["threshold"], "MATCH_MIN_DISPLAY_SCORE=0.35")
        self.assertEqual(meta["seed"], SEED)
        self.assertEqual(meta["top_k"], 5)
        self.assertEqual(meta["engine"], "MatchEngine(baseline-1.0)")
        self.assertEqual(meta["image_backend"], "mock")
        self.assertEqual(sorted(meta["weights"]), sorted(DIMENSIONS))
        self.assertAlmostEqual(sum(meta["weights"].values()), 1.0, places=9)

    def test_error_cases_schema(self):
        error_cases = _EVAL["error_cases"]
        self.assertIsInstance(error_cases, list)
        for entry in error_cases:
            self.assertEqual(set(entry), _ALLOWED_ERROR_KEYS)
            self.assertIsNotNone(entry["sample_id"])
            self.assertIsNotNone(entry["lost_item_id"])
            self.assertIsNotNone(entry["expected_found_item_id"])
            self.assertIsInstance(entry["rank"], int)
            self.assertGreaterEqual(entry["rank"], 2)      # 只有非 Top-1 才算 error case

    def test_error_case_can_be_produced(self):
        """构造一个必然失败的排名（候选池里没有正确答案）→ rank=None。"""
        engine = MatchEngine()
        items_by_id = {int(item["id"]): item for item in _ITEMS}
        sample = next(m for m in _MATCHES if m.get("label") == "positive")
        lost, _found = E.sample_payloads(sample, items_by_id)
        pool = [E.item_payload(item) for item in _ITEMS
                if item.get("post_type") == "found" and str(item["id"]) != str(sample["found_item_id"])]
        rank, _scored, _top1, hit = E._rank_pool(engine, lost, pool, sample["found_item_id"], 5)
        self.assertIsNone(rank)
        self.assertFalse(hit)

    def test_split_filter_reduces_query_count(self):
        test_split = _SPLIT["summary"]["test"]
        result = E.evaluate(split_name="test", ablation="A", top_k=5, seed=SEED,
                            dataset=_DATASET, write_split_file=False)
        meta = result["eval"]["meta"]
        ranking = result["eval"]["metrics_detail"]["ranking"]
        self.assertGreater(ranking["queries"], 0, "测试集里应至少有 1 条正样本查询")
        self.assertLess(ranking["queries"], 24, "测试集查询数应少于全量")
        # 候选池固定为全部 found 物品（60 条），不随 --split 裁剪
        self.assertEqual(meta["candidate_pool"]["size"], 60)
        self.assertEqual(meta["candidate_pool"]["scope"], "all_found_items")
        self.assertEqual(meta["candidate_pool"]["query_scope"], "test")
        # 池子完整 → 正确答案应当能被召回（不是退化的 0.0）
        self.assertGreater(result["eval"]["metrics"]["Recall@5"], 0.0)
        self.assertLessEqual(ranking["queries"], test_split)

    def test_default_seed_and_paths(self):
        self.assertEqual(E.DEFAULT_SEED, 20260930)
        self.assertEqual(E.DEFAULT_TOP_K, 5)
        parser = E.build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.split, "all")
        self.assertEqual(args.ablation, "all")
        self.assertEqual(args.top_k, 5)
        self.assertEqual(args.seed, 20260930)
        self.assertFalse(args.quiet)
        # 输出路径应落在项目根的 data/eval/ 下（不写死盘符，跟随实际检出位置）
        self.assertTrue(args.output.replace("\\", "/").endswith(".json"))
        self.assertIn("/data/eval/eval_", args.output.replace("\\", "/"))


class TestCredibilityVerification(unittest.TestCase):
    """可信认领验证：全对 → low，全错 → high（评估里用内存构造的答案）。"""

    def test_constructs_questions_from_hidden_features(self):
        item = next(i for i in _ITEMS if i.get("hidden_features"))
        payload = E.item_payload(item)
        questions = E.build_verification_questions(payload, "T001")
        self.assertEqual(len(questions), len(item["hidden_features"]))
        for question in questions:
            self.assertIn("reference_answer", question)
            self.assertIn(question["reference_answer"], item["hidden_features"])

    def test_wrong_answers_are_same_category_but_different_item(self):
        item = next(i for i in _ITEMS if i.get("post_type") == "found" and i.get("hidden_features"))
        plans = E.wrong_answers_for(item, _ITEMS, count=3)
        self.assertTrue(plans)
        own = set(item["hidden_features"])
        for plan in plans:
            self.assertTrue(plan)
            for answer in plan:
                self.assertNotIn(answer, own)

    def test_wrong_answer_pool_excludes_same_variant(self):
        """回归：同一变体在数据集里有多条 found 记录，只按 id 排除会把正确答案当干扰答案。"""
        for item in _ITEMS:
            if item.get("post_type") != "found":
                continue
            own = tuple(item["hidden_features"])
            for plan in E.wrong_answers_for(item, _ITEMS, count=3):
                self.assertNotEqual(tuple(plan), own, f"{item['id']} 的干扰方案混入了自己的隐藏特征")

    def test_plan_answers_never_similar_to_reference(self):
        """回归：每个「全错」答案都必须与对应参考不相似（否则误通过率不可信）。"""
        checked = 0
        for item in _ITEMS:
            if item.get("post_type") != "found" or not item.get("hidden_features"):
                continue
            payload = E.item_payload(item)
            questions = E.build_verification_questions(payload, "T%04d" % item["id"])
            for plan in E.wrong_answers_for(item, _ITEMS, count=3):
                answers = E.plan_answers(plan, questions)
                self.assertEqual(len(answers), len(questions))
                for answer, question in zip(answers, questions):
                    self.assertFalse(E._is_similar(answer, str(question["reference_answer"])),
                                     f"{item['id']} 的干扰答案与参考相似：{answer!r}")
                    checked += 1
        self.assertGreater(checked, 100)

    def test_no_wrong_answer_judged_as_matched(self):
        """全错答案在单题层面**不允许**出现 matched=True（防冒领的核心断言）。"""
        scorer = CredibilityScorer()
        matched_count = 0
        for item in _ITEMS:
            if item.get("post_type") != "found" or not item.get("hidden_features"):
                continue
            payload = E.item_payload(item)
            questions = E.build_verification_questions(payload, "T%04d" % item["id"])
            for plan in E.wrong_answers_for(item, _ITEMS, count=3):
                for answer, question in zip(E.plan_answers(plan, questions), questions):
                    if scorer.score_answer(question, answer, question_id=question["id"])["matched"]:
                        matched_count += 1
        self.assertEqual(matched_count, 0, "存在被误判为命中的错误答案")

    def test_wrong_answers_never_land_in_low_risk(self):
        """防冒领红线（需求 §13.1 F09）：答错不得被判为 low（直接通过）。"""
        scorer = CredibilityScorer()
        for item in _ITEMS:
            if item.get("post_type") != "found" or not item.get("hidden_features"):
                continue
            payload = E.item_payload(item)
            questions = E.build_verification_questions(payload, "T%04d" % item["id"])
            for plan in E.wrong_answers_for(item, _ITEMS, count=3):
                answers = E.plan_answers(plan, questions)
                result = E.credibility_case(scorer, questions, answers, 0.9, payload)
                self.assertNotEqual(result["risk_level"], "low",
                                    f"{item['id']} 的错误答案被判为 low：{result}")

    def test_credibility_case_returns_no_answer_text(self):
        scorer = CredibilityScorer()
        found = next(i for i in _ITEMS if i.get("post_type") == "found" and i.get("hidden_features"))
        payload = E.item_payload(found)
        questions = E.build_verification_questions(payload, "T002")
        correct = [q["reference_answer"] for q in questions]
        result = E.credibility_case(scorer, questions, correct, 0.9, payload)
        self.assertEqual(set(result), {"credibility_score", "risk_level", "answer_match_score", "behavior_score"})
        self.assertIn(result["risk_level"], ("low", "medium", "high"))
        self.assertEqual(result["risk_level"], "low")
        for answer in correct:
            self.assertNotIn(answer, json.dumps(result, ensure_ascii=False))

    def test_all_wrong_answers_are_high_risk(self):
        scorer = CredibilityScorer()
        found = next(i for i in _ITEMS if i.get("post_type") == "found" and i.get("hidden_features"))
        payload = E.item_payload(found)
        questions = E.build_verification_questions(payload, "T003")
        plans = E.wrong_answers_for(found, _ITEMS, count=3)
        self.assertTrue(plans)
        levels = set()
        for plan in plans:
            answers = E.plan_answers(plan, questions)
            levels.add(E.credibility_case(scorer, questions, answers, 0.9, payload)["risk_level"])
        self.assertEqual(levels, {"high"})


class TestKeyTokenVerification(unittest.TestCase):
    """判别性 token 强校验（关键字段必须逐字对上，但不破坏同义容忍）。"""

    @classmethod
    def setUpClass(cls):
        cls.scorer = CredibilityScorer()

    def _tokens(self, text):
        return self.scorer.discriminating_tokens(text)

    def _matched(self, answer, reference, mode="fuzzy_with_synonyms"):
        result = self.scorer.score_answer({"reference_answer": reference, "match_mode": mode}, answer)
        return result["matched"], result["score"], result.get("diagnostic", "")

    def test_extracts_labeled_and_serial_tokens(self):
        tokens = self._tokens("学号 2023XXXXXX，姓名 张某某")
        # 掩码折叠成 "*"，并做混淆字符归一（0→o），因此归一化形式为 2o23*
        self.assertIn("2o23*", tokens)
        # 姓名里的掩码同样折叠：判别力落在真实姓氏上（张* ≠ 王*）
        self.assertIn("张*", tokens)
        self.assertNotIn("王*", tokens)
        # 混淆字符归一（0→o、1→l）与掩码折叠对所有 token 一致生效
        self.assertIn("o42l", self._tokens("书脊内侧写有学号后四位 0421"))
        # 数字串同样过混淆归一（512 → 5l2）：两侧口径一致，不同取值仍然判为不同
        self.assertIn("5l2", self._tokens("杯底标签写着房间号 512"))
        self.assertNotIn("5l2", self._tokens("杯底标签写着房间号 513"))
        # 拉丁字母数字（设备名）整体作为一个 token，且过混淆归一（1→l）
        self.assertTrue(any("882l" in token for token in self._tokens("蓝牙名称 HUAWEI-FreeBuds-5i-8821")))
        self.assertNotIn("yiqiairpods", self._tokens("蓝牙名称 HUAWEI-FreeBuds-5i-8821"))

    def test_plain_paraphrase_has_no_tokens(self):
        """纯文字同义改写不应抽出任何 token（保证模糊匹配路径不被拦截）。"""
        self.assertEqual(self._tokens("盒体右侧有一道划痕"), set())
        self.assertEqual(self._tokens("右边刮了一道"), set())

    def test_short_numbers_are_not_treated_as_serials(self):
        """「3厘米」这类量词不是编号，不能被当成判别性 token。"""
        self.assertNotIn("3", self._tokens("盒盖内侧有一道3厘米划痕"))

    def test_wrong_student_id_is_mismatch(self):
        """根因回归：复合式证件条目答错学号/姓名必须判不命中且不给部分分。"""
        reference = "学号 2023XXXXXX，姓名 赵某某"
        for answer in ("学号 2022XXXXXX，姓名 王某某",
                       "学号 2024XXXXXX，姓名 陈某某",
                       "学号 2023XXXXXX，姓名 张某某"):
            matched, score, diagnostic = self._matched(answer, reference)
            self.assertFalse(matched, f"{answer!r} 不应命中")
            self.assertAlmostEqual(score, 0.0, delta=1e-6, msg="关键字段不一致不得给部分分")
            self.assertEqual(diagnostic, "key_token_mismatch")

    def test_wrong_page_number_is_mismatch(self):
        matched, score, _diag = self._matched("第 15 页边角有咖啡渍", "第 42 页夹有一张公交卡大小的书签")
        self.assertFalse(matched)
        self.assertAlmostEqual(score, 0.0, delta=1e-6)

    def test_same_tokens_are_compatible(self):
        reference = "学号 2023XXXXXX，姓名 张某某"
        for answer in ("学号2023XXXXXX，姓名张某某", "学号 2023xxxxxx 姓名 张某某",
                       "学号 2023XXXXXX"):
            matched, _score, _diag = self._matched(answer, reference)
            self.assertTrue(matched, f"{answer!r} 应命中（同义/省略表达）")

    def test_subset_answers_are_compatible(self):
        """回答得更具体（参考没有编号）时不能被误杀。"""
        self.assertTrue(self.scorer.key_tokens_compatible("杯底标签写着房间号 512", "杯底有标签"))

    def test_synonym_tolerance_not_degraded(self):
        """同义表达容忍度不得退化（约束项）：含「刮」/「划」等价表达。"""
        cases = [
            ("盒体右侧有划痕", "盒体右侧有一道划痕"),
            ("盒子右边刮了一道", "盒体右侧有一道划痕"),
            ("右边刮了一道", "盒体右侧有一道划痕"),
            ("右侧有一道刮痕", "盒体右侧有一道划痕"),
            ("盒体右侧有一道划伤", "盒体右侧有一道划痕"),
            ("右面有一道划痕", "盒体右侧有一道划痕"),
        ]
        for answer, reference in cases:
            matched, score, _diag = self._matched(answer, reference)
            self.assertTrue(matched, f"{answer!r} vs {reference!r} 应命中同义表达")
            self.assertAlmostEqual(score, 1.0, delta=1e-6)

    def test_contradictions_still_rejected(self):
        for answer, reference in (("没有划痕", "盒体右侧有一道划痕"),
                                  ("左侧有划痕", "盒体右侧有一道划痕"),
                                  ("红色保温杯", "蓝色保温杯")):
            matched, _score, _diag = self._matched(answer, reference)
            self.assertFalse(matched, f"{answer!r} 不应命中")

    def test_key_token_check_can_be_disabled_by_config(self):
        """配置可关闭强校验（用于回归对比），说明这是规则而非写死的逻辑。"""
        base = load_config()
        questions = json.loads(json.dumps(base.questions, ensure_ascii=False))
        questions.setdefault("answer_matching", {}).setdefault("key_tokens", {})["enabled"] = False
        variant = RuntimeConfig(matching=base.matching, locations=base.locations,
                                questions=questions, warnings=[], sources=dict(base.sources))
        scorer = CredibilityScorer(variant)
        self.assertEqual(scorer.discriminating_tokens("学号 2023XXXXXX，姓名 张某某"), set())
        result = scorer.score_answer(
            {"reference_answer": "学号 2023XXXXXX，姓名 赵某某"}, "学号 2024XXXXXX，姓名 陈某某")
        self.assertTrue(result["matched"], "关闭强校验后应回到旧的模糊匹配行为")


# ===========================================================================
# 5. 安全红线：输出中不得出现 hidden_features 原文
# ===========================================================================
class TestNoHiddenFeatureLeak(unittest.TestCase):
    HIDDEN = None

    @classmethod
    def setUpClass(cls):
        cls.HIDDEN = _hidden_feature_texts()
        assert len(cls.HIDDEN) > 0, "数据集里必须有 hidden_features 才能做泄露检测"

    def _assert_no_leak(self, payload, label):
        strings = _iter_strings(payload)
        self.assertGreater(len(strings), 10)
        for path, text in strings:
            for feature in self.HIDDEN:
                self.assertNotIn(feature, text, f"{label} 的 {path} 泄露了隐藏特征原文：{feature}")

    def test_eval_output_has_no_hidden_features(self):
        self._assert_no_leak(_EVAL, "eval 输出")

    def test_ablation_output_has_no_hidden_features(self):
        self._assert_no_leak(_ABLATION, "ablation 输出")

    def test_split_output_has_no_hidden_features(self):
        self._assert_no_leak(_SPLIT, "testset_split")

    def test_eval_output_has_no_item_payload_dump(self):
        """输出里不应出现物品描述/标题等原文（只允许 id、分数、指标）。"""
        blob = json.dumps(_EVAL, ensure_ascii=False)
        for item in _ITEMS[:20]:
            title = str(item.get("title") or "")
            description = str(item.get("description") or "")
            if len(title) >= 3:
                self.assertNotIn(title, blob)
            if len(description) >= 12:
                self.assertNotIn(description[:12], blob)

    def test_no_reference_answers_written(self):
        """可信度题目对象（含 reference_answer）绝不进入产物。"""
        blob = json.dumps(_EVAL, ensure_ascii=False) + json.dumps(_ABLATION, ensure_ascii=False)
        self.assertNotIn("reference_answer", blob)
        self.assertNotIn("hidden_features", blob)


# ===========================================================================
# 6. 数据一致性（只报告，不掩盖）
# ===========================================================================
class TestDatasetConsistency(unittest.TestCase):
    """三方一致性：文件 `reference_score` == 数据生成器重算 == 评估脚本实时重算。

    背景（2026-10-13 修复的缺陷）：`evaluate.py::item_payload` 早期多传了显式
    ``keywords``，导致同一组样本在「数据生成脚本」与「评估脚本」下文本维度口径不同，
    28/60 条负样本得分不一致（例如 M016：0.7056 vs 0.6556）。修复后三方完全一致，
    本用例把该不变量**写成硬断言**，以后任何口径漂移都会立即失败。

    容差 5e-5：``reference_score`` 在 JSON 里以 4 位小数存储，而引擎返回全精度。
    """

    #: 允许的最大偏差（4 位小数存储带来的取整误差上界）
    TOLERANCE = 5e-5

    def _scores(self):
        items_by_id = {int(item["id"]): item for item in _ITEMS}
        engine = MatchEngine()
        rows = []
        for sample in _MATCHES:
            lost, found = E.sample_payloads(sample, items_by_id)
            evaluate_score = engine.match_pair(lost, found).total_score
            generator_score = G._score_sample(engine, sample, items_by_id)
            file_score = float(sample["reference_score"])
            rows.append((sample["sample_id"], sample.get("label"),
                         file_score, evaluate_score, generator_score))
        return rows

    def test_file_reference_matches_engine_exactly(self):
        """文件里的 reference_score 必须等于当前引擎实时重算值（全部 60 条）。"""
        offenders = []
        for sample_id, _label, file_score, evaluate_score, _generator in self._scores():
            if abs(evaluate_score - file_score) > self.TOLERANCE:
                offenders.append((sample_id, file_score, evaluate_score))
        self.assertEqual(offenders, [],
                         f"{len(offenders)} 条样本的 reference_score 与实时重算不一致"
                         f"（示例 {offenders[:3]}）：请重跑 scripts/generate_demo_data.py")

    def test_generator_and_evaluate_agree_exactly(self):
        """数据生成脚本与评估脚本对同一组样本必须给出完全相同的分数与入参口径。"""
        offenders = []
        for sample_id, _label, _file_score, evaluate_score, generator_score in self._scores():
            if abs(evaluate_score - generator_score) > 1e-12:
                offenders.append((sample_id, generator_score, evaluate_score))
        self.assertEqual(offenders, [], f"生成器与评估脚本口径不一致：{offenders[:3]}")

    def test_payload_builders_are_identical(self):
        """两侧的入参构造函数必须逐字段一致（本次缺陷的直接回归测试）。"""
        by_id = {int(item["id"]): item for item in _ITEMS}
        for item in _ITEMS:
            self.assertEqual(E.item_payload(item), G._to_payload(item),
                             f"item {item['id']} 的入参构造与生成脚本不一致")
        for sample in _MATCHES:
            for side, item_key, view_key in (("lost", "lost_item_id", "lost_view"),
                                             ("found", "found_item_id", "found_view")):
                item = by_id[int(sample[item_key])]
                view = sample.get(view_key)
                if view is None:
                    continue      # 生成脚本的 _apply_view 不接受 None，与 evaluate 等价
                self.assertEqual(G._apply_view(G._to_payload(item), view),
                                 E.apply_view(E.item_payload(item), view),
                                 f"{sample['sample_id']} {side} 的视图覆盖口径不一致")

    def test_payload_does_not_pass_explicit_keywords(self):
        """入参不得携带 keywords：关键词必须由 MatchEngine 自行抽取，否则文本维度口径会漂移。"""
        for item in _ITEMS[:30]:
            self.assertNotIn("keywords", E.item_payload(item))

    def test_score_ranges_by_label(self):
        """三类分数区间必须不重叠（数据集的可用性前提）。"""
        buckets = {}
        for _sample_id, label, _file_score, evaluate_score, _generator in self._scores():
            buckets.setdefault(label, []).append(evaluate_score)
        positive = buckets["positive"]
        hard = buckets["hard_negative"]
        negative = buckets["negative"]
        self.assertGreater(min(positive), max(hard), "正样本与困难负样本分数重叠")
        self.assertGreater(min(hard), max(negative), "困难负样本与普通负样本分数重叠")

    def test_expected_range_covers_reference_score(self):
        """expected_total_score_range 必须覆盖自己写入的 reference_score（数据自洽性）。"""
        for sample in _MATCHES:
            low, high = (float(x) for x in sample["expected_total_score_range"])
            self.assertLessEqual(low, float(sample["reference_score"]) + 1e-9, sample["sample_id"])
            self.assertGreaterEqual(high, float(sample["reference_score"]) - 1e-9, sample["sample_id"])

    def test_mock_data_agrees_with_dataset_files(self):
        """内存构造的数据与磁盘 JSON 内容一致（保证两种加载路径等价）。"""
        dataset = mock_data.build_demo_dataset()
        self.assertEqual(len(dataset["items"]), len(_ITEMS))
        self.assertEqual(len(dataset["matches"]), len(_MATCHES))
        by_id = {int(item["id"]): item for item in _ITEMS}
        for item in dataset["items"]:
            disk = by_id[int(item["id"])]
            self.assertEqual(item["hidden_features"], disk["hidden_features"])
            self.assertEqual(item["public_features"], disk["public_features"])
            self.assertEqual(item["image_url"], disk["image_url"])


class TestEvaluateDeterminism(unittest.TestCase):
    """同一输入 + 同一种子 → 除 executed_at 外完全一致（答辩现场可复现）。"""

    maxDiff = None

    def test_second_run_matches(self):
        again = E.evaluate(split_name="all", ablation="A", top_k=3, seed=SEED,
                           dataset=_DATASET, write_split_file=False)
        first = copy.deepcopy(_EVAL)
        second = copy.deepcopy(again["eval"])
        first["meta"].pop("executed_at", None)
        second["meta"].pop("executed_at", None)
        # eval_split/ablation/top_k 属于本次运行的参数，排除后比较其余部分
        for payload in (first, second):
            payload["meta"].pop("eval_split", None)
            payload["meta"].pop("ablation", None)
            payload["meta"].pop("top_k", None)
            payload["metrics"].pop("平均响应时间", None)
            payload["metrics_detail"].pop("latency", None)
            # top_k 不同 → 指标键名不同（Recall@K），排除后比较其余指标
            payload["metrics"].pop("Recall@5", None)
            payload["metrics"].pop("Recall@3", None)
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True),
                         json.dumps(second, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
