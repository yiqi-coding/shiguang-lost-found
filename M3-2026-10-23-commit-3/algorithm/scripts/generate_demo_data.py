"""确定性生成演示数据集。

用法::

    python scripts/generate_demo_data.py            # 写入 data/*.json
    python scripts/generate_demo_data.py --check    # 只校验，不写文件

生成内容：
  * ``data/demo_items.json``    78 条物品（lost 39 / found 39，6 个类别）
  * ``data/demo_matches.json``  48 组匹配样本（positive 24 / hard_negative 12 / negative 12）

本脚本不联网、不装依赖、不使用随机数：同一份代码反复运行结果完全一致。
其中 ``expected_total_score_range`` 由算法自身的真实分值回填（保证数据自洽），
并会做「正样本显著高于困难负样本」的自检。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.matcher import MatchEngine  # noqa: E402
from app.mock_data import build_demo_items, build_demo_matches  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "data")

# 期望分值区间：围绕算法真实分值给出带宽（正/困难负/负的带宽不同）
MARGINS = {"positive": 0.08, "hard_negative": 0.06, "negative": 0.05}


def _to_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """把数据集记录转成 match 接口的入参形态（字段名与契约一致）。"""
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


def _apply_view(payload: Dict[str, Any], view: Dict[str, Any]) -> Dict[str, Any]:
    """把 hard_negative 的 lost_view / found_view 覆盖到入参上（制造困难样本）。"""
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


def _score_sample(engine: MatchEngine, sample: Dict[str, Any], items_by_id: Dict[int, Dict[str, Any]]) -> float:
    lost = _to_payload(items_by_id[sample["lost_item_id"]])
    found = _to_payload(items_by_id[sample["found_item_id"]])
    if sample.get("lost_view"):
        lost = _apply_view(lost, sample["lost_view"])
    if sample.get("found_view"):
        found = _apply_view(found, sample["found_view"])
    # 时间与地点按样本记录重写，保证与 time_diff_hours / location_* 自洽
    lost["event_time"] = sample.get("lost_event_time") or lost["event_time"]
    found["event_time"] = sample.get("found_event_time") or found["event_time"]
    lost["location"] = sample.get("location_lost") or lost["location"]
    found["location"] = sample.get("location_found") or found["location"]
    outcome = engine.match_pair(lost, found)
    return float(outcome.total_score)


def build_dataset() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    items = build_demo_items()
    # 深拷贝，避免修改 mock_data 返回的结构（保证反复生成结果一致）
    matches = json.loads(json.dumps(build_demo_matches(items), ensure_ascii=False))
    items_by_id = {int(item["id"]): item for item in items}
    engine = MatchEngine()

    summary: Dict[str, Dict[str, float]] = {}
    for sample in matches:
        score = _score_sample(engine, sample, items_by_id)
        margin = MARGINS.get(sample["label"], 0.08)
        low = max(0.0, round(score - margin, 2))
        high = min(1.0, round(score + margin, 2))
        sample["expected_total_score_range"] = [low, high]
        sample["reference_score"] = round(score, 4)
        sample.setdefault("lost_event_time", items_by_id[sample["lost_item_id"]]["event_time"])
        bucket = summary.setdefault(sample["label"], {"min": score, "max": score, "sum": 0.0, "count": 0.0})
        bucket["min"] = min(bucket["min"], score)
        bucket["max"] = max(bucket["max"], score)
        bucket["sum"] += score
        bucket["count"] += 1

    stats = {
        label: {
            "count": int(bucket["count"]),
            "min": round(bucket["min"], 4),
            "max": round(bucket["max"], 4),
            "mean": round(bucket["sum"] / max(bucket["count"], 1), 4),
        }
        for label, bucket in sorted(summary.items())
    }
    return items, matches, stats


def self_check(stats: Dict[str, Dict[str, float]]) -> List[str]:
    problems: List[str] = []
    positive = stats.get("positive", {})
    hard = stats.get("hard_negative", {})
    negative = stats.get("negative", {})
    if positive.get("count", 0) < 20:
        problems.append(f"正样本不足 20 组：{positive.get('count')}")
    if (hard.get("count", 0) + negative.get("count", 0)) < 20:
        problems.append("负样本不足 20 组")
    if hard.get("count", 0) < 8:
        problems.append(f"hard_negative 不足 8 组：{hard.get('count')}")
    if positive and hard and positive.get("min", 0.0) <= hard.get("max", 1.0):
        problems.append(
            f"正样本最低分 {positive.get('min')} 未高于困难负样本最高分 {hard.get('max')}，样本区分度不足"
        )
    if hard and negative and negative.get("max", 0.0) > hard.get("min", 1.0):
        problems.append(
            f"普通负样本最高分 {negative.get('max')} 高于困难负样本最低分 {hard.get('min')}，标签可能混淆"
        )
    return problems


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成拾光演示数据集（确定性）")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args(argv)

    items, matches, stats = build_dataset()
    problems = self_check(stats)

    print(f"物品条数: {len(items)}  (lost={sum(1 for i in items if i['post_type'] == 'lost')}, "
          f"found={sum(1 for i in items if i['post_type'] == 'found')})")
    print(f"匹配样本: {len(matches)}  正={stats.get('positive', {}).get('count')}  "
          f"困难负={stats.get('hard_negative', {}).get('count')}  普通负={stats.get('negative', {}).get('count')}")
    for label, bucket in stats.items():
        print(f"  [score] {label:14s} n={bucket['count']:3d}  min={bucket['min']:.4f}  "
              f"mean={bucket['mean']:.4f}  max={bucket['max']:.4f}")

    if problems:
        print("\n自检未通过：")
        for problem in problems:
            print("  - " + problem)
        return 1
    print("\n自检通过：正样本分数显著高于困难负样本，困难负样本高于普通负样本。")

    if args.check:
        print("（--check 模式，未写入文件）")
        return 0

    items_path = os.path.join(DATA_DIR, "demo_items.json")
    matches_path = os.path.join(DATA_DIR, "demo_matches.json")
    _write_json(items_path, items)
    _write_json(matches_path, matches)
    print(f"\n已写入:\n  {items_path}\n  {matches_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
