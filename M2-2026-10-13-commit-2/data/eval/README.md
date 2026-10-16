# data/eval —— 评估结果与消融实验

## 这个目录放什么

存放算法评估的**可复现产物**，对应需求说明书 §9.3（算法指标）与 §9.4（消融实验）。

| 文件名约定 | 内容 |
|---|---|
| `eval_YYYYMMDD.json` | 单次评估的完整结果（`meta` + 9 项 `metrics` + `metrics_detail` + `error_cases` + `notes`） |
| `ablation_YYYYMMDD.json` | A/B/C/D 四组消融实验对比 + 相对 A 的提升幅度 + D 组一致性校验 |
| `testset_split.json` | 训练/验证/测试集划分记录（60% / 20% / 20%） |
| `error_cases_YYYYMMDD.md` | **可选**：失败案例人工分析（哪些样本判错了、为什么）。**仅当 `eval_*.json` 的 `error_cases` 非空时才产出**；当前全量评估 `error_cases = []`（Recall@1 = 1.0，无召回失败样本），因此该文件不存在。一旦出现错误案例，从 `eval_*.json` 的 `error_cases` 数组（只有 id / 排名 / 分数）整理成 Markdown 即可 |

## 强制要求

1. **每次评估必须记录**：数据集版本、样本数、阈值、模型版本、执行日期 —— 缺少任一项的结果不予采信；
2. **测试集不得在调权重过程中反复使用**（需求 §9.2）。每次使用测试集都必须在此目录留下记录文件；
3. 评估脚本应输出确定性结果（同输入同输出），便于答辩现场复现；
4. 结果同步摘要到 `docs/test-report.md` §7–§8。

## 当前状态

✅ **M2 已落地**。评估脚本：[`algorithm/scripts/evaluate.py`](../../algorithm/scripts/evaluate.py)；
回归测试：`algorithm/tests/test_metrics.py`（全套 197 个用例通过）。

已产出的文件（2026-10-13 实际运行，seed=20260930）：

| 文件 | 说明 |
|---|---|
| `eval_20261013.json` | 全量评估：9 项指标 + `error_cases`（当前为空）+ `meta`（threshold = `MATCH_MIN_DISPLAY_SCORE=0.35`） |
| `ablation_20261013.json` | A/B/C/D 四组消融对比 + 相对 A 的提升幅度 + D 组一致性校验（`max_abs_delta = 0`） |
| `eval_20261013_test.json` / `ablation_20261013_test.json` | **测试集使用记录**（3 条正样本查询；召回 1.0）。按约定，每次使用测试集都要留下这类带日期的记录文件 |
| `testset_split.json` | 120 条物品的 train/val/test 归属（72/24/24）+ 组内顺序 + 划分依据 |

本轮关键指标（全量）：`Recall@1` 1.0000、区分准确率 1.0000、可信认领验证准确率 0.9688、
误通过率 0.0417、误拒绝率 0.0000。误通过率经「关键字段强校验」修复后由 0.3056 降到 0.0417
（详见 `algorithm/README.md` §9.3.1）。

复现命令：

```powershell
cd E:\ICT\algorithm
python -X utf8 scripts\evaluate.py --ablation all
python -X utf8 scripts\evaluate.py --split test --output E:\ICT\data\eval\eval_20261013_test.json `
                                    --ablation-output E:\ICT\data\eval\ablation_20261013_test.json
```

数据集现状（M0 已就绪）：

| 项 | 数量 |
|---|---:|
| 物品记录 | 120（lost 60 / found 60） |
| 正匹配样本 | 24 |
| 困难负样本 | 12 |
| 普通负样本 | 24 |
| 类别覆盖 | 6 类 |

> ⚠️ 已知数据问题（**正由数据负责人重跑生成器修复，勿并发修改**）：
> `algorithm/data/demo_matches.json` 的 `reference_score` /
> `expected_total_score_range` 对 36 条负样本中的 28 条已陈旧（偏差 −0.02 ~ −0.05）。
> 评估脚本不使用这两列，全部指标实时重算；`tests/test_metrics.py::TestDatasetConsistency`
> 对「陈旧」与「已对齐」两种状态都通过。
