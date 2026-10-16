# 拾光 · 校园多模态失物检索与可信认领平台

> 华为云 AI 创新应用竞赛参赛作品 · 可运行 MVP + 云端在线 Demo
>
> 通过**图片、文字、时间、地点与物品属性**的跨模态融合做候选匹配，再通过**隐藏特征问答、风险评估与管理员审核**降低冒领风险。

本仓库按**里程碑**分目录存放外包交付基线，两个目录各自独立完整、可分别检出运行：

| 目录 | 里程碑 | 内容 |
|---|---|---|
| [`M0-2026-9-30-commit-1/`](M0-2026-9-30-commit-1/) | M0 · 需求冻结与本地业务骨架 | 原型与 API 契约、数据库设计、FastAPI 后端骨架、Vue 3 前端 10 页面、Mock 算法服务、脱敏演示数据集、本地部署文件 |
| [`M2-2026-10-13-commit-2/`](M2-2026-10-13-commit-2/) | M2 · AI 属性提取、多模态匹配与可信认领 | 六维加权匹配与可解释结果、隐藏特征验证与可信度评分、管理员审核与审计日志、评测与消融实验、测试报告、云端部署 runbook |

需求说明书见 [`拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md`](拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md)。

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
├── 拾光_校园多模态失物检索与可信认领平台_外包技术需求说明书.md
└── .gitignore
```

两个子目录是**同一套代码库在不同里程碑的快照**，不是两个不同项目。M2 在 M0 的骨架上补齐了 AI 与可信认领能力，因此若只跑一个版本，建议直接使用 `M2-2026-10-13-commit-2/`。

---

## 快速开始

以下命令均在对应子目录内执行（示例以 M2 为准，M0 同理）。

```bash
# 1. 后端
cd M2-2026-10-13-commit-2/backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env          # 然后按本地环境填写变量
alembic upgrade head                # 或 python ../scripts/init_db.sql

# 2. 算法服务
cd ../algorithm
pip install -r requirements.txt
python -m app.main                  # 默认 9000 端口

# 3. 导入脱敏演示数据
cd ..
python scripts/seed_demo_data.py

# 4. 前端
cd frontend
npm install
npm run dev                         # Vite dev server，代理到本地后端
```

更详细的部署与排障见各子目录的 `docs/deployment.md`、`docs/troubleshooting.md`；
M2 另提供 `docs/cloud-deployment-runbook.md` 与 `docs/pre-deployment-checklist.md`。

---

## 测试

```bash
# 算法服务
cd M2-2026-10-13-commit-2/algorithm && pytest

# 后端
cd M2-2026-10-13-commit-2/backend && pytest

# 端到端演示流程
cd M2-2026-10-13-commit-2 && python scripts/e2e_demo.py
```

各目录的 `docs/test-report.md` 记录了最近一次测试与评测结果。

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
