# 拾光 · 云部署前置准备清单

> 用途：进入云端部署（M4）**之前**需要准备就绪的全部事项。勾完本清单，即可按
> [cloud-deployment-runbook.md](cloud-deployment-runbook.md) 一次性部署成功。
>
> 状态日期：2026-10-13 ｜ 版本：Baseline v1.0

---

## 如何使用本清单

```text
第 0 节  决策项 —— 不解决就无法开始（非技术问题，需甲方/负责人拍板）
第 1 节  代码与产物 —— 本地已完成的准备工作（多数已就绪）
第 2 节  账号与权限 —— 华为云侧要开通什么
第 3 节  资源清单 —— 要买哪些资源，各要记录什么
第 4 节  配置准备 —— 密钥与环境变量
第 5 节  验证工具 —— 部署后怎么验
第 6 节  材料与交接 —— 交付物准备
```

**建议顺序**：第 0 节 → 第 2、3 节（申请资源，可能要等审批）→ 同时做第 1、4、5、6 节。

---

## 第 0 节 · 决策项（🔴 阻塞，必须先定）

| # | 事项 | 为什么阻塞 | 责任人 | 状态 |
|---|---|---|---|:--:|
| 0.1 | **华为云账号归属**：谁注册、谁持有、赛后如何交接 | 需求 §16 明确不接受「无法交接的个人账号建全部云资源」；账号主体决定资源能否交接、成员能否移除 | 甲方 / PM | ⬜ |
| 0.2 | **账号类型**：组织账号还是个人账号 | 个人账号赛后无法移交，验收时会被质疑 | 甲方 / PM | ⬜ |
| 0.3 | **研发接入方式**：IAM 子用户 vs 共享主账号 | 共享主账号会导致密钥扩散、赛后无法干净移除成员 | PM | ⬜ |
| 0.4 | **代码仓库归属**：CodeArts / Gitee / GitHub，组织是哪个 | 需求 §3.3 要求 CodeArts 使用记录，属答辩素材 | PM | ⬜ |
| 0.5 | **比赛额度与到期日** | ⚠ 若额度在答辩前到期，演示当天资源会被回收 | PM | ⬜ |
| 0.6 | **外网访问方式**：域名 or 直接用公网 IP | 影响 HTTPS 方案与 `CORS_ORIGINS` 配置 | PM / BE | ⬜ |
| 0.7 | **谁负责开资源与部署** | 决定 §3 各项由谁在控制台操作 | PM | ⬜ |

> **这 7 项就是 M0 评审会遗留的「附录 A 第 7、8 项」**。它们从 9-30 挂到今天仍未定，
> 是 M4 唯一的硬阻塞。**在解决之前，第 2 节之后的工作都无法真正落地。**

---

## 第 1 节 · 代码与产物（✅ 多数已完成）

### 1.1 代码就绪度

| # | 事项 | 验收方式 | 状态 |
|---|---|---|:--:|
| 1.1.1 | 后端可在真实依赖下启动并自检 | `python scripts\run_backend_tests.py` → 237 tests OK | ✅ |
| 1.1.2 | 算法服务测试通过 | `cd algorithm; python -m unittest discover -s tests -t .` → 200 tests OK | ✅ |
| 1.1.3 | 产物与契约自检 | `python scripts\verify_m0.py` → 0 失败 | ✅ |
| 1.1.4 | 云部署静态前置自检 | `python scripts\preflight_cloud.py` → 0 阻塞 | ✅ |
| 1.1.5 | 三个 Dockerfile 与 compose 齐备 | `Dockerfile` / `algorithm/Dockerfile` / `frontend/Dockerfile` / `docker-compose.yml` | ✅ |
| 1.1.6 | Nginx 配置齐备（SPA 回退、反代、安全头） | `deploy/nginx.conf` | ✅ |
| 1.1.7 | 数据库初始化脚本 + Alembic 迁移 | `scripts/init_db.sql`、`backend/alembic/versions/0001_initial_schema.py` | ✅ |
| 1.1.8 | 环境变量模板 | `.env.example`（含全部必需项，无真实值） | ✅ |
| 1.1.9 | 演示数据集 | 120 物品 / 60 组样本，三类分数不重叠 | ✅ |
| 1.1.10 | 指标与消融实验 | `data/eval/*.json` | ✅ |

### 1.2 尚未验证的项（需在部署前补）

| # | 事项 | 为什么重要 | 怎么补 | 状态 |
|---|---|---|---|:--:|
| 1.2.1 | **镜像构建从未实际执行** | 本机未装 Docker；COPY 路径、构建上下文都未经真实构建验证 | 装 Docker Desktop 后执行 `docker compose build`；**或直接在 ECS 上首次构建时验证** | ⬜ |
| 1.2.2 | ~~`frontend/package-lock.json` 缺失~~ | ✅ **已解决**：2026-10-13 用 `npm install --package-lock-only` 生成（lockfileVersion 3，171 条目，无本机路径泄露），`npm ci` 可用 | — | ✅ |
| 1.2.3 | `alembic upgrade head` 未在真实数据库执行过 | 迁移脚本仅做过语法与文本对齐 | 本地装 MySQL 后执行一次，或首次部署时在 RDS 上执行并观察 | ⬜ |
| 1.2.4 | 前端从未编译过（`vue-tsc` / `vite build`） | 类型错误会在构建阶段暴露 | 同 1.2.1 | ⬜ |

> 1.2.1、1.2.3、1.2.4 三项的共同点是**本机缺少运行环境**（Docker / MySQL / 前端依赖），
> 无法在本地补验。它们会在首次云端构建时集中暴露，因此 Runbook §6 要求**保留 1 天缓冲**。
> 降低风险的做法：在 ECS 上先跑 `docker compose build`，构建通过后再启服务。

### 1.3 代码入库准备

| # | 事项 | 命令 / 说明 | 状态 |
|---|---|---|:--:|
| 1.3.1 | 本地 Git 仓库已初始化 | `git init` + 首次提交（**尚未执行**，等仓库归属确定） | ⬜ |
| 1.3.2 | `.gitignore` 覆盖 `.env`、`.vendor/`、缓存 | 已配置 | ✅ |
| 1.3.3 | 确认历史中无密钥 | `git log --all -- .env` 应为空 | ⬜ |
| 1.3.4 | 确认无大文件误入库 | `data/images/`、`*.zip` 已忽略 | ✅ |

---

## 第 2 节 · 账号与权限（华为云侧）

| # | 事项 | 说明 | 状态 |
|---|---|---|:--:|
| 2.1 | 华为云账号注册并**完成实名认证** | 未实名无法购买 RDS/ECS | ⬜ |
| 2.2 | 创建 IAM 子用户 `shiguang-deploy` | 授予 RDS/DAS/OBS/ECS/VPC 权限，用于开资源与部署 | ⬜ |
| 2.3 | 创建 IAM 子用户 `shiguang-dev` | OBS 读写 + RDS 只读，用于本地联调 | ⬜ |
| 2.4 | 创建 IAM 子用户 `shiguang-app` | **仅限单个桶**的 OBS 读写，应用运行时使用 | ⬜ |
| 2.5 | 为每个子用户**单独**创建 AK/SK | 不要共享凭证 | ⬜ |
| 2.6 | 开启登录保护与操作保护 | 防止凭证被盗用 | ⬜ |
| 2.7 | 确认比赛代金券/免费额度可用 | 记录金额、适用服务、到期日 | ⬜ |

---

## 第 3 节 · 资源清单（要买什么、要记什么）

| # | 资源 | 关键配置 | 必须记录的信息 | 状态 |
|---|---|---|:--:|
| 3.1 | **RDS for MySQL 8.0** | 2vCPU/4GB，与 ECS 同 VPC，**不开放公网**，开启自动备份 | 内网地址、端口、库名、管理员账号；密码单独交接 | ⬜ |
| 3.2 | **DAS Redis** | 1GB 主备，同 VPC，**设置密码** | 内网地址、端口、密码（单独交接） | ⬜ |
| 3.3 | **OBS 桶** | **私有**桶，禁公共读 | 桶名、**区域**、Endpoint | ⬜ |
| 3.4 | **OBS 凭证** | `shiguang-app` 的 AK/SK，策略限定该桶 | AK/SK（单独交接） | ⬜ |
| 3.5 | **MaaS / 模型服务** | 获取 Endpoint、API Key、模型名 | 三者 + 额度信息 | ⬜ |
| 3.6 | **ECS** | Ubuntu 22.04，2vCPU/4GB，与 RDS/Redis 同 VPC | 公网 IP、登录方式（密钥对） | ⬜ |
| 3.7 | **弹性公网 IP + 带宽** | 按带宽计费，5 Mbps 起 | IP 地址 | ⬜ |
| 3.8 | **安全组** | 入：443、80（全网）；22（**仅运维 IP**）；**3306/6379/9000 绝不放行公网** | 规则清单 | ⬜ |
| 3.9 | 域名 + SSL 证书（可选） | 用于 HTTPS 与更正式的演示地址 | 域名、证书到期日 | ⬜ |
| 3.10 | ELB 负载均衡（可选） | 用于 HTTPS 终止 | 实例信息 | ⬜ |

> **区域一致性**：OBS 桶的区域必须与 `OBS_ENDPOINT` 中的区域一致；
> RDS / Redis / ECS 必须在**同一 VPC**，否则内网不通。这两条是最常见的部署失败原因。

---

## 第 4 节 · 配置准备

| # | 事项 | 说明 | 状态 |
|---|---|---|:--:|
| 4.1 | 生成 `APP_SECRET_KEY`（≥32 字节） | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | ⬜ |
| 4.2 | 生成数据库密码 | 同上，24 字节即可 | ⬜ |
| 4.3 | 生成 Redis 密码 | 同上 | ⬜ |
| 4.4 | 确定 `CORS_ORIGINS` | 生产环境填实际域名/IP，**不能留 localhost** | ⬜ |
| 4.5 | 确认 `STORAGE_BACKEND=obs` | 生产必须为 obs（`local` 不满足验收 F03） | ⬜ |
| 4.6 | 决定 `MAAS_ENABLED` | 未开通额度时保持 `false`，系统自动降级为规则提取 | ⬜ |
| 4.7 | 准备 `.env` 并核对无残留 `replace_me` | `grep -n replace_me .env` | ⬜ |
| 4.8 | **密钥交接渠道** | 密码管理器 / 加密压缩包线下交付 —— **不得走 Markdown 或聊天记录** | ⬜ |

---

## 第 5 节 · 验证工具（✅ 已就绪）

| # | 工具 | 用途 | 命令 | 状态 |
|---|---|---|---|:--:|
| 5.1 | 部署前静态自检 | 构建源、环境变量、编排、Nginx、密钥卫生 | `python scripts\preflight_cloud.py` | ✅ |
| 5.2 | 产物与契约自检 | 交付物完整性、枚举权重一致性、安全红线 | `python scripts\verify_m0.py` | ✅ |
| 5.3 | 健康检查 | 分别报告 app/db/redis/obs/算法/MaaS 状态 | `python scripts\healthcheck.py --url <URL>` | ✅ |
| 5.4 | **端到端验收** | 发布→匹配→认领→验证→审核→归档→日志→越权拦截 | `python scripts\e2e_demo.py --base-url <URL>` | ✅ |
| 5.5 | 演示数据导入 | 演示账号 + 60 条数据 | `python scripts\seed_demo_data.py --all` | ✅ |

---

## 第 6 节 · 材料与交接

| # | 事项 | 对应文档 | 状态 |
|---|---|---|:--:|
| 6.1 | 云资源清单填写 | [cloud-resources.md](cloud-resources.md)（现有 75 处「待填」） | ⬜ |
| 6.2 | 环境变量清单（只列名，值单独交接） | [cloud-resources.md](cloud-resources.md) §3 | ⬜ |
| 6.3 | 备份方式与恢复演练记录 | [cloud-resources.md](cloud-resources.md) §4 | ⬜ |
| 6.4 | 到期与费用说明 | [cloud-resources.md](cloud-resources.md) §5 | ⬜ |
| 6.5 | 关闭与释放步骤 | [cloud-deployment-runbook.md](cloud-deployment-runbook.md) §12.2 | ✅ |
| 6.6 | CodeArts 使用记录与截图 | [codearts-usage.md](codearts-usage.md)（框架就绪，记录待补） | ⬜ |
| 6.7 | 测试报告回填云端结果 | [test-report.md](test-report.md) §2 F13/F14 | ⬜ |
| 6.8 | 演示剧本 | `docs/demo-script.md`（**待编写**） | ⬜ |

---

## 准备就绪判定

同时满足以下条件，即可开始部署：

- [ ] 第 0 节 7 项决策**全部确定**（尤其 0.1 账号归属、0.4 仓库归属）
- [ ] 第 1.2 节的 4 项未验证内容**至少已处理 1.2.2**（lock 文件 / 改 `npm ci`）
- [ ] 第 2 节 IAM 子用户与凭证就绪
- [ ] 第 3 节资源已开通或已提交申请（含安全组规则）
- [ ] 第 4 节密钥已生成、`.env` 已备好且无占位符残留
- [ ] `python scripts\preflight_cloud.py` 输出 **0 阻塞**

**然后执行**：[cloud-deployment-runbook.md](cloud-deployment-runbook.md)（预计 3.5 小时）

---

## 附录 · 当前阻塞项汇总

| 优先级 | 阻塞项 | 归属 | 影响 |
|---|---|---|---|
| 🔴 P0 | 华为云账号归属未定 | 甲方 / PM | **完全无法开始**云端部署 |
| 🔴 P0 | 代码仓库归属未定 | 甲方 / PM | 无法入库、无法用 CodeArts、无提交历史 |
| 🟠 P1 | 镜像构建从未实际执行 | BE | 构建期问题会在部署当天集中暴露（建议 ECS 上先构建再启服务） |
| 🟠 P1 | `alembic upgrade head` 未在真实库跑过 | BE | 迁移问题会在首次部署时暴露 |
| 🟡 P2 | 额度到期日未确认 | PM | 若在答辩前到期，演示可能失败 |
| 🟡 P2 | `cloud-resources.md` 75 处待填 | BE | 影响交接完整性 |

> P0 两项自 2026-09-30 的 M0 评审会起挂起至今（已 13 天），是当前唯一的硬阻塞。
>
> ✅ 已解除：`frontend/package-lock.json` 缺失（2026-10-13 生成，`npm ci` 可用）。
