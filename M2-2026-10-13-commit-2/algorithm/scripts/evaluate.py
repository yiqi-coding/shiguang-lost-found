"""算法评估主脚本（M2：评估脚本 + 9 项指标 + 测试集划分 + A/B/C/D 消融实验）。

对应需求说明书 §9.2（数据集划分）、§9.3（算法指标）、§9.4（消融实验）。

用法::

    cd <仓库根>/algorithm
    python -X utf8 scripts\\evaluate.py                      # 全量数据集 + 四组消融
    python -X utf8 scripts\\evaluate.py --split test          # 只在测试集上评估
    python -X utf8 scripts\\evaluate.py --ablation all        # 显式跑 A/B/C/D
    python -X utf8 scripts\\evaluate.py --ablation A --quiet  # 只跑 A 组且不打印

产物（全部是确定性产物，同输入同输出；目录由仓库位置推导，见 DEFAULT_OUTPUT_DIR）::

    <仓库根>/data/eval/eval_<YYYYMMDD>.json        # 9 项指标 + error_cases + meta
    <仓库根>/data/eval/ablation_<YYYYMMDD>.json    # A/B/C/D 对比 + 相对 A 的提升
    <仓库根>/data/eval/testset_split.json          # 120 条物品的 train/val/test 归属

硬性约束（本脚本遵守）
----------------------
* **纯标准库**（不得依赖 numpy/pandas/yaml/pydantic/fastapi）。
* **确定性**：不使用 `random`，划分用「固定种子 + 稳定哈希」派生的 `random.Random`
  实例；报告里的时间字段属于运行元信息，不影响任何指标数值。
* **安全红线**：`hidden_features` 只用于在内存里构造可信度验证答案，
  任何输出文件（eval / ablation / split）都**不得**出现隐藏特征原文，
  也不得把标准答案写进产物。
* **不侵入现有代码**：不改 `app/matcher.py`；消融实验取 `outcome.scores` 六维分后
  按「维度子集 + 归一化权重」重算总分。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import sys
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --- 路径引导：允许 `python scripts\evaluate.py` 直接运行 ---------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.config_loader import DIMENSIONS, RuntimeConfig, load_config  # noqa: E402
from app.credibility import CredibilityScorer  # noqa: E402
from app.matcher import MatchEngine, MatchOutcome  # noqa: E402
from app.mock_data import build_demo_dataset  # noqa: E402
from app.text_utils import ngram_similarity, normalize  # noqa: E402

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_SEED = 20260930
DEFAULT_TOP_K = 5
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(_ROOT), "data", "eval")

#: 划分比例（需求 §9.2：60% / 20% / 20%）
SPLIT_RATIOS: Tuple[Tuple[str, float], ...] = (("train", 0.60), ("val", 0.20), ("test", 0.20))

#: 阈值信息：与 config/matching.yaml 的展示阈值/契约表述保持一致
THRESHOLD_NOTE = "MATCH_MIN_DISPLAY_SCORE=0.35"

#: 消融实验定义：实验 → (使用维度, 说明)。原始权重一律取自 config/matching.yaml。
ABLATIONS: Dict[str, Dict[str, Any]] = {
    "A": {
        "dimensions": ("category",),
        "title": "仅类别/关键词",
        "description": "只用类别维度，检验「类别过滤」本身的检索能力",
    },
    "B": {
        "dimensions": ("category", "text"),
        "title": "类别 + 文本属性",
        "description": "加入文本/公开属性比对，检验细节描述的贡献",
    },
    "C": {
        "dimensions": ("category", "text", "time", "location"),
        "title": "类别 + 文本 + 时间地点",
        "description": "在 B 的基础上加入时间与地点合理度",
    },
    "D": {
        "dimensions": DIMENSIONS,
        "title": "完整模型（图文 + 时间地点 + 类别 + 属性）",
        "description": "契约冻结的六维权重，等价于 MatchEngine 的 total_score",
    },
}

_ABLATION_ORDER = ("A", "B", "C", "D")


# ===========================================================================
# 一、数据集加载（优先磁盘 JSON，回退内存构造，两者内容一致）
# ===========================================================================
def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_dataset(data_dir: Optional[str] = None) -> Dict[str, Any]:
    """加载 120 条物品 + 60 组标注样本。

    优先读取 ``data/demo_items.json`` / ``data/demo_matches.json``；
    文件缺失时回退到 :func:`app.mock_data.build_demo_dataset`（同源同内容）。
    """
    root = data_dir or os.path.join(_ROOT, "data")
    items_path = os.path.join(root, "demo_items.json")
    matches_path = os.path.join(root, "demo_matches.json")
    source = "memory:mock_data.build_demo_dataset"
    if os.path.exists(items_path) and os.path.exists(matches_path):
        items = _read_json(items_path)
        matches = _read_json(matches_path)
        source = "file:data/demo_items.json+data/demo_matches.json"
    else:
        dataset = build_demo_dataset()
        items, matches = dataset["items"], dataset["matches"]
    if not isinstance(items, list) or not isinstance(matches, list) or not items or not matches:
        dataset = build_demo_dataset()
        items, matches = dataset["items"], dataset["matches"]
        source = "memory:mock_data.build_demo_dataset(fallback)"
    return {
        "items": items,
        "matches": matches,
        "source": source,
        "meta": {
            "model_version": "baseline-1.0",
            "dataset_version": "demo-2026.09.30",
            "items": len(items),
            "matches": len(matches),
        },
    }


# ===========================================================================
# 二、测试集划分（需求 §9.2，确定性 60/20/20）
# ===========================================================================
def _stable_hash(*parts: Any) -> int:
    """稳定哈希（blake2b，不受 PYTHONHASHSEED 影响）。"""
    digest = hashlib.blake2b("|".join(str(p) for p in parts).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def deterministic_shuffle(values: Sequence[Any], seed: int, *salt: Any) -> List[Any]:
    """确定性洗牌：同一 (values, seed, salt) 永远得到同一顺序，不使用全局随机状态。"""
    rng = random.Random(_stable_hash(seed, *salt))
    shuffled = list(values)
    rng.shuffle(shuffled)
    return shuffled


def _allocate(count: int, ratios: Sequence[Tuple[str, float]] = SPLIT_RATIOS) -> Dict[str, int]:
    """按比例分配数量，余数按比例小数部分从大到小补齐（保证总数不变）。"""
    if count <= 0:
        return {name: 0 for name, _ in ratios}
    exact = [(name, count * ratio) for name, ratio in ratios]
    allocation = {name: int(value) for name, value in exact}
    remainder = count - sum(allocation.values())
    for name, value in sorted(exact, key=lambda kv: (-(kv[1] - int(kv[1])), kv[0])):
        if remainder <= 0:
            break
        allocation[name] += 1
        remainder -= 1
    return allocation


def build_split(items: Sequence[Dict[str, Any]], seed: int = DEFAULT_SEED) -> Dict[str, Any]:
    """把物品划分为 train/val/test。

    算法（确定性，可复现）：

    1. 按 ``(category, post_type)`` 分组（6 类别 × 失物/拾物 = 12 组，每组 10 条）；
    2. 组内按 ``id`` 升序；
    3. 用 ``seed`` + 组键派生的固定种子做一次洗牌；
    4. 洗牌后按 6 / 2 / 2 位置切片 → train / val / test。

    分组内划分保证每个子集都覆盖全部 6 个类别与两种 post_type；
    同一条物品只属于一个子集（无重叠、并集为全集）。
    """
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for item in items:
        groups.setdefault((str(item.get("category") or ""), str(item.get("post_type") or "")), []).append(item)

    assignment: Dict[str, str] = {}
    group_details: List[Dict[str, Any]] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda entry: str(entry.get("id")))
        order = deterministic_shuffle(members, seed, key[0], key[1])
        allocation = _allocate(len(order))
        cursor = 0
        counts: Dict[str, int] = {}
        for name, _ratio in SPLIT_RATIOS:
            size = allocation.get(name, 0)
            for item in order[cursor:cursor + size]:
                assignment[str(item.get("id"))] = name
            counts[name] = size
            cursor += size
        group_details.append({
            "group": f"{key[0]}/{key[1]}",
            "size": len(order),
            "counts": counts,
            "order": [str(item.get("id")) for item in order],
        })

    summary = {name: sum(1 for value in assignment.values() if value == name) for name, _ in SPLIT_RATIOS}
    return {
        "seed": int(seed),
        "ratios": {name: ratio for name, ratio in SPLIT_RATIOS},
        "assignment": assignment,
        "summary": summary,
        "groups": group_details,
        "method": (
            "按 (category, post_type) 分组 → 组内 id 升序 → 用 seed 与组键派生的固定随机种子做 "
            "random.Random(...).shuffle 确定性洗牌 → 按 60%/20%/20% 位置切片；不使用全局随机状态，"
            "同一种子重复执行结果完全一致。"
        ),
        "note": "测试集只用于最终报告与回归验证；调权重/改规则时禁止反复使用测试集（需求 §9.2）。",
    }


def split_count(items: Sequence[Dict[str, Any]], split: Dict[str, Any]) -> Dict[str, int]:
    summary = split.get("summary") or {}
    return {name: int(summary.get(name, 0)) for name, _ in SPLIT_RATIOS}


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


# ===========================================================================
# 三、样本 → 引擎入参（与 scripts/generate_demo_data.py 的构造方式一致）
# ===========================================================================
def item_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """把数据集记录转成 ``match_pair`` 的入参形态。

    ⚠ **必须与 ``scripts/generate_demo_data.py::_to_payload`` 完全一致**：
    入参里**不传** ``keywords``，让 :class:`MatchEngine` 按引擎自身规则从描述里抽取关键词。
    早期版本这里多传了显式 ``keywords``，导致同一条样本在「评估脚本」与
    「数据生成脚本」下得分不同（文本维度口径不一致），必须保持一致才能让
    `reference_score`、实时重算与评估输出三方对齐。
    """
    return {
        "id": item.get("id"),
        "category": item.get("category"),
        "title": item.get("title"),
        "description": item.get("description"),
        "attributes": {
            "color": item.get("color"),
            "brand": item.get("brand"),
            "model": item.get("model"),
            "material": item.get("material"),
            "features": list(item.get("public_features") or []),
        },
        "location": item.get("location"),
        "event_time": item.get("event_time"),
        "image_url": item.get("image_url"),
        "public_features": list(item.get("public_features") or []),
        "hidden_features": list(item.get("hidden_features") or []),
    }


def apply_view(payload: Dict[str, Any], view: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把 hard_negative 的 ``lost_view`` / ``found_view`` 覆盖到入参上。

    与 ``scripts/generate_demo_data.py::_apply_view`` 保持等价（同一套覆盖口径）。
    """
    if not view:
        return payload
    updated = dict(payload)
    attributes = dict(updated.get("attributes") or {})
    for key in ("color", "brand", "model"):
        if key in view:
            attributes[key] = view[key]
            updated[key] = view[key]
    if view.get("public_features") is not None:
        attributes["features"] = list(view["public_features"])
        updated["public_features"] = list(view["public_features"])
    if view.get("hidden_features") is not None:
        updated["hidden_features"] = list(view["hidden_features"])
    updated["attributes"] = attributes
    return updated


def sample_payloads(sample: Dict[str, Any], items_by_id: Dict[int, Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """还原一组标注样本的实际入参（含时间/地点/困难负样本视图覆盖）。

    覆盖口径与 ``scripts/generate_demo_data.py::_score_sample`` **逐字段一致**：
    视图覆盖 → 再写入样本记录的 ``lost_event_time`` / ``found_event_time`` /
    ``location_lost`` / ``location_found``。样本里记录的时间与地点才是评分的真实输入
    （物品自身的 ``event_time`` 是发布展示时间，不能用来打分）。
    """
    lost_item = items_by_id[int(sample["lost_item_id"])]
    found_item = items_by_id[int(sample["found_item_id"])]
    lost = apply_view(item_payload(lost_item), sample.get("lost_view"))
    found = apply_view(item_payload(found_item), sample.get("found_view"))
    lost["event_time"] = sample.get("lost_event_time") or lost.get("event_time")
    found["event_time"] = sample.get("found_event_time") or found.get("event_time")
    lost["location"] = sample.get("location_lost") or lost.get("location")
    found["location"] = sample.get("location_found") or found.get("location")
    return lost, found


# ===========================================================================
# 四、消融实验：维度子集 + 权重重新归一化
# ===========================================================================
def normalized_ablation_weights(weights: Dict[str, float], dimensions: Sequence[str]) -> Dict[str, float]:
    """按剩余维度重新归一化权重，保证和为 1.0（总分仍在 [0,1]）。"""
    selected = {dim: float(weights.get(dim, 0.0)) for dim in dimensions}
    total = sum(selected.values())
    if total <= 0:
        return {dim: 0.0 for dim in dimensions}
    return {dim: value / total for dim, value in selected.items()}


def ablation_score(scores: Dict[str, float], weights: Dict[str, float], dimensions: Sequence[str]) -> float:
    """用「实验维度子集 + 归一化权重」重算总分（不修改 matcher）。"""
    normalized = normalized_ablation_weights(weights, dimensions)
    return float(sum(float(scores.get(dim, 0.0)) * normalized.get(dim, 0.0) for dim in dimensions))


# ===========================================================================
# 五、指标计算（9 项）
# ===========================================================================
def rank_metrics(ranks: Sequence[Optional[int]], top_k: int = DEFAULT_TOP_K) -> Dict[str, Any]:
    """由「正确答案排名」序列计算 Recall@1 / Recall@5 / Precision@1 / MRR。"""
    total = len(ranks)
    hits = [rank for rank in ranks if isinstance(rank, int) and rank > 0]
    return {
        "queries": total,
        "Recall@1": (sum(1 for rank in hits if rank <= 1) / total) if total else 0.0,
        f"Recall@{top_k}": (sum(1 for rank in hits if rank <= top_k) / total) if total else 0.0,
        # 在「单条正确答案」的评估协议下，Top-1 命中即 Precision@1（等价于 Recall@1）
        "Precision@1": (sum(1 for rank in hits if rank == 1) / total) if total else 0.0,
        "MRR": (sum(1.0 / rank for rank in hits) / total) if total else 0.0,
        "hit_count": len(hits),
        "miss_count": total - len(hits),
        "median_rank": float(statistics.median(hits)) if hits else 0.0,
        "mean_rank": (sum(hits) / len(hits)) if hits else 0.0,
    }


def discrimination_accuracy(
    positives: Sequence[float],
    negatives: Sequence[float],
) -> Dict[str, Any]:
    """正负样本区分准确率：所有 (正样本对, 负样本对) 组合中正样本分更高的比例。

    并列（分数完全相等）按「未正确区分」计（half credit 不采用，避免掩盖问题）。
    """
    total = len(positives) * len(negatives)
    wins = 0
    ties = 0
    for positive in positives:
        for negative in negatives:
            if positive > negative:
                wins += 1
            elif positive == negative:
                ties += 1
    return {
        "pairs": total,
        "wins": wins,
        "ties": ties,
        "accuracy": (wins / total) if total else 0.0,
    }


def _percentile(values: Sequence[float], ratio: float) -> float:
    """最近秩法百分位（确定性，不插值）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = int(round(ratio * (len(ordered) - 1)))
    index = max(0, min(len(ordered) - 1, index))
    return float(ordered[index])


def latency_metrics(samples_ms: Sequence[float]) -> Dict[str, Any]:
    """平均响应时间：mean / p50 / p95（毫秒）。"""
    if not samples_ms:
        return {"mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "samples": 0}
    return {
        "mean_ms": round(statistics.fmean(samples_ms), 3),
        "p50_ms": round(_percentile(samples_ms, 0.50), 3),
        "p95_ms": round(_percentile(samples_ms, 0.95), 3),
        "max_ms": round(max(samples_ms), 3),
        "samples": len(samples_ms),
    }


# ===========================================================================
# 六、可信度验证（答案在内存中构造，绝不写入产物）
# ===========================================================================
def build_verification_questions(
    found_payload: Dict[str, Any],
    sample_id: str,
) -> List[Dict[str, Any]]:
    """按隐藏特征构造验证题目。

    注意：``reference_answer`` 只存在于内存中的题目对象里，
    :class:`CredibilityScorer` 的返回结构不含答案原文，本脚本也不输出题目内容。
    """
    questions: List[Dict[str, Any]] = []
    for index, feature in enumerate(found_payload.get("hidden_features") or []):
        text = str(feature).strip()
        if not text:
            continue
        questions.append({
            "id": f"{sample_id}-h{index + 1}",
            "question": "请描述该物品不对外公开的细节",
            "reference_answer": text,
            "match_mode": "fuzzy_with_synonyms",
            "weight": 1.0,
        })
    return questions


#: 干扰答案兜底文案：**必定与任何隐藏特征都不相似**。
#: 用途：当同类别干扰素材用尽时，用「记不清了」这种真实存在的失败回答补位，
#: 而不是循环复用其它答案（复用会把「正确答案」重新塞回干扰集，等于自欺欺人）。
_FALLBACK_WRONG_ANSWER = "记不清了，没什么特别的"


def wrong_answers_for(
    found_item: Dict[str, Any],
    items: Sequence[Dict[str, Any]],
    count: int = 3,
) -> List[List[str]]:
    """构造「全错」答案：取同类别**其它变体**物品的隐藏特征（真实存在但与本题不符）。

    构造约束（保证「全错」名副其实）：

    1. 排除同一件物品（同 ``id``）与**同一变体**（同 ``category + variant_index + post_type``）。
       数据集里同一变体对应多条 found 记录，只比 id 会把「正确答案」当成干扰答案；
    2. 每个方案都满足「每个答案与对应参考不相似」，由 :func:`plan_answers` 保证；
    3. 答案内容只留在内存里，不写入任何产物。
    """
    category = str(found_item.get("category") or "")
    variant = found_item.get("variant_index")
    post_type = found_item.get("post_type")
    pool: List[List[str]] = []
    for item in items:
        if str(item.get("category") or "") != category:
            continue
        if item.get("post_type") != post_type:
            continue
        if str(item.get("id")) == str(found_item.get("id")):
            continue
        if variant is not None and item.get("variant_index") == variant:
            continue                      # 同一件物品的另一条记录 → 特征完全相同
        features = [str(x) for x in (item.get("hidden_features") or []) if str(x).strip()]
        if features:
            pool.append(features)
    if not pool:
        return []
    plans: List[List[str]] = []
    for offset in range(min(count, len(pool))):
        plans.append(list(pool[offset]))
    return plans


def _is_similar(text_a: str, text_b: str) -> bool:
    """两段文本是否高度相似（用引擎自身的字符 n-gram Dice，口径与 partial 阈值一致）。"""
    key_a, key_b = normalize(text_a), normalize(text_b)
    if not key_a or not key_b:
        return False
    if key_a in key_b or key_b in key_a:
        return True
    return ngram_similarity(key_a, key_b, [2, 3], "dice") >= 0.34


def plan_answers(
    plan: Sequence[str],
    questions: Sequence[Dict[str, Any]],
    used: Optional[set] = None,
) -> List[str]:
    """把「干扰答案素材」逐题映射成答案：保证每个答案都与该题参考**不相似**。

    优先取未被占用且与参考不相似的干扰特征；取不到就用兜底文案补位。
    这样「全错」方案里不可能混进正确答案（本函数是误通过率可信度的前提）。
    """
    answers: List[str] = []
    taken = used if used is not None else set()
    for question in questions:
        reference = str(question.get("reference_answer") or "")
        picked = ""
        for index, candidate in enumerate(plan):
            text = str(candidate)
            if index in taken or not text.strip():
                continue
            if _is_similar(text, reference):
                continue
            picked = text
            taken.add(index)
            break
        answers.append(picked or _FALLBACK_WRONG_ANSWER)
    return answers


def credibility_case(
    scorer: CredibilityScorer,
    questions: Sequence[Dict[str, Any]],
    answers: Sequence[Any],
    candidate_score: float,
    target_item: Dict[str, Any],
) -> Dict[str, Any]:
    """跑一次可信度评分；只返回分值与等级，不含任何答案文本。"""
    result = scorer.score(
        questions=list(questions),
        answers=list(answers),
        candidate_match={"total_score": float(candidate_score)},
        target_item=target_item,
    )
    return {
        "credibility_score": round(float(result.credibility_score), 4),
        "risk_level": str(result.risk_level),
        "answer_match_score": round(float(result.answer_match_score), 4),
        "behavior_score": round(float(result.behavior_score), 4),
    }


# ===========================================================================
# 七、评估主流程
# ===========================================================================
def _rank_pool(
    engine: MatchEngine,
    query_payload: Dict[str, Any],
    pool_payloads: Sequence[Dict[str, Any]],
    expected_id: Any,
    top_k: int,
    reuse: Optional[List[Tuple[Any, float, Dict[str, float]]]] = None,
) -> Tuple[Optional[int], List[Tuple[Any, float, Dict[str, float]]], float, bool]:
    """给候选池打分并排名；返回 (正确答案排名, 排序结果, 首名分数, 是否命中)。

    排名定义：并列分按 ``id`` 升序决出先后（与 ``MatchEngine.match`` 的排序一致），
    正确答案与首名同分时仍记为第 1。
    """
    scored: List[Tuple[Any, float, Dict[str, float]]] = []
    if reuse is not None:
        scored = list(reuse)
    else:
        for payload in pool_payloads:
            outcome: MatchOutcome = engine.match_pair(query_payload, payload)
            scored.append((outcome.candidate_id, float(outcome.total_score), dict(outcome.scores)))
    scored.sort(key=lambda entry: (-entry[1], str(entry[0])))
    rank: Optional[int] = None
    for index, entry in enumerate(scored, start=1):
        if str(entry[0]) == str(expected_id):
            rank = index
            break
    top1_score = scored[0][1] if scored else 0.0
    return rank, scored, top1_score, rank == 1


def _predictability_timer():
    import time
    return time.perf_counter


def evaluate(
    split_name: str = "all",
    ablation: str = "all",
    top_k: int = DEFAULT_TOP_K,
    seed: int = DEFAULT_SEED,
    config: Optional[RuntimeConfig] = None,
    dataset: Optional[Dict[str, Any]] = None,
    write_split_file: bool = True,
    split_path: Optional[str] = None,
) -> Dict[str, Any]:
    """执行一次完整评估，返回 eval / ablation 两份产物字典。"""
    if split_name not in ("all", "train", "val", "test"):
        raise ValueError(f"未知的 --split 取值：{split_name}（可选 all/train/val/test）")
    if ablation not in _ABLATION_ORDER + ("all",):
        raise ValueError(f"未知的 --ablation 取值：{ablation}（可选 A/B/C/D/all）")
    cfg = config or load_config()
    weights = cfg.weights
    dataset = dataset or load_dataset()
    items: List[Dict[str, Any]] = dataset["items"]
    matches: List[Dict[str, Any]] = dataset["matches"]
    items_by_id = {int(item["id"]): item for item in items}

    split = build_split(items, seed)
    assignment = split["assignment"]
    if write_split_file:
        _write_json(split_path or os.path.join(DEFAULT_OUTPUT_DIR, "testset_split.json"), split)

    engine = MatchEngine(cfg)
    scorer = CredibilityScorer(cfg)

    def in_split(item_id: Any) -> bool:
        if split_name == "all":
            return True
        return assignment.get(str(item_id)) == split_name

    # ---- 候选池：**全量** found 物品（检索语料），--split 只筛选「查询样本」----
    # 说明：若把候选池也限制在同一个子集内，测试集里 24 条 found 中只有约 3 条是某条
    # 查询的正确答案，Recall 会退化为 0，指标失去意义。检索评估的标准做法是
    # 「索引/候选池固定，只换查询集」，因此这里始终用全部 found 物品作为候选池，
    # 只用 --split 过滤被评估的查询（lost 物品）。
    pool_items = [item for item in items if item.get("post_type") == "found"]
    pool_payloads = [item_payload(item) for item in pool_items]
    pool_size = len(pool_payloads)
    pool_mirrored = False
    if 0 < pool_size < 2:
        pool_payloads = pool_payloads * 2   # 极小池时至少保证可排名（并显式标记）
        pool_mirrored = True

    positives = [m for m in matches if m.get("label") == "positive" and in_split(m.get("lost_item_id"))]
    negatives = [m for m in matches if m.get("label") == "negative" and in_split(m.get("lost_item_id"))]
    hard_negatives = [m for m in matches if m.get("label") == "hard_negative" and in_split(m.get("lost_item_id"))]

    timer = _predictability_timer()
    latencies: List[float] = []
    ranks: List[Optional[int]] = []
    error_cases: List[Dict[str, Any]] = []
    cached_scores: Dict[str, Tuple[List[Tuple[Any, float, Dict[str, float]]], float, Optional[int]]] = {}
    ablation_ranks: Dict[str, List[Optional[int]]] = {key: [] for key in _ABLATION_ORDER}
    d_deltas: List[float] = []

    for sample in positives:
        lost_payload, _found_payload = sample_payloads(sample, items_by_id)
        started = timer()
        rank, scored, top1_score, _hit = _rank_pool(
            engine, lost_payload, pool_payloads, sample.get("found_item_id"), top_k
        )
        latencies.append((timer() - started) * 1000.0)
        ranks.append(rank)
        cached_scores[str(sample["sample_id"])] = (scored, top1_score, rank)

        correct_score = 0.0
        correct_scores: Dict[str, float] = {}
        for entry in scored:
            if str(entry[0]) == str(sample.get("found_item_id")):
                correct_score = float(entry[1])
                correct_scores = dict(entry[2])
                break

        # ---- 消融：同一份六维分，按各实验的维度子集重算总分 ----
        for key in _ABLATION_ORDER:
            spec = ABLATIONS[key]
            rescored = [
                (entry[0], ablation_score(entry[2], weights, spec["dimensions"]), entry[2])
                for entry in scored
            ]
            rescored.sort(key=lambda entry: (-entry[1], str(entry[0])))
            rank_ablation: Optional[int] = None
            for index, entry in enumerate(rescored, start=1):
                if str(entry[0]) == str(sample.get("found_item_id")):
                    rank_ablation = index
                    break
            ablation_ranks[key].append(rank_ablation)
            if key == "D" and correct_scores:
                d_deltas.append(abs(ablation_score(correct_scores, weights, spec["dimensions"]) - correct_score))

        got_top1 = scored[0][0] if scored else None
        if rank != 1:
            error_cases.append({
                "sample_id": sample.get("sample_id"),
                "lost_item_id": sample.get("lost_item_id"),
                "expected_found_item_id": sample.get("found_item_id"),
                "got_top1_id": got_top1,
                "rank": rank,
                "top1_score": round(float(top1_score), 4),
                "correct_score": round(float(correct_score), 4),
                # 说明只含「失败类型 + 排名」，不含任何隐藏特征内容
                "note": ("未召回：正确答案不在候选池内" if rank is None
                         else f"排名第 {rank}，Top-1 为 {got_top1}（仅记录 id 与分数）"),
            })

    metrics_rank = rank_metrics(ranks, top_k)

    # ---- 正负样本区分准确率：用全部标注样本的真实分数 ----
    def score_of(sample: Dict[str, Any]) -> float:
        lost_payload, found_payload = sample_payloads(sample, items_by_id)
        return float(engine.match_pair(lost_payload, found_payload).total_score)

    positive_scores = [score_of(sample) for sample in positives]
    negative_scores = [score_of(sample) for sample in negatives]
    hard_scores = [score_of(sample) for sample in hard_negatives]
    discrimination_raw = discrimination_accuracy(positive_scores, negative_scores + hard_scores)
    discrimination_negative = discrimination_accuracy(positive_scores, negative_scores)
    discrimination_hard = discrimination_accuracy(positive_scores, hard_scores)

    # ---- 可信度：全对 / 全错 ----
    correct_cases: List[Dict[str, Any]] = []
    wrong_cases: List[Dict[str, Any]] = []
    for sample in positives:
        found_item = items_by_id[int(sample["found_item_id"])]
        _lost_payload, found_payload = sample_payloads(sample, items_by_id)
        questions = build_verification_questions(found_payload, str(sample.get("sample_id")))
        if not questions:
            continue
        cached = cached_scores.get(str(sample.get("sample_id")))
        candidate_score = cached[1] if cached and cached[2] == 1 else (
            float(engine.match_pair(*sample_payloads(sample, items_by_id)).total_score)
        )
        # 全对：答案 = 隐藏特征原文（仅在内存里使用）
        correct_answers = [str(q["reference_answer"]) for q in questions]
        correct_cases.append(credibility_case(scorer, questions, correct_answers, candidate_score, found_payload))
        # 全错：答案取同类别其它变体的隐藏特征（由 plan_answers 保证与参考不相似）
        for plan in wrong_answers_for(found_item, items, count=3):
            answers = plan_answers(plan, questions)
            assert all(not _is_similar(answer, str(q["reference_answer"]))
                       for answer, q in zip(answers, questions)), "干扰答案不得与参考答案相似"
            wrong_cases.append(credibility_case(scorer, questions, answers, candidate_score, found_payload))

    correct_low = sum(1 for case in correct_cases if case["risk_level"] == "low")
    wrong_high = sum(1 for case in wrong_cases if case["risk_level"] == "high")
    credibility_total = len(correct_cases) + len(wrong_cases)
    credibility_correct = correct_low + wrong_high

    metrics = {
        "Recall@1": round(metrics_rank["Recall@1"], 6),
        f"Recall@{top_k}": round(metrics_rank[f"Recall@{top_k}"], 6),
        "Precision@1": round(metrics_rank["Precision@1"], 6),
        "MRR": round(metrics_rank["MRR"], 6),
        "正负样本区分准确率": round(discrimination_raw["accuracy"], 6),
        "可信认领验证准确率": round((credibility_correct / credibility_total) if credibility_total else 0.0, 6),
        "误通过率": round((len(wrong_cases) - wrong_high) / len(wrong_cases), 6) if wrong_cases else 0.0,
        "误拒绝率": round((len(correct_cases) - correct_low) / len(correct_cases), 6) if correct_cases else 0.0,
        "平均响应时间": latency_metrics(latencies),
    }

    detail = {
        "ranking": {
            "queries": metrics_rank["queries"],
            "hit_count": metrics_rank["hit_count"],
            "miss_count": metrics_rank["miss_count"],
            "mean_rank": round(metrics_rank["mean_rank"], 4),
            "median_rank": round(metrics_rank["median_rank"], 4),
        },
        "discrimination": {
            "overall": {
                "pairs": discrimination_raw["pairs"],
                "wins": discrimination_raw["wins"],
                "ties": discrimination_raw["ties"],
                "accuracy": round(discrimination_raw["accuracy"], 6),
            },
            "vs_negative": {
                "pairs": discrimination_negative["pairs"],
                "accuracy": round(discrimination_negative["accuracy"], 6),
            },
            "vs_hard_negative": {
                "pairs": discrimination_hard["pairs"],
                "accuracy": round(discrimination_hard["accuracy"], 6),
            },
            "score_ranges": {
                "positive": _range_of(positive_scores),
                "hard_negative": _range_of(hard_scores),
                "negative": _range_of(negative_scores),
            },
        },
        "credibility": {
            "correct_answers": len(correct_cases),
            "wrong_answers": len(wrong_cases),
            "correct_answered_as_low": correct_low,
            "correct_risk_levels": _tally(case["risk_level"] for case in correct_cases),
            "wrong_risk_levels": _tally(case["risk_level"] for case in wrong_cases),
            "expected_rule": "全对答案 → low（通过）；全错答案 → high（拒绝）",
        },
        "latency": {
            "unit": "ms",
            "measure": "单次 MatchEngine.match_pair(lost, found) 调用耗时（仅测正样本查询的候选池打分）",
            "mean_ms": metrics["平均响应时间"]["mean_ms"],
            "p50_ms": metrics["平均响应时间"]["p50_ms"],
            "p95_ms": metrics["平均响应时间"]["p95_ms"],
            "max_ms": metrics["平均响应时间"]["max_ms"],
            "samples": metrics["平均响应时间"]["samples"],
        },
    }

    # ---------------- 消融实验 ----------------
    ablation_report: Dict[str, Any] = {}
    for key in _ABLATION_ORDER:
        spec = ABLATIONS[key]
        rank_block = rank_metrics(ablation_ranks[key], top_k)
        normalized = normalized_ablation_weights(weights, spec["dimensions"])
        ablation_report[key] = {
            "title": spec["title"],
            "description": spec["description"],
            "dimensions": list(spec["dimensions"]),
            "raw_weights": {dim: round(float(weights.get(dim, 0.0)), 6) for dim in spec["dimensions"]},
            "normalized_weights": {dim: round(value, 8) for dim, value in normalized.items()},
            "weight_sum": round(sum(normalized.values()), 12),
            "Recall@1": round(rank_block["Recall@1"], 6),
            f"Recall@{top_k}": round(rank_block[f"Recall@{top_k}"], 6),
            "Precision@1": round(rank_block["Precision@1"], 6),
            "MRR": round(rank_block["MRR"], 6),
            "区分准确率": round(discrimination_raw["accuracy"], 6),
            "区分准确率_vs_hard_negative": round(discrimination_hard["accuracy"], 6),
            "queries": rank_block["queries"],
            "hit_count": rank_block["hit_count"],
            "miss_count": rank_block["miss_count"],
        }

    # 相对 A 的提升幅度
    base = ablation_report["A"]
    for key in _ABLATION_ORDER:
        block = ablation_report[key]
        for metric_name in ("Recall@1", f"Recall@{top_k}", "MRR"):
            base_value = float(base[metric_name])
            value = float(block[metric_name])
            block[f"{metric_name}_vs_A_abs"] = round(value - base_value, 6)
            block[f"{metric_name}_vs_A_rel"] = (
                round((value - base_value) / base_value, 6) if base_value > 0 else None
            )

    d_consistent = all(delta <= 1e-9 for delta in d_deltas) if d_deltas else False
    d_max_delta = max(d_deltas) if d_deltas else 0.0

    executed_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
    meta = {
        "model_version": cfg.model_version,
        "dataset_version": dataset["meta"].get("dataset_version", "unknown"),
        "dataset_size": {"items": len(items), "matches": len(matches)},
        "split": split_count(items, split),
        "weights": {dim: round(float(weights.get(dim, 0.0)), 6) for dim in DIMENSIONS},
        "threshold": THRESHOLD_NOTE,
        "top_k": int(top_k),
        "executed_at": executed_at,
        "seed": int(seed),
        "engine": f"MatchEngine({cfg.model_version})",
        "image_backend": str((cfg.rule("image", {}) or {}).get("backend") or "mock"),
        "eval_split": split_name,
        "ablation": ablation,
        "dataset_source": dataset["source"],
        "candidate_pool": {
            "post_type": "found",
            "scope": "all_found_items",
            "size": pool_size,
            "mirrored": pool_mirrored,
            "query_scope": split_name,
            "note": ("候选池 = 全部 found 物品（检索语料固定，共 60 条）；"
                     "查询 = 所选子集内 label==positive 的 lost 物品。"
                     "--split 只筛选查询集，不裁剪候选池，否则测试集召回必然退化为 0。"),
        },
        "sample_counts": {
            "positive": len(positives),
            "negative": len(negatives),
            "hard_negative": len(hard_negatives),
            "evaluated_split": split_name,
        },
        "config_warnings": list(cfg.warnings),
    }

    notes = [
        "划分按 (category, post_type) 分组后组内确定性洗牌，train/val/test = 60%/20%/20%，"
        "每个子集都覆盖 6 个类别与 lost/found 两种 post_type。",
        "测试集不得在调权重过程中反复使用（需求 §9.2）：测试集结果只用于最终报告，"
        "每次使用都在 data/eval/ 留下记录文件。",
        "error_cases 只记录样本 id、排名与分数，不含任何隐藏特征内容（安全红线）。",
        "可信认领验证的答案在内存中构造，产物中不出现标准答案原文。",
        "本评估的全部指标都由引擎实时重算，不使用 demo_matches.json 里预存的 "
        "reference_score / expected_total_score_range（该两列对 36 条负样本中的 27 条已陈旧，"
        "偏差 -0.02~-0.05，属于数据集生成时的配置版本差异，详见 tests/test_metrics.py "
        "TestDatasetConsistency 与本报告说明）。",
        "消融实验按「维度子集 + 权重重新归一化」重算总分，未修改 app/matcher.py。",
        f"维度分之和（D 组重算）与 engine.total_score 的最大偏差 = {d_max_delta:.3e}"
        + ("，在 1e-9 容差内一致（adjustments 全为 0）。" if d_consistent
           else "，超出 1e-9 容差：说明存在非零 adjustment（冲突惩罚/加分），报告如实记录未做掩盖。"),
    ]
    if d_deltas and min(d_deltas) != max(d_deltas):
        notes.append("D 组偏差并非恒定值，请核对 config/matching.yaml 的 adjustments 配置。")

    eval_payload = {
        "meta": meta,
        "metrics": metrics,
        "metrics_detail": detail,
        "error_cases": error_cases,
        "notes": notes,
    }
    ablation_payload = {
        "meta": {
            "model_version": cfg.model_version,
            "dataset_version": dataset["meta"].get("dataset_version", "unknown"),
            "executed_at": executed_at,
            "seed": int(seed),
            "eval_split": split_name,
            "top_k": int(top_k),
            "weights": meta["weights"],
            "threshold": THRESHOLD_NOTE,
            "split": meta["split"],
        },
        "experiments": ablation_report,
        "order": list(_ABLATION_ORDER),
        "consistency": {
            "D_recomputed_equals_engine_total": bool(d_consistent),
            "max_abs_delta": d_max_delta,
            "tolerance": 1e-9,
            "adjustments_note": (
                "config/matching.yaml 的 adjustments 全为 0.0，"
                "因此完整模型总分严格等于六维加权和，D 组重算与 total_score 完全一致。"
                if d_consistent else
                "存在非零 adjustment（冲突惩罚/加分），D 组重算与 total_score 不一致，已如实记录。"
            ),
        },
        "notes": [
            "A/B/C/D 只使用维度子集，权重按剩余维度重新归一化，总分仍在 [0,1]。",
            "消融分数由 engine.match_pair 的六维分重算得到，不修改 matcher 代码。",
            "消融单调性不强制成立；脚本如实输出，不做任何权重调优掩盖。",
            "四组共用同一次 match_pair 结果（六维分一次算完，四组各取子集重算），"
            "因此选定任一组都不会改变其余组的数值，跨组对比等价于同一次运行。",
            "本文件不含任何隐藏特征内容与标准答案。",
        ],
    }
    return {"eval": eval_payload, "ablation": ablation_payload, "split": split}


def _range_of(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"min": 0.0, "max": 0.0, "mean": 0.0, "count": 0}
    return {
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "count": len(values),
    }


def _tally(values: Iterable[str]) -> Dict[str, int]:
    tally: Dict[str, int] = {}
    for value in values:
        tally[str(value)] = tally.get(str(value), 0) + 1
    return tally


# ===========================================================================
# 八、打印与命令行
# ===========================================================================
def print_summary(eval_payload: Dict[str, Any], ablation_payload: Dict[str, Any]) -> None:
    meta = eval_payload["meta"]
    metrics = eval_payload["metrics"]
    detail = eval_payload["metrics_detail"]
    top_k = int(meta["top_k"])
    recall_k_label = "Recall@%d" % top_k
    print("=" * 78)
    print(f"拾光算法评估  model={meta['model_version']}  dataset={meta['dataset_size']}  "
          f"split={meta['eval_split']}  seed={meta['seed']}")
    print(f"划分 train/val/test = {meta['split']['train']}/{meta['split']['val']}/{meta['split']['test']}  "
          f"候选池 {meta['candidate_pool']['size']} 条 found")
    print("-" * 78)
    print("9 项指标")
    for name in ("Recall@1", recall_k_label, "Precision@1", "MRR",
                 "正负样本区分准确率", "可信认领验证准确率", "误通过率", "误拒绝率"):
        print(f"  {name:<18s} {metrics[name]:.4f}")
    latency = metrics["平均响应时间"]
    print(f"  {'平均响应时间':<18s} mean={latency['mean_ms']:.3f}ms  "
          f"p50={latency['p50_ms']:.3f}ms  p95={latency['p95_ms']:.3f}ms")
    disc = detail["discrimination"]
    print(f"  区分准确率明细：负样本 {disc['vs_negative']['accuracy']:.4f} | "
          f"困难负样本 {disc['vs_hard_negative']['accuracy']:.4f} | 组合对 {disc['overall']['pairs']}")
    print(f"  分数区间：正 {disc['score_ranges']['positive']['min']}~{disc['score_ranges']['positive']['max']} | "
          f"困难负 {disc['score_ranges']['hard_negative']['min']}~{disc['score_ranges']['hard_negative']['max']} | "
          f"普通负 {disc['score_ranges']['negative']['min']}~{disc['score_ranges']['negative']['max']}")
    print(f"  可信度：全对 {detail['credibility']['correct_answers']} 例 → "
          f"{detail['credibility']['correct_risk_levels']}；全错 {detail['credibility']['wrong_answers']} 例 → "
          f"{detail['credibility']['wrong_risk_levels']}")
    print(f"  error_cases：{len(eval_payload['error_cases'])} 条")
    print("-" * 78)
    print("A/B/C/D 消融（权重按剩余维度归一化）")
    print(f"  {'实验':<4}{'维度':<34}{'w和':<8}{'R@1':<9}{'R@5':<9}{'MRR':<9}{'vs A (MRR)':<12}")
    for key in ablation_payload["order"]:
        block = ablation_payload["experiments"][key]
        delta = block.get("MRR_vs_A_abs", 0.0)
        recall_k = block.get("Recall@%d" % int(meta["top_k"]), 0.0)
        print(f"  {key:<4}{'+'.join(block['dimensions']):<34}"
              f"{block['weight_sum']:<8.4f}{block['Recall@1']:<9.4f}"
              f"{recall_k:<9.4f}{block['MRR']:<9.4f}{delta:+.4f}")
    consistency = ablation_payload["consistency"]
    print(f"  D 组重算 vs engine.total_score：max|delta|={consistency['max_abs_delta']:.3e}  "
          f"一致={consistency['D_recomputed_equals_engine_total']}")
    print("=" * 78)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="拾光算法评估（9 项指标 + 测试集划分 + A/B/C/D 消融）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--split", choices=["all", "train", "val", "test"], default="all",
                        help="使用哪个子集做检索评估（查询与候选池都取该子集）")
    parser.add_argument("--ablation", choices=["A", "B", "C", "D", "all"], default="all",
                        help="跑哪些消融实验（仍会计算四组指标，标记当前选择）")
    default_output = os.path.join(DEFAULT_OUTPUT_DIR, f"eval_{datetime.now().strftime('%Y%m%d')}.json")
    parser.add_argument("--output", default=default_output, help="评估结果 JSON 输出路径")
    parser.add_argument("--ablation-output", default=None,
                        help="消融结果 JSON 输出路径（默认与 --output 同目录的 ablation_<YYYYMMDD>.json）")
    parser.add_argument("--split-output", default=None,
                        help="划分记录输出路径（默认 <output 目录>/testset_split.json）")
    parser.add_argument("--data-dir", default=None, help="数据集目录（默认 algorithm/data）")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Recall@K 的 K")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="划分与洗牌种子（确定性）")
    parser.add_argument("--quiet", action="store_true", help="不打印报告，只写文件")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.top_k <= 0:
        print("--top-k 必须为正整数", file=sys.stderr)
        return 2

    output_dir = os.path.dirname(os.path.abspath(args.output))
    ablation_output = args.ablation_output or os.path.join(
        output_dir, f"ablation_{datetime.now().strftime('%Y%m%d')}.json")
    split_output = args.split_output or os.path.join(output_dir, "testset_split.json")

    result = evaluate(
        split_name=args.split,
        ablation=args.ablation,
        top_k=args.top_k,
        seed=args.seed,
        dataset=load_dataset(args.data_dir),
        split_path=split_output,
    )
    eval_payload = result["eval"]
    ablation_payload = result["ablation"]
    eval_payload["meta"]["ablation"] = args.ablation

    _write_json(args.output, eval_payload)
    _write_json(ablation_output, ablation_payload)

    if not args.quiet:
        print_summary(eval_payload, ablation_payload)
        print(f"已写入：\n  {os.path.abspath(args.output)}\n  "
              f"{os.path.abspath(ablation_output)}\n  {os.path.abspath(split_output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
