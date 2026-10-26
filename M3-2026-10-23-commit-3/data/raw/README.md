# data/raw —— 原始脱敏数据

## 这个目录放什么

存放**未经处理或轻度处理的原始数据**，以及数据集的来源说明。

| 文件类型 | 说明 |
|---|---|
| `*.json` / `*.csv` | 从外部获取或人工构造的原始记录 |
| `demo_items.json` | 演示物品数据的**副本**（权威版本在 `algorithm/data/demo_items.json`） |
| `SOURCES.md` | 数据来源、构造方式、许可证说明（**M2 补齐**） |

## 红线

1. **不得放入任何真实个人信息**：真实姓名、学号、手机号、身份证号、真实照片一律禁止；
2. 证件类（校园卡/学生证）必须使用**虚构信息**或彻底打码；
3. 图片若涉及可识别的真人或他人作品，不得放入本目录（改用自绘/无版权素材）；
4. 大文件（> 10 MB）请勿提交，改为在 `docs/cloud-resources.md` 记录存放位置。

## 权威数据位置

演示数据集的**唯一权威来源**是算法服务的确定性生成器：

```powershell
cd E:\ICT\algorithm
python scripts\generate_demo_data.py     # 重新生成 demo_items.json / demo_matches.json
```

生成结果写入 `algorithm/data/`，并由 `scripts/seed_demo_data.py` 导入数据库。
本目录仅用于存放外部来源的原始素材，**不要**手工编辑权威数据集的副本。
