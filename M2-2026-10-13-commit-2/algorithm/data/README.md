# 拾光 · 演示数据集说明

本目录存放**算法侧演示与评测用**的合成数据，供 `POST /algorithm/v1/match` 联调、
离线评估与回归测试使用。

| 文件 | 内容 | 规模 |
| --- | --- | --- |
| `demo_items.json` | 物品记录（失物 + 拾物） | **120 条**（lost 60 / found 60） |
| `demo_matches.json` | 匹配样本（正 / 困难负 / 普通负） | **60 组**（positive 24 / hard_negative 12 / negative 24） |

---

## 1. 数据来源与许可

* **来源**：全部为**人工构造 / 合成**数据，由 `scripts/generate_demo_data.py` 依据
  `app/mock_data.py` 中的固定模板 + 索引算术**确定性生成**（不使用随机数，
  反复运行结果完全一致）。
* **无隐私**：不含任何真实个人、真实校园、真实机构的信息。
  校园卡 / 证件类样本一律使用虚构写法（如 `学号 2023XXXXXX，姓名 张某某`）。
* **校园为虚构**：校区、建筑、楼层、地点点均来自 `config/locations.yaml` 中的
  虚构校园「拾光大学」，与任何真实校园无关。
* **图片**：`image_url` 指向保留域名 `cdn.example-campus.invalid`，
  仅作为字符串输入用于 Mock 图像向量化，**不存在真实图片文件**。
* **许可**：可自由使用、修改、再分发（CC0 风格），无需署名。

---

## 2. `demo_items.json` 字段含义

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | int | 物品编号。失物 `1001+`，拾物 `2001+`；`1001` 与 `2001` 对应**同一件物品** |
| `post_type` | str | `lost`（失物）或 `found`（拾物） |
| `category` | str | 类别：无线耳机 / 水杯 / 书籍 / 校园卡 / 雨伞 / 背包 |
| `title` | str | 标题（同一件物品的失物与拾物标题一致） |
| `description` | str | 公开描述：颜色、品牌、材质、可见特征 |
| `color` / `brand` / `model` / `material` | str \| null | 结构化属性；无品牌类目（书籍、校园卡）为 `null` |
| `location` | str | 中文地点串，可被 `app.locations.parse_location` 解析 |
| `event_time` | str | ISO 时间，基准时间 `2026-09-28T15:00:00` 之后偏移得到 |
| `public_features` | list[str] | **公开特征**：任何人都能在描述里看到的特征 |
| `hidden_features` | list[str] | **隐藏特征**：只用于认领验证问答，不得随匹配结果返回（如「盒盖内侧有一道3厘米划痕」「蓝牙名称 YiQi-AirPods」） |
| `ai_attributes` | object | 规则抽取器的输出形态（`category/brand/model/color/material/visible_features/accessories/keywords/uncertainties`），便于与 `/extract-attributes` 离线比对 |
| `image_url` | str | 演示用占位地址（文件名带类别与变体号） |
| `accessories` / `keywords` | list[str] | 配件与关键词 |
| `synthetic` | bool | 恒为 `true`，提示这是合成数据 |
| `variant_index` | int | 变体号（0–4），同一变体代表同一件物品的外观与隐藏特征 |

### 类别分布

每个类别 20 条（lost 10 / found 10），覆盖 6 个类别，共 120 条：

| 类别 | 变体数 | 说明 |
| --- | --- | --- |
| 无线耳机 | 5 | 含蓝牙名称类隐藏特征 |
| 水杯 | 5 | 含杯底刻字、茶渍等隐藏特征 |
| 书籍 | 5 | 含扉页姓名、页码标记等隐藏特征（无品牌） |
| 校园卡 | 5 | 虚构学号 / 姓名 / 卡套细节 |
| 雨伞 | 5 | 含伞骨、伞柄刻字等隐藏特征 |
| 背包 | 5 | 含内衬墨渍、姓名布标等隐藏特征 |

---

## 3. `demo_matches.json` 字段含义

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `sample_id` | str | 样本编号（`M001` 起） |
| `label` | str | `positive` / `hard_negative` / `negative`（定义见下） |
| `lost_item_id` / `found_item_id` | int | 参与比对的两条物品 |
| `expected_total_score_range` | [float, float] | 期望总分区间（闭区间），由算法真实分值 ± 带宽回填，供后续评估 |
| `reference_score` | float | 生成时的算法真实总分（仅作参考基线，不参与断言） |
| `time_diff_hours` | float | 两条记录的登记时间差（小时） |
| `location_lost` / `location_found` | str | 比对时使用的地点串 |
| `lost_event_time` / `found_event_time` | str | 比对时使用的时间 |
| `notes` | str | 人可读说明 |
| `conflict_fields` | list[str] | 仅困难负样本：刻意制造差异的字段 |
| `lost_view` / `found_view` | object | 仅困难负样本：覆盖后的属性视图（用于制造「同色不同款」） |

### 正负样本定义

* **positive（正样本，24 组）**：同一件物品的失物与拾物记录。
  类别、颜色、品牌、型号、公开特征一致，时间与地点合理（同地点或同建筑不同楼层）。
  时间差覆盖 0.5 小时 ~ 24 小时，其中每组含一条「时间差较大」的样本。
* **hard_negative（困难负样本，12 组）**：**同类别 + 同颜色，但细节不同**的另一件物品。
  例如同为黑色无线耳机，但一个是 `Apple AirPods Pro 2`「盒体右侧有划痕」，
  另一个是 `漫步者 LolliPods Plus`「盒角有磕碰」。
  时间与地点也刻意接近/同校区，逼迫算法依赖品牌与隐藏细节区分，而不是只看颜色。
* **negative（普通负样本，24 组）**：类别不同、跨校区、时间差 30~240 小时的随机组合。

### 分数分布（生成时的实测基线）

| 标签 | 组数 | 最低分 | 平均分 | 最高分 |
| --- | --- | --- | --- | --- |
| positive | 24 | 0.8238 | 0.8705 | 0.9169 |
| hard_negative | 12 | 0.7056 | 0.7705 | 0.7978 |
| negative | 24 | 0.2209 | 0.2838 | 0.3857 |

> 三类样本的分数区间不重叠：正样本 > 困难负样本 > 普通负样本。
> 该性质由 `tests/test_contract.py::test_positives_score_above_hard_negatives` 持续守护。

---

## 4. 如何重新生成 / 校验

```powershell
cd E:\ICT\algorithm

# 重新生成（确定性：结果与已提交的 JSON 完全一致）
python scripts\generate_demo_data.py

# 只校验不写盘（含「正样本必须高于困难负样本」自检）
python scripts\generate_demo_data.py --check
```

生成脚本在写盘前会做自检，任一条不满足即退出码 1：
正样本 ≥20 组、负样本 ≥20 组、hard_negative ≥8 组、
正样本最低分 > 困难负样本最高分 > 普通负样本最高分。

---

## 5. 使用示例

```python
import json
from app.matcher import MatchEngine

items = json.load(open("data/demo_items.json", encoding="utf-8"))
by_id = {item["id"]: item for item in items}
engine = MatchEngine()

lost, found = by_id[1001], by_id[2001]
print(engine.match_pair(lost, found).total_score)   # ≈ 0.92（同一件物品）
```

> 注意：数据集里的 `hidden_features` 仅用于认领验证问答，
> **匹配接口的响应中不会出现隐藏特征内容**。
