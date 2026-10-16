# 拾光 · 文档索引

> 本文件是仓库全部文档的导航与交付物对照表。
>
> 更新日期：2026-09-30

---

## 1. 交付文档对照（需求 §14.2 要求清单）

需求说明书要求交付以下文档，对照关系如下：

| 需求要求 | 本仓库对应文件 | 状态 |
|---|---|---|
| `README.md` | [../README.md](../README.md) | ✅ |
| `API.md` 或 `docs/api-contract.md` | [api-contract.md](api-contract.md) | ✅ 已冻结 v1.0 |
| `docs/database-design.md` | [database-design.md](database-design.md) | ✅ 已冻结 v1.0 |
| `docs/algorithm.md` | [algorithm.md](algorithm.md) | ✅ |
| `docs/deployment.md` | [deployment.md](deployment.md) | ✅ |
| `docs/user-guide.md` | [user-guide.md](user-guide.md) | ✅ |
| `docs/troubleshooting.md` | [troubleshooting.md](troubleshooting.md) | ✅ |
| `docs/test-report.md` | [test-report.md](test-report.md) | ✅ 框架就绪，结果待填 |
| `docs/cloud-resources.md` | [cloud-resources.md](cloud-resources.md) | ✅ 模板就绪，M4 填写 |
| `docs/codearts-usage.md` | [codearts-usage.md](codearts-usage.md) | ✅ 框架就绪，持续记录 |

**M0 阶段额外产出**

| 文件 | 用途 |
|---|---|
| [M0-交付索引.md](M0-交付索引.md) | M0 交付物索引、评审清单、冻结内容速查、变更流程 |
| [plan.md](plan.md) | 里程碑 M0–M5、WBS 72 人日、人员分工、风险登记 12 项 |
| [prototypes.md](prototypes.md) | 10 个页面线框原型、交互时序、状态清单、验收对照 |

**云部署专项文档**

| 文件 | 用途 |
|---|---|
| [pre-deployment-checklist.md](pre-deployment-checklist.md) | **部署前置准备清单**：7 项决策、资源清单、配置准备、验证工具、阻塞项汇总 |
| [cloud-deployment-runbook.md](cloud-deployment-runbook.md) | **云端部署流程**：从零到外网可访问的 14 节操作手册（约 3.5 小时） |

---

## 2. 阅读路径建议

### 2.1 如果你是评委（5 分钟了解项目）

```text
README.md（项目定位与差异）
   → docs/prototypes.md §6（匹配结果页：看分数怎么算出来的）
   → docs/prototypes.md §7（认领验证页：看怎么防冒领）
   → docs/plan.md §9（答辩脚本 14 步与页面/接口映射）
   → docs/test-report.md §4（安全验收 12 项）
```

### 2.2 如果你是甲方 / 评审专家（需求与风险）

```text
docs/M0-交付索引.md §2（评审会必须确认的 10 项）
   → docs/plan.md（排期、分工、风险登记表）
   → docs/M0-交付索引.md §3.3（与需求说明书的三处有意差异）
   → docs/test-report.md §11（需求 §19 十五项验收结论）
```

### 2.3 如果你是后端开发

```text
docs/api-contract.md（全部接口与枚举，先读 §1 通用约定）
   → docs/database-design.md（表结构与索引）
   → backend/app/services/serializers.py（字段级权限唯一收口点）
   → backend/app/services/state_machine.py（状态机）
   → backend/app/services/scoring.py（评分与权重）
   → docs/deployment.md（本地启动）
```

### 2.4 如果你是前端开发

```text
docs/prototypes.md（10 个页面线框与状态清单）
   → docs/api-contract.md §2（枚举字典，用于文案映射）
   → frontend/src/types/（类型定义）
   → frontend/src/api/http.ts（统一响应解包与错误处理）
```

### 2.5 如果你是算法开发

```text
docs/api-contract.md §4（算法服务三个接口契约）
   → docs/algorithm.md（评分模型、时间/地点规则、可信度公式）
   → algorithm/config/matching.yaml（权重唯一来源）
   → docs/test-report.md §7–8（指标与消融实验要求）
```

### 2.6 如果你是运维 / 部署

```text
docs/pre-deployment-checklist.md（先勾这张清单：决策项、资源、配置、验证工具）
   → docs/cloud-deployment-runbook.md（再照这份跑：从零到外网可访问）
   → docs/deployment.md（方案设计：双路径、华为云资源、安全组、降级矩阵）
   → docs/troubleshooting.md（出问题查这份）
   → docs/cloud-resources.md（资源清单与交接记录）
```

**部署阶段的三条命令**（顺序执行）

```powershell
python scripts\preflight_cloud.py                    # 部署前：静态自检，应 0 阻塞
python scripts\healthcheck.py --url http://<公网IP>   # 部署后：六项组件体检
python scripts\e2e_demo.py --base-url http://<公网IP> # 部署后：走完整业务流程
```

---

## 3. 文档间的权威关系

发生冲突时，按以下优先级判定：

```text
1. 需求说明书（甲方原始文件）
      ↓ 只在与本项目决策冲突时由三方书面确认后偏离
2. docs/api-contract.md      —— 接口、枚举、错误码、状态流转的唯一权威
3. docs/database-design.md   —— 表结构、字段、索引的唯一权威
4. docs/prototypes.md        —— 页面范围与交互的唯一权威
5. 代码
      ↓ 代码与文档不一致时，视为缺陷，必须修复其一并登记
6. 其他文档（README / user-guide / troubleshooting 等）
```

**变更流程**（详见 [M0-交付索引.md](M0-交付索引.md) §6）

```text
发现不一致
   → 判断哪一方是权威（按上表）
   → 修订单侧并在文档末尾「变更记录」登记
   → 破坏性变更需三方确认
   → 同步修改：后端 models/schemas/routes、前端 types/api、算法 schemas、数据集、OpenAPI
```

---

## 4. 文档维护约定

| 约定 | 说明 |
|---|---|
| 版本号 | 每份文档头部标注状态与版本；冻结文档变更必须登记 |
| 变更记录 | 每份文档末尾必须有「变更记录」表格 |
| 术语统一 | 全站使用「候选匹配」「建议归属」「可信度」等非确定性表述 |
| 禁用词 | 文档与代码中不得出现：`确认失主`、`自动归还`、`已确权`、`保证找到`、`100%匹配`、`AI 认定` |
| 密钥 | 任何文档不得包含真实密钥、密码、Token、含密码的连接串 |
| 个人信息 | 示例数据必须为虚构；证件类必须彻底虚构或打码 |
| 时效性 | 「待填」「待补」项必须在对应里程碑结束前补齐，否则视为该里程碑未完成 |

---

## 5. 待补文档清单

| 文档 | 待补内容 | 责任阶段 |
|---|---|---|
| [algorithm.md](algorithm.md) | 真实模型接入后的实现细节补充 | M2 |
| [test-report.md](test-report.md) | §2–§11 全部实测结果 | M1–M5 |
| [cloud-resources.md](cloud-resources.md) | §1–§7 全部资源信息与交接确认 | M4 |
| [codearts-usage.md](codearts-usage.md) | #004 起的持续使用记录与截图 | 全程 |
| `docs/demo-script.md` | Demo 操作剧本（逐步操作与预期结果） | M5 |
| `docs/algorithm.md` §消融 | A/B/C/D 四组实验结果 | M5 |
