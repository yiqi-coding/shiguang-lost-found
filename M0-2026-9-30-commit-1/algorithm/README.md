# 拾光 · 算法服务（baseline-1.0）

校园多模态失物检索与可信认领平台 —— **算法服务骨架 + 可运行 Mock 实现 + 演示数据集**。

把「图片、文本属性、时间、地点」融合成候选匹配分并给出可解释结论，认领时用隐藏特征
问答算可信度分。**不依赖 GPU、不联网、不下载模型**：主匹配逻辑为纯 Python 标准库实现，
FastAPI 仅作为可选外壳，缺失时自动降级。

---

## 1. 快速开始

```powershell
cd E:\ICT\algorithm

# 1) 跑测试（纯标准库，无需安装任何依赖）
python -m unittest discover -s tests -t . -v

# 2) 启动服务（没有 fastapi 时自动使用标准库 http.server，行为一致）
python run_service.py               # 默认 http://127.0.0.1:8000（推荐，任何环境都可用）
python app\main.py                  # 等价写法
python app\main.py --host 0.0.0.0 --port 8080
```

```powershell
# 3) 装了 fastapi/uvicorn 时，也可以用标准方式启动
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

> **关于启动方式**：本机 Python 是 isolated 安装（存在 `F:\python311\python311._pth`），
> `python -c` / `python -m` 不会把当前目录与 `PYTHONPATH` 加入 `sys.path`，因此
> `python -m app.main` 会报 `No module named 'app'`。请使用上面的
> `python run_service.py` 或 `python app\main.py`——两者都会自行把
> `algorithm\` 加入 `sys.path`，已实测可启动并跑通三个端点。
> （若想在任意目录直接用 `python -m app.main`，可在 `python311._pth` 末尾追加一行
> `E:\ICT\algorithm`；这属于本机解释器配置，不属于本仓库交付内容。）

> **关于测试命令里的 `-t .`**：`python -m unittest discover -s tests` 会把 `tests/`
> 当作顶层目录放进 `sys.path`，此时 `import app` 会失败。加上 `-t .` 指定顶层目录为
> `algorithm\` 即可；测试文件里已内置路径兜底（`tests/_bootstrap.py`），两种写法都已实测通过。

### 冒烟测试（无需启动服务）

```powershell
python -c "from app import main; import json; print(json.dumps(main.handle_match({'query_item': {'id': 1, 'category': '无线耳机', 'description': '黑色无线耳机，盒体右侧有划痕', 'attributes': {'color': '黑色'}, 'location': '图书馆三楼', 'event_time': '2026-09-28T15:00:00'}, 'candidate_items': []}), ensure_ascii=False))"
```

---

## 2. 目录结构

```
algorithm/
├─ app/
│  ├─ __init__.py
│  ├─ main.py           FastAPI 应用（**只有此文件导入 fastapi**）+ 三个端点的纯逻辑实现
│  ├─ schemas.py        请求/响应 Schema（优先 Pydantic v2，缺失时降级为 dataclass）
│  ├─ matcher.py        核心评分：六维加权 + 可插拔图像向量接口 + 时间/地点/品牌规则
│  ├─ extractor.py      规则式属性提取（固定 JSON Schema，空输入降级）
│  ├─ credibility.py    认领可信度评分与风险等级（不回显标准答案）
│  ├─ locations.py      校园地点层级解析与地点合理度（只读 locations.yaml）
│  ├─ text_utils.py     中文归一化 / 字符 n-gram / Dice、Jaccard 相似度
│  ├─ config_loader.py  config/*.yaml 加载（无 PyYAML 时用内置极简解析器）+ 内置兜底值
│  └─ mock_data.py      确定性演示数据构造（无随机数）
├─ config/
│  ├─ matching.yaml     ★ 唯一的权重与规则配置源
│  ├─ locations.yaml    虚构校园地点层级表 + 地点分档位
│  └─ questions.yaml    验证问题模板库 + 可信度权重 / 阈值 / 同义词 / 风险规则
├─ tests/               标准库 unittest（test_matcher / test_credibility / test_contract）
├─ data/                demo_items.json / demo_matches.json / README.md
├─ scripts/             数据生成与校验脚本（不参与线上运行）
├─ run_service.py       启动入口（把 algorithm/ 加入 sys.path 后再启动服务）
├─ requirements.txt
└─ README.md
```

---

## 3. 接口契约（3 个端点，路径与字段不可更改）

### 3.1 `POST /algorithm/v1/match`

请求：

```json
{
  "query_item": {
    "id": 1001,
    "image_url": "https://...",
    "category": "无线耳机",
    "description": "黑色无线耳机",
    "attributes": {"color": "黑色", "brand": null, "features": ["盒体右侧有划痕"]},
    "location": "图书馆三楼",
    "event_time": "2026-09-28T15:00:00"
  },
  "candidate_items": []
}
```

响应：

```json
{
  "model_version": "baseline-1.0",
  "results": [
    {
      "candidate_id": 2001,
      "total_score": 0.88,
      "scores": {
        "image_score": 0.81, "text_score": 0.92, "category_score": 1.0,
        "time_score": 0.95, "location_score": 1.0, "brand_model_score": 0.5
      },
      "evidence": ["物品类别一致", "颜色一致"],
      "uncertainties": ["品牌未确认"]
    }
  ]
}
```

* `results` 按 `total_score` 降序；默认最多返回 `scoring.max_results`（配置，20 条），
  可用请求体里的 `top_k` 覆盖。
* 请求体缺失 / `candidate_items` 为空 / 单条候选异常，都不会 500：分别返回空结果、
  空结果与「该候选已跳过」的 `uncertainties`。

### 3.2 `POST /algorithm/v1/extract-attributes`

请求：`{"image_url": "...", "description": "...", "title": "...", "category": "..."}`

响应（**固定 Schema**，由 `app/extractor.py: ATTRIBUTE_SCHEMA` 校验）：

```json
{
  "category": "无线耳机", "brand": null, "model": null, "color": "黑色", "material": null,
  "visible_features": ["椭圆形充电盒", "盒体右侧有划痕"],
  "accessories": ["左右耳机", "充电盒"],
  "keywords": ["蓝牙耳机", "黑色", "无线"],
  "uncertainties": ["品牌标识不清晰"]
}
```

空输入 / 类型错误 / 完全无法识别 → 返回各字段为 `null`/`[]` 的降级结果 + `uncertainties`，**不抛异常**。
描述中出现「疑似 / 好像 / 可能」等表述时，只作为推测输出并写入 `uncertainties`。

### 3.3 `POST /algorithm/v1/claim-credibility`

请求：`{"match_result": {...}, "questions": [...], "answers": [...], "target_item": {...}, "behavior_signals": {...}}`

响应：

```json
{
  "credibility_score": 0.81,
  "risk_level": "low",
  "per_question": [
    {"question_id": "q_scratch_detail", "matched": true, "score": 1.0, "reason": "回答与登记信息一致"}
  ],
  "risk_tips": ["验证结果较好，可按常规流程交付并留存记录"],
  "breakdown": {"answer_match": 1.0, "candidate_match": 0.9, "completeness": 1.0, "behavior": 0.85}
}
```

* `risk_level ∈ {low, medium, high}`，阈值来自 `config/questions.yaml`。
* 兼容 `answers` 传字符串数组或 `{question_id, answer}` 对象数组。
* **绝不返回标准答案**：`per_question[].reason` 由固定模板生成，
  已由 `tests/test_credibility.py::TestNoAnswerLeak` 守护。

> 额外提供 `GET /algorithm/v1/health`（探针，非契约端点）。

---

## 4. 评分模型

### 4.1 总分（权重只来自 `config/matching.yaml`）

```
总分 = 0.20×类别分 + 0.25×属性/文本分 + 0.20×图像相似度
     + 0.15×时间合理度 + 0.15×地点合理度 + 0.05×品牌/型号分
总分 = Σ(权重 × 维度分) + Σ冲突惩罚      # 惩罚默认全为 0，即严格等于加权和
```

所有维度分与总分归一化到 `[0, 1]`。任何维度缺数据 → 取该维度的**可配置中性分**
（默认均为 0.5），并写入 `uncertainties`；`weights` 之和若不为 1，加载时会按比例归一
并写入 `config_warnings`。

### 4.2 时间合理度（分段函数，阈值在 yaml）

| 时间差 | 分数 | 说明 |
| --- | --- | --- |
| 0 ~ 2h | 1.00 | flat |
| 2 ~ 12h | 线性 1.00 → 0.80 | linear |
| 12 ~ 24h | 0.60 | step |
| 24 ~ 72h | 0.30 | step |
| > 72h | 0.10 | flat |
| 缺失 / 无法解析 | 0.50 | 中性分，**不抛异常** |

兼容「失物时间范围 vs 拾取单点时间」：`{"start": ..., "end": ...}` / `[start, end]` /
`"start ~ end"` 均按「点到区间的最短距离」计算。

### 4.3 地点合理度（`config/locations.yaml`）

```
同一具体地点 1.00 / 同建筑同层 1.00 / 同建筑不同楼层 0.85 / 相邻建筑 0.60
/ 同一校区较远地点 0.35 / 不同校区 0.10 / 未知 0.50
```

地点解析：输入中文串（`图书馆三楼`、`三食堂门口`、`西山校区实验楼三楼实验室`）→
输出 `{campus, building, floor, spot}`；解析不出任何层级 → `unknown`（中性分）。
虚构校园含 **2 个校区 / 15 栋建筑 / 若干楼层与地点点**，并在配置里声明相邻建筑关系。

### 4.4 图像相似度（可插拔 + 确定性 Mock）

定义协议 `ImageEmbedder.embed(image_ref) -> list[float]`，再算余弦相似度并映射到 `[0,1]`：
`score = (cos + 1) / 2`。默认实现 `MockImageEmbedder` 由「图片文件名 + 类别/颜色/品牌/型号/
材质/特征」等伪视觉词袋做 `blake2b` 稳定哈希生成 64 维向量 —— 同输入必得同输出（可复现）。
传入的 `extra_tokens` 只是**提示**，真实后端可完全忽略它们直接读图片字节。

### 4.5 属性/文本分

`0.30×字符 n-gram(Dice) + 0.25×关键词重叠 + 0.45×属性匹配`，再按 `calibration` 分段定分；
属性匹配包含颜色、材质与**逐条公开特征的 n-gram 配对（双向覆盖率）**。
出现明确冲突时设上限（gate）：颜色冲突 ≤ 0.65、材质冲突 ≤ 0.80、特征覆盖率 < 0.6 时 ≤ 0.70。
这样「同色不同款」的困难负样本不会因为文案相似而被判成高相似。

### 4.6 可信度分（权重在 `config/questions.yaml`）

```
可信度分 = 0.60×隐藏特征答案匹配 + 0.20×候选物品综合匹配分
         + 0.10×发布信息完整度 + 0.10×行为/风险规则分
```

* 答案匹配：先做同义词归一（划痕↔刮痕、右侧↔右边），再做 bigram Dice；
  关键字段（蓝牙名称、学号）走**精确/规范化匹配**（去空格、统一大小写、全角转半角、
  常见混淆字符 0/O、1/l 归一）。
* 互斥取值组（颜色、左右、有无）命中即判不一致 —— 避免「红色保温杯 vs 蓝色保温杯」
  因措辞相似而误命中。
* 无信号时的中性分：答案 0.50 / 候选 0.50 / 完整度 0.30 / 行为 0.85
  （没有风险信号 ≠ 有风险）。

---

## 5. 演示数据集

见 [`data/README.md`](data/README.md)。摘要：

* `data/demo_items.json`：**120 条**物品（lost 60 / found 60），覆盖 6 个类别，每条含
  `public_features` 与 `hidden_features`；校园卡使用虚构学号与姓名。
* `data/demo_matches.json`：**60 组**样本 —— positive 24 / hard_negative 12 / negative 24，
  每条含 `expected_total_score_range` 与标签。
* 全部为人工合成、可自由使用；由 `scripts/generate_demo_data.py` **确定性生成**。

| 标签 | 组数 | 最低分 | 平均分 | 最高分 |
| --- | --- | --- | --- | --- |
| positive | 24 | 0.8238 | 0.8705 | 0.9169 |
| hard_negative | 12 | 0.7056 | 0.7705 | 0.7978 |
| negative | 24 | 0.2209 | 0.2838 | 0.3857 |

---

## 6. 测试

```powershell
cd E:\ICT\algorithm
python -m unittest discover -s tests -t . -v     # 全部用例（推荐）
python -m unittest discover -s tests             # 亦可（测试文件内置路径兜底）
python -m unittest discover -s tests -t . -k credibility   # 只跑匹配关键字的用例
```

覆盖内容：

| 文件 | 重点 |
| --- | --- |
| `tests/test_matcher.py` | 权重与文档一致、总分 = 加权和（容差 1e-6）、时间分段与单调性、时间缺失取中性分、地点六个档位、Mock embedder 幂等与可插拔、属性提取空输入降级 |
| `tests/test_credibility.py` | 可信度权重与加权和、全对 > 部分对 > 全错、同义表达命中、关键字段大小写/空格/全角归一、风险等级、**标准答案不泄露** |
| `tests/test_contract.py` | 三个端点请求/响应结构、契约字段名、路由表、核心模块不得导入 fastapi、数据集规模与标签定义、`expected_total_score_range` 与实际分值自洽 |

其他脚本：

```powershell
python scripts\generate_demo_data.py            # 重新生成演示数据（确定性）
python scripts\generate_demo_data.py --check    # 只校验不写盘
python scripts\verify_yaml_parser.py            # 校验内置极简 YAML 解析器与 PyYAML 结果一致
python scripts\verify_stdlib_only.py            # 模拟无 PyYAML / pydantic / fastapi 的降级路径
```

---

## 7. 真实模型接入点

| 能力 | 替换位置 | 做法 |
| --- | --- | --- |
| 图像向量 | `app/matcher.py` 的 `ImageEmbedder` 协议 + `register_image_embedder()` | 实现 `embed(image_ref) -> list[float]`（华为云 MaaS 多模态向量 / 本地 CLIP 服务），注册后把 `config/matching.yaml → rules.image.backend` 改成对应名称即可；`MatchEngine` 不引用任何具体类名，后端异常自动降级为中性分 |
| 文本/属性抽取 | `app/extractor.py` 的 `AttributeExtractor` | 保持 `extract()` 返回同样的 JSON Schema；可换成 LLM/多模态模型，只要求把置信度低的结果写进 `uncertainties` |
| 中文分词与语义相似度 | `app/matcher.py::score_text` 的 `component_weights` | 换成 embedding 语义相似度后仍返回 0~1 分值即可，权重与阈值继续留在 yaml |
| 答案语义判定 | `app/credibility.py::CredibilityScorer.score_answer` | 换成向量检索/LLM 判定，但必须保持「不回显标准答案」的约束 |
| 时间/地点规则 | `config/matching.yaml`、`config/locations.yaml` | 纯配置调整，无需改代码 |

---

## 8. 依赖与降级矩阵

| 依赖 | 是否必需 | 缺失时的行为 |
| --- | --- | --- |
| Python 3.11 | ✅ 必需 | — |
| PyYAML | ❌ 可选 | 使用 `app/config_loader.py` 内置的极简 YAML 解析器（与 PyYAML 结果一致，`scripts/verify_yaml_parser.py` 校验） |
| pydantic v2 | ❌ 可选 | `app/schemas.py` 自动切换到等价的 dataclass 实现 |
| fastapi | ❌ 可选 | `app` 置为 `None`；核心逻辑可直接调用，`python -m app.main` 起标准库 HTTP 服务 |
| uvicorn | ❌ 可选 | 回退到 `http.server`（`ThreadingHTTPServer`），路由与返回结构一致 |
| numpy / 任何模型库 | ❌ 不需要 | 余弦相似度、n-gram 等全部为纯标准库实现 |

`requirements.txt` 只列出可选依赖，**不安装也能跑通全部测试与演示**。
