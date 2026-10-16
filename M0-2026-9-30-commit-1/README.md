# 拾光 · 校园多模态失物检索与可信认领平台

> 华为云 AI 创新应用竞赛参赛作品 · 可运行 MVP + 云端在线 Demo
>
> 通过**图片、文字、时间、地点与物品属性**的跨模态融合做候选匹配，再通过**隐藏特征问答、风险评估与管理员审核**降低冒领风险。

---

## 这个项目和普通失物招领的区别

| 普通失物招领 | 拾光 |
|---|---|
| 关键词搜索，描述用词不同就搜不到 | 图文 + 时间 + 地点六维加权打分，描述不同也能匹配 |
| 只给一个「找到了」的结果 | 返回总分、六个维度分、匹配依据、不确定因素与风险提示 |
| 谁都能认领，容易冒领 | 隐藏特征问答 + 可信度评分 + 管理员审核 |
| 信息发布后没有闭环 | 状态机驱动的发布 → 匹配 → 认领 → 审核 → 归档全流程 |

**免责声明**：本系统提供候选匹配和辅助审核建议，**不构成物品所有权的自动认定**。最终认领结果由平台管理员根据匹配依据、验证结果和实际交接情况确认。

---

## 仓库结构

```text
E:\ICT\
├── docs/                          # 📐 设计与契约文档（M0 交付物）
│   ├── api-contract.md            #    REST + 算法服务接口契约（已冻结 v1.0）
│   ├── database-design.md         #    5 张表设计 + 完整 DDL + 迁移策略
│   ├── prototypes.md              #    10 个页面原型线框与交互说明
│   ├── plan.md                    #    里程碑、排期、分工、风险登记
│   └── deployment.md              #    本地与华为云部署方案
│
├── backend/                       # 🐍 后端 API（FastAPI）
│   ├── app/
│   │   ├── core/                  #   配置、错误、响应封装、请求追踪、日志脱敏、安全
│   │   ├── db/                    #   数据库会话
│   │   ├── models/                #   SQLAlchemy 模型（users/items/item_images/matches/claims/audit_logs）
│   │   ├── schemas/               #   Pydantic 请求/响应模型
│   │   ├── services/              #   业务编排、状态机、评分、序列化脱敏、缓存、存储、审计
│   │   ├── clients/               #   华为云 MaaS 与算法服务 HTTP 客户端
│   │   ├── api/v1/                #   路由
│   │   └── main.py                #   应用入口
│   ├── alembic/                   #   数据库迁移
│   ├── tests/                     #   单元测试与安全红线测试
│   └── requirements.txt
│
├── frontend/                      # 🖥️ 前端（Vue 3 + Vite + TypeScript + Element Plus）
│   └── src/{api,components,router,stores,styles,types,views}/
│
├── algorithm/                     # 🧠 算法服务（可独立替换）
│   ├── app/                       #   匹配、属性提取、可信度评估、地点解析
│   ├── config/                    #   ⭐ 权重与规则的唯一配置源（matching.yaml 等）
│   ├── data/                      #   脱敏演示数据集（≥60 条物品 + 正负样本标签）
│   └── tests/
│
├── scripts/                       # 🔧 初始化与运维脚本
│   ├── init_db.sql                #   建库建表 DDL
│   ├── seed_demo_data.py          #   导入脱敏演示数据与演示账号
│   └── healthcheck.py             #   健康检查
│
├── data/                          # 📦 数据集与评估产物
│   ├── raw/                       #   原始脱敏数据
│   ├── images/                    #   演示图片（大文件不提交，单独交接）
│   └── eval/                      #   指标与消融实验结果
│
├── deploy/nginx.conf              # Nginx 配置（静态托管 + API 反代）
├── Dockerfile                     # 后端镜像（多阶段）
├── frontend/Dockerfile            # 前端镜像（构建 + Nginx）
├── algorithm/Dockerfile           # 算法服务镜像
├── docker-compose.yml             # 本地/云端编排
├── .env.example                   # 环境变量模板（真实值不入库）
└── 拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md   # 原始需求（甲方文件）
```

---

## 快速开始

### 前置要求

| 组件 | 版本 | 当前开发机状态 |
|---|---|---|
| Python | 3.11+ | ✅ 3.11.9 |
| Node.js | 20+ | ✅ 24.16.0 |
| MySQL | 8.0 | ⚠ 需安装 |
| Redis | 7.x | ⚠ 需安装（可用 Memurai / WSL2 替代） |
| Docker | 24+ | ⚠ **当前开发机未安装**（云端部署需要） |

> 详细安装步骤见 [docs/deployment.md](docs/deployment.md) §4「路径 A · 原生安装」。

### 1. 配置环境变量

```powershell
cd E:\ICT
Copy-Item .env.example .env
# 编辑 .env，填入本地 MySQL / Redis 连接信息
```

**红线**：`.env` 已被 `.gitignore` 忽略，**绝不提交**；仓库只保留 `.env.example`。

### 2. 初始化数据库

```powershell
# 方式一：直接用 SQL 建表
mysql -u root -p < scripts\init_db.sql

# 方式二：用 Alembic 迁移（推荐，可回滚）
cd backend
pip install -r requirements.txt
alembic upgrade head
```

### 3. 导入演示数据与账号

```powershell
python scripts\seed_demo_data.py --all
```

预置演示账号（答辩用，密码见脚本输出的提示）：

| 账号 | 角色 | 用途 |
|---|---|---|
| `demo_student` | student | 失主视角，发布失物、发起认领 |
| `demo_finder` | student | 拾到者视角，发布拾物 |
| `admin_demo` | admin | 管理台审核、查看日志 |

### 4. 启动三个服务（三个终端）

```powershell
# 终端 1 · 算法服务（端口 9000）
cd E:\ICT\algorithm
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload

# 终端 2 · 后端 API（端口 8000，Swagger 在 /docs）
cd E:\ICT\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 终端 3 · 前端（端口 5173）
cd E:\ICT\frontend
npm install
npm run dev
```

访问 <http://localhost:5173>；接口文档 <http://localhost:8000/docs>。

### 5. 用 Docker 一键启动（需先安装 Docker）

```bash
cp .env.example .env    # 填写真实值
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python /app/scripts/seed_demo_data.py --all
# 访问 http://localhost
```

---

## 核心设计要点

### 匹配评分模型（权重集中在 `algorithm/config/matching.yaml`）

```text
总分 = 0.20×类别分 + 0.25×属性/文本分 + 0.20×图像相似度
     + 0.15×时间合理度 + 0.15×地点合理度 + 0.05×品牌/型号分
```

- 所有维度分与总分归一化到 `[0, 1]`，且有单元测试断言 `total_score == Σ(权重×维度分)`（容差 1e-6）；
- **数值由程序计算，AI 只负责理解与解释**——这是可复现性的保证；
- 权重、时间分段、地点层级、风险阈值全部配置化，不散落在代码里。

### 时间分与地点分

| 时间差 | 分数 |
|---|---|
| ≤ 2 小时 | 1.00 |
| 2~12 小时 | 1.00 → 0.80 线性 |
| 12~24 小时 | 0.60 |
| 24~72 小时 | 0.30 |
| > 72 小时 | 0.10 |
| 缺失 | 0.50（可配置中性分，不崩溃） |

| 地点关系 | 分数 |
|---|---|
| 同一具体地点 | 1.00 |
| 同一建筑不同楼层 | 0.85 |
| 相邻建筑 | 0.60 |
| 同一校区较远 | 0.35 |
| 不同校区 | 0.10 |
| 未知 | 0.50 |

### 可信度评分（仅作辅助依据，**不自动确权**）

```text
可信度分 = 0.60×隐藏特征答案匹配 + 0.20×候选物品综合匹配分
         + 0.10×发布信息完整度 + 0.10×行为/风险规则分
```

风险等级：`≥0.75 low` / `0.45~0.75 medium` / `<0.45 high`。

### 状态机（后端集中控制，非法跳转返回 409）

```text
published → matched → claiming → pending_review → claimed / rejected / request_more_info
任意状态（管理员）→ suspended / disputed → closed（终态）
```

### 多层安全设计

| 层次 | 措施 |
|---|---|
| 序列化层 | `app/services/serializers.py` 是字段级权限的**唯一收口点**，普通用户响应中不出现 `hidden_features` |
| 存储层 | 标准答案只存**加盐哈希**（`answers_hash`），`answers_plain` 默认 NULL 不启用 |
| 日志层 | `SensitiveFilter` 统一正则脱敏，禁止 Authorization / 密钥 / 隐藏特征 / 答案进入日志 |
| 数据库层 | 应用账号对 `audit_logs` **只有 SELECT/INSERT**，从底层保证审计日志不可篡改 |
| 测试层 | `tests/test_serializers.py` 与 `test_questions_security.py` 是专门的安全红线测试 |
| 传输层 | 上传文件经扩展名 → Content-Type → 魔数 → 大小 → Pillow 可解析性五重校验 |

---

## 测试与自检

```powershell
# 产物与契约自检（不需要数据库、不需要依赖，可随时运行）
python scripts\verify_m0.py

# 演示数据集校验（不写库）
python scripts\seed_demo_data.py --dry-run

# 后端（单元测试 + 安全红线测试；需先 cd backend && pip install -r requirements.txt）
cd backend
python -m unittest discover -s tests -v
# 或：pytest -v --cov=app

# 算法服务（确定性，可重复运行；纯标准库，无需第三方依赖）
cd ..\algorithm
python -m unittest discover -s tests -v

# 健康检查（服务启动后）
python scripts\healthcheck.py --url http://localhost:8000
```

`scripts/verify_m0.py` 会校验：交付物是否齐全、枚举与权重是否前后一致、安全红线是否有明文缺口、
演示数据集是否满足需求 §9.1（数量、类别覆盖、hard negative、隐私）、部署文件是否合规。

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [需求说明书](拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md) | 甲方原始需求（Baseline v1.0） |
| [文档索引](docs/README.md) | 全部文档导航、按角色阅读路径、权威关系 |
| [M0 交付索引](docs/M0-交付索引.md) | M0 交付物清单、评审会必须确认的 10 项、冻结内容速查 |
| [接口契约](docs/api-contract.md) | 23 个接口、枚举字典、错误码、状态流转图（已冻结 v1.0.2） |
| [数据库设计](docs/database-design.md) | 6 张表、索引、完整 DDL、迁移与备份策略 |
| [算法设计](docs/algorithm.md) | 六维评分、时间/地点规则、属性提取、可信度、真实模型接入指引 |
| [原型与交互](docs/prototypes.md) | 10 个页面线框、状态清单、验收对照 |
| [实施计划](docs/plan.md) | 里程碑 M0–M5、WBS 72 人日、风险登记 12 项 |
| [部署方案](docs/deployment.md) | 本地/云端双路径、华为云资源、降级矩阵 |
| [用户手册](docs/user-guide.md) | 面向失主/拾到者/管理员的操作指引与 FAQ |
| [排障手册](docs/troubleshooting.md) | 症状 → 原因 → 处理的快速定位表 |
| [测试报告](docs/test-report.md) | F01–F14、接口、安全、性能、降级、指标与消融（结果待填） |
| [云资源清单](docs/cloud-resources.md) | 华为云资源与交接记录（M4 填写） |
| [CodeArts 使用记录](docs/codearts-usage.md) | 华为云 CodeArts 在项目中的实际使用（持续记录） |


---

## 里程碑

| 阶段 | 时间 | 目标 |
|---|---|---|
| M0 需求冻结 | D1–D2 | 原型、契约、数据库设计、排期 ✅ |
| M1 本地业务骨架 | D3–D7 | 不依赖真实 AI 即可端到端演示 |
| M2 AI 与匹配 | D8–D14 | 属性提取、向量化、六维评分、可解释结果 |
| M3 可信认领 | D15–D19 | 隐藏特征、验证问答、风险等级、审核台 |
| M4 华为云部署 | D20–D25 | RDS/Redis/OBS/MaaS 接入，外网可访问 |
| M5 比赛交付 | D26–D30 | 源码、文档、测试报告、指标、演示材料 |

---

## 许可与数据说明

- 演示数据集全部为**人工构造的脱敏数据**，不含任何真实个人信息；
- 校园卡、证件类数据均使用虚构学号与姓名；
- 代码用于竞赛评审目的。
