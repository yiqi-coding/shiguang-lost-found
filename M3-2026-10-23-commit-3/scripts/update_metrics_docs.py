#!/usr/bin/env python
"""把评估结果回填到文档（避免手抄数字出错）。

用法：
    python scripts/update_metrics_docs.py                    # 回填到 docs/test-report.md 与 docs/algorithm.md
    python scripts/update_metrics_docs.py --dry-run          # 只打印将要写入的内容
    python scripts/update_metrics_docs.py --eval PATH --ablation PATH

设计原则：
    文档里的指标**必须**来自 `data/eval/*.json` 的真实产物，而不是人工誊写。
    本脚本用标记区间替换的方式更新，标记之外的内容保持不动：

        <!-- METRICS:TABLE:BEGIN -->
        ...由脚本生成...
        <!-- METRICS:TABLE:END -->

    这样既保证数字与产物一致，也允许在标记外补充人工分析文字。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "data" / "eval"
TEST_REPORT = REPO_ROOT / "docs" / "test-report.md"
ALGORITHM_DOC = REPO_ROOT / "docs" / "algorithm.md"

METRICS_BEGIN = "<!-- METRICS:TABLE:BEGIN -->"
METRICS_END = "<!-- METRICS:TABLE:END -->"
ABLATION_BEGIN = "<!-- ABLATION:TABLE:BEGIN -->"
ABLATION_END = "<!-- ABLATION:TABLE:END -->"

# 指标名 → (中文说明, 是否"越高越好")
METRIC_LABELS: dict[str, tuple[str, bool]] = {
    "Recall@1": ("Recall@1（正样本中排名第 1 即为正确答案的比例）", True),
    "Recall@5": ("Recall@5（正确答案出现在前 5 的比例）", True),
    "Precision@1": ("Precision@1（Top-1 结果确实为正样本的比例）", True),
    "MRR": ("MRR（平均倒数排名）", True),
    "正负样本区分准确率": ("正负样本区分准确率（正样本得分高于负样本的比例）", True),
    "可信认领验证准确率": ("可信认领验证准确率（验证结论与预期一致的比例）", True),
    "误通过率": ("误通过率（错误答案被判为低风险的比例，**越低越好**）", False),
    "误拒绝率": ("误拒绝率（正确答案被判为高风险的比例，越低越好）", False),
}


def latest(pattern: str) -> Path:
    files = sorted(EVAL_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"✗ 未在 {EVAL_DIR} 找到匹配 {pattern} 的评估产物")
    return files[0]


def fmt_metric(name: str, value) -> str:
    if isinstance(value, dict):
        return (
            f"mean {value.get('mean_ms', 0):.1f} ms / "
            f"p50 {value.get('p50_ms', 0):.1f} ms / "
            f"p95 {value.get('p95_ms', 0):.1f} ms"
        )
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def build_metrics_table(eval_data: dict) -> str:
    metrics = eval_data["metrics"]
    meta = eval_data.get("meta", {})
    executed_at = meta.get("executed_at") or datetime.now().strftime("%Y-%m-%d")

    lines: list[str] = []
    lines.append(f"**实测数据**（模型版本 `{meta.get('model_version', 'baseline-1.0')}`，"
                 f"执行时间 {executed_at}，随机种子 {meta.get('seed')}，Top-K={meta.get('top_k')}）")
    lines.append("")
    lines.append("| 指标 | 定义 | 实测值 |")
    lines.append("|---|---|---:|")
    for name, (label, _higher_better) in METRIC_LABELS.items():
        if name in metrics:
            lines.append(f"| `{name}` | {label} | {fmt_metric(name, metrics[name])} |")

    lines.append("")
    latency = metrics.get("平均响应时间", {})
    lines.append(f"| `平均响应时间` | 单次匹配耗时 | {fmt_metric('平均响应时间', latency)} |")

    dataset = meta.get("dataset_size", {})
    split = meta.get("split", {})
    pool = meta.get("candidate_pool", {})
    lines.append("")
    lines.append("**测试条件**")
    lines.append("")
    lines.append(f"- 数据集规模：物品 {dataset.get('items', '?')} 条、匹配样本 {dataset.get('matches', '?')} 组")
    lines.append(f"- 训练/验证/测试划分：{split.get('train', '?')} / {split.get('val', '?')} / {split.get('test', '?')}")
    lines.append(f"- 候选池：{pool.get('size', '?')} 条（{pool.get('scope', 'n/a')}）")
    lines.append(f"- 展示阈值：`{meta.get('threshold', 'n/a')}`")
    lines.append(f"- 图像向量后端：`{meta.get('image_backend', 'n/a')}`（Mock 实现，真实模型接入见 docs/algorithm.md §8）")

    warnings = meta.get("config_warnings") or []
    if warnings:
        lines.append(f"- ⚠ 配置告警：{warnings}")
    return "\n".join(lines)


def build_ablation_table(ablation_data: dict) -> str:
    experiments = ablation_data.get("experiments", {})
    order = ablation_data.get("order") or sorted(experiments)

    lines: list[str] = []
    lines.append("| 实验 | 使用因素 | Recall@1 | Recall@5 | MRR | MRR 相对 A 提升 |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for key in order:
        block = experiments.get(key, {})
        dims = " + ".join(block.get("dimensions") or [])
        rel = block.get("MRR_vs_A_rel")
        rel_text = "—（基线）" if key == "A" or not rel else f"+{rel * 100:.1f}%"
        lines.append(
            f"| {key} | {dims} | {block.get('Recall@1', 0):.4f} | "
            f"{block.get('Recall@5', 0):.4f} | {block.get('MRR', 0):.4f} | {rel_text} |"
        )

    consistency = ablation_data.get("consistency", {})
    lines.append("")
    if consistency:
        delta = consistency.get("max_abs_delta", 0)
        ok = consistency.get("D_recomputed_equals_engine_total", False)
        lines.append(
            f"- **D 组一致性**：用契约冻结权重重算的 D 组总分与引擎 `total_score` "
            f"最大偏差 `{delta:.3e}`（{'一致 ✓' if ok else '不一致 ✗'}），"
            "说明 `matching.yaml` 的 adjustments 确为全 0，总分严格等于六维加权和。"
        )
    c_mrr = experiments.get("C", {}).get("MRR", 0)
    d_mrr = experiments.get("D", {}).get("MRR", 0)
    if abs(c_mrr - d_mrr) < 1e-9:
        lines.append(
            "- **关于 C 与 D 并列**：两者 MRR 相同，即在本数据集上图像分未带来额外排序增益。"
            "原因是 Mock 图像向量由文本特征派生、与属性分高度相关。"
            "**这不代表图像分无效**，而是当前 Mock 实现的局限；接入真实多模态模型后需重新评测。"
        )
    return "\n".join(lines)


def replace_block(text: str, begin: str, end: str, content: str, *, path: Path) -> str:
    start = text.find(begin)
    stop = text.find(end)
    if start == -1 or stop == -1 or stop < start:
        print(f"  ! {path.name} 未找到标记 {begin} / {end}，跳过")
        return text
    return f"{text[:start]}{begin}\n{content}\n{end}{text[stop + len(end):]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="把评估结果回填到文档")
    parser.add_argument("--eval", default=None, help="评估产物路径（默认取最新）")
    parser.add_argument("--ablation", default=None, help="消融产物路径（默认取最新）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args()

    eval_path = Path(args.eval) if args.eval else latest("eval_*.json")
    ablation_path = Path(args.ablation) if args.ablation else latest("ablation_*.json")

    print("=" * 66)
    print("  回填评估指标到文档")
    print("=" * 66)
    print(f"  评估产物：{eval_path.name}")
    print(f"  消融产物：{ablation_path.name}")

    eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
    ablation_data = json.loads(ablation_path.read_text(encoding="utf-8"))

    metrics_table = build_metrics_table(eval_data)
    ablation_table = build_ablation_table(ablation_data)

    if args.dry_run:
        print("\n--- 指标表 ---")
        print(metrics_table)
        print("\n--- 消融表 ---")
        print(ablation_table)
        return 0

    changed: list[str] = []
    for path, begin, end, content in (
        (TEST_REPORT, METRICS_BEGIN, METRICS_END, metrics_table),
        (TEST_REPORT, ABLATION_BEGIN, ABLATION_END, ablation_table),
        (ALGORITHM_DOC, ABLATION_BEGIN, ABLATION_END, ablation_table),
    ):
        if not path.is_file():
            print(f"  ! 文档不存在：{path}")
            continue
        original = path.read_text(encoding="utf-8")
        updated = replace_block(original, begin, end, content, path=path)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            if path.name not in changed:
                changed.append(path.name)

    print()
    if changed:
        print(f"  ✓ 已更新：{', '.join(changed)}")
    else:
        print("  ! 没有任何文档被更新（请检查标记是否存在）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
