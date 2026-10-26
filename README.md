# 拾光 · 校园多模态失物检索与可信认领平台

> 华为云 AI 创新应用竞赛参赛作品 · 可运行 MVP + 云端在线 Demo
>
> 通过**图片、文字、时间、地点与物品属性**的跨模态融合做候选匹配，再通过**隐藏特征问答、风险评估与管理员审核**降低冒领风险。

本仓库按**里程碑**分目录存放外包交付基线，各目录独立完整、可分别检出运行：

| 目录 | 里程碑 | 内容 |
|---|---|---|
| [`M0-2026-9-30-commit-1/`](M0-2026-9-30-commit-1/) | M0 · 需求冻结与本地业务骨架 | 原型与 API 契约、数据库设计、FastAPI 后端骨架、Vue 3 前端 10 页面、Mock 算法服务、脱敏演示数据集、本地部署文件 |
| [`M2-2026-10-13-commit-2/`](M2-2026-10-13-commit-2/) | M2 · AI 属性提取、多模态匹配与可信认领 | 六维加权匹配与可解释结果、隐藏特征验证与可信度评分、管理员审核与审计日志、评测与消融实验、测试报告、云端部署 runbook |
| [`M3-2026-10-23-commit-3/`](M3-2026-10-23-commit-3/) | M3 · 可信认领验收与前后端联调可运行 | 认领验证语义判定与风险分级、审核闭环与审计留痕、前端可构建可访问、272 后端 + 200 算法用例全通过、M3 验收报告 |

需求说明书见 [`M3-2026-10-23-commit-3/拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md`](M3-2026-10-23-commit-3/拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md)。

---

## 目录约定

```text
.
├── M0-2026-9-30-commit-1/    # M0 交付基线（自包含）
│   ├── backend/              #   FastAPI 后端
│   ├── frontend/             #   Vue 3 + Vite + TypeScript
│   ├── algorithm/            #   算法服务（含 Mock）
│   ├── docs/                 #   设计、契约、原型、部署文档
│   ├── scripts/              #   初始化与运维脚本
│   └── README.md             #   M0 运行说明
├── M2-2026-10-13-commit-2/   # M2 交付基线（自包含）
│   ├── backend/              #   FastAPI 后端
│   ├── frontend/             #   Vue 3 前端
│   ├── algorithm/            #   匹配 / 属性提取 / 可信度
│   ├── data/eval/            #   评测与消融实验结果
│   ├── docs/                 #   算法、测试报告、云部署 runbook
│   ├── scripts/              #   评测、播种、健康检查、E2E 演示
│   └── README.md             #   M2 运行说明
├── M3-2026-10-23-commit-3/   # M3 交付基线（自包含，推荐使用）
│   ├── backend/              #   FastAPI 后端（272 用例）
│   ├── frontend/             #   Vue 3 前端
│   ├── algorithm/            #   匹配 / 属性提取 / 可信度（200 用例）
│   ├── data/eval/            #   评测与消融实验结果
│   ├── docs/                 #   算法、M3 验收报告、云部署 runbook
│   ├── scripts/              #   评测、播种、健康检查、E2E 演示
│   └── README.md             #   M3 运行说明
├── LICENSE
└── .gitignore
```

三个子目录是**同一套代码库在不同里程碑的快照**，不是三个不同项目。能力逐级累积：M0 立骨架 → M2 补 AI 与匹配 → M3 完成可信认领验收与前后端联调。**若只跑一个版本，直接用 `M3-2026-10-23-commit-3/`**。

---

## 快速开始

以下命令均在对应子目录内执行（示例以 M3 为准）。

```bash
# 1. 后端
cd M3-2026-10-23-commit-3/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env          # 然后按本地环境填写变量
alembic upgrade head                # 或 mysql -u root -p < ../scripts/init_db.sql

# 2. 算法服务（仅标准库即可运行）
cd ../algorithm
pip install -r requirements.txt     # 可选：装上则走 FastAPI，缺失时自动降级
python run_service.py               # 默认 9000 端口

# 3. 导入脱敏演示数据
cd ..
python scripts/seed_demo_data.py --all

# 4. 前端
cd frontend
npm install
npm run dev                         # Vite dev server（5173，代理 /api 与 /health）
npm run build && npx vite preview    # 或构建后预览（4173）
```

更详细的部署与排障见各子目录的 `docs/deployment.md`、`docs/troubleshooting.md`；
M2/M3 另提供 `docs/cloud-deployment-runbook.md` 与 `docs/pre-deployment-checklist.md`。

> **本地实测环境（2026-10-26）**：MySQL 8.0 + Python 3.11 + Node 24，Redis/MaaS 未配置时按设计自动降级，核心流程不受影响。

---

## 测试

```bash
# 算法服务（200 用例，仅标准库）
cd M3-2026-10-23-commit-3/algorithm && python -m unittest discover -s tests

# 后端（272 用例，含 M3 验收 38 项）
cd M3-2026-10-23-commit-3 && python scripts/run_backend_tests.py

# 端到端演示流程（服务启动后运行；覆盖发布→匹配→认领→验证→审核→归档）
python scripts/e2e_demo.py --base-url http://127.0.0.1:8000
```

各目录的 `docs/test-report.md` 记录了最近一次测试与评测结果，M3 另有 `docs/M3-验收报告.md`。

---

## 许可

本项目采用 [MIT License](LICENSE)。

演示数据集为人工构造的脱敏数据；第三方依赖（FastAPI、Vue、SQLAlchemy 等）遵循各自的开源许可协议。

---

## 安全约定

- `.env` 与任何密钥**不进入版本库**（已被 `.gitignore` 拦截），只提交 `.env.example`。
- 隐藏特征（`hidden_features`）仅发布者与管理员可读，权限由后端 API 强制，不依赖前端隐藏。
- 验证问题的标准答案不返回前端、不写入普通日志。
- 演示数据集为脱敏数据，证件类图片使用虚构信息。

详细安全与隐私要求见需求说明书第 10 章。

---

## 免责声明

本系统提供候选匹配和辅助审核建议，**不构成物品所有权的自动认定**。最终认领结果由平台管理员根据匹配依据、验证结果和实际交接情况确认。
