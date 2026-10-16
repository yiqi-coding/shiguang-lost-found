# 拾光 · 华为云部署流程（Runbook）

> 用途：**从零到外网可访问**的完整操作手册。按本文顺序执行即可完成云端部署与验收。
>
> 适用版本：Baseline v1.0 ｜ 编写日期：2026-10-13
>
> 配套文档：[deployment.md](deployment.md)（方案设计）· [cloud-resources.md](cloud-resources.md)（资源清单与交接）· [troubleshooting.md](troubleshooting.md)（排障）

---

## 0. 开始之前：两个必须先解决的前置问题

这两项**未确定就无法开始**，且都是非技术问题，必须由甲方/项目负责人拍板：

| # | 待决事项 | 为什么必须先行 |
|---|---|---|
| 1 | **华为云账号归谁持有** | 需求 §16 明确「不接受使用无法交接的个人账号创建全部云资源」。账号主体决定了资源能否在赛后交接、成员能否干净移除 |
| 2 | **代码仓库放哪里** | 需求 §3.3 要求 CodeArts 使用记录；仓库归属决定了 CI/CD 与答辩素材的获取方式 |

**推荐方案**：账号由甲方（或学校/项目组）以**组织账号**注册并持有，研发团队通过 **IAM 子用户**接入，各自授予最小权限，**任何人都不持有主账号 AK/SK**。这样同时满足三条红线：账号可交接、密钥不扩散、CodeArts 全程留痕。

> ⚠ 如果你现在还没有账号，先看 §1；已有账号则直接跳到 §2。

---

## 1. 账号与权限准备（约 30 分钟）

### 1.1 注册 / 确认账号

1. 访问华为云官网注册（若尚无账号）；
2. **实名认证**——未实名无法购买 RDS/ECS 等付费资源；
3. 确认账号类型为**企业/组织**而非个人（个人账号赛后难以交接）；
4. 记录账号 ID（后续填入 [cloud-resources.md](cloud-resources.md) §1）。

### 1.2 创建 IAM 子用户（关键步骤）

在**统一身份认证服务 IAM** 中为每位成员创建独立子用户：

| 子用户 | 授予策略 | 用途 |
|---|---|---|
| `shiguang-deploy` | `RDS FullAccess`、`DAS FullAccess`、`OBS OperateAccess`、`ECS FullAccess`、`VPC FullAccess` | 开资源与部署 |
| `shiguang-dev` | `OBS OperateAccess`、`RDS ReadOnlyAccess` | 本地联调读取 |
| `shiguang-app` | 仅对象存储读写（自定义策略，限定单个桶） | **应用运行时使用的 AK/SK** |

**要点**

- 每个子用户**单独**创建 AK/SK，不要共享；
- `shiguang-app` 的权限必须**限定到具体桶**，不要给全局 OBS 权限；
- 开启**登录保护**与**操作保护**；
- 记录每个子用户的用途，赛后逐一删除（§8）。

### 1.3 确认比赛额度

| 项 | 需要确认 |
|---|---|
| 免费额度 / 代金券 | 金额、适用服务、**有效期** |
| 额度到期日 | ⚠ 若落在答辩日之前，演示当天资源可能被回收 —— 必须写进 [cloud-resources.md](cloud-resources.md) §5 |
| 是否需要额外付费 | 超出部分由谁承担 |

---

## 2. 开通云资源（约 40 分钟）

按以下顺序开通，**每一步完成后立即把信息填入 [cloud-resources.md](cloud-resources.md)**。

### 2.1 RDS for MySQL

| 配置项 | 建议值 | 说明 |
|---|---|---|
| 数据库引擎 | MySQL 8.0 | 与本地开发一致 |
| 规格 | 2 vCPU / 4 GB | 演示规模足够 |
| 存储 | 40 GB SSD | |
| 部署形态 | 单机（演示）或主备（更稳） | |
| 虚拟私有云 | 与 ECS 同一 VPC | 否则内网不通 |
| 安全组 | **仅放行 ECS 网段访问 3306** | 绝不开放公网 |
| 字符集 | `utf8mb4` / `utf8mb4_0900_ai_ci` | 支持中文与 emoji |
| 自动备份 | **开启**，保留 7 天 | 验收要求 |
| 数据库名 | `shiguang` | |

开通后记录：**内网地址、端口、数据库名、管理员账号**（密码单独交接，**不要写进任何文档**）。

### 2.2 DAS Redis

| 配置项 | 建议值 |
|---|---|
| 版本 | Redis 5.0 或以上 |
| 规格 | 1 GB 主备 |
| VPC | 与 ECS 同一 VPC |
| 安全组 | 仅放行 ECS 网段访问 6379 |
| 密码 | **必须设置**（`REDIS_PASSWORD`） |
| 最大内存策略 | `allkeys-lru` |

### 2.3 OBS 对象存储

1. 创建**私有**桶（**不要**选公共读）；
2. 桶名建议：`shiguang-items-<随机后缀>`（桶名全局唯一）；
3. 记录 **Endpoint**（格式 `https://obs.<区域>.myhuaweicloud.com`）与**区域**；
4. 为 `shiguang-app` 子用户创建 AK/SK，策略限定该桶；
5. （可选）配置生命周期规则，演示结束后转归档或删除以控成本。

> ⚠ **区域必须与桶一致**：Endpoint 写错区域会报 404 或跨区域访问失败，这是最常见的部署坑之一。

### 2.4 MaaS / 模型服务

1. 开通模型服务，获取 **Endpoint** 与 **API Key**；
2. 确认所用**模型名称**（填入 `MAAS_MODEL`）；
3. 记录额度与计费方式；
4. 若暂时无法开通 → **不要阻塞部署**：保持 `MAAS_ENABLED=false`，系统会自动降级为规则提取（需求 §13.5 强制要求此降级能力）。

### 2.5 ECS 云服务器

| 配置项 | 建议值 |
|---|---|
| 镜像 | Ubuntu 22.04 LTS |
| 规格 | 2 vCPU / 4 GB（前端 + 后端 + 算法三个容器） |
| 系统盘 | 40 GB |
| 弹性公网 IP | 需要（外网访问） |
| 带宽 | 按带宽计费，5 Mbps 起 |
| VPC | **与 RDS / Redis 同一 VPC** |

**安全组规则（严格遵守）**

| 方向 | 协议/端口 | 源 | 说明 |
|---|---|---|---|
| 入 | TCP 443 | `0.0.0.0/0` | HTTPS 访问 |
| 入 | TCP 80 | `0.0.0.0/0` | HTTP（跳转 HTTPS 用） |
| 入 | TCP 22 | **运维固定 IP** | SSH —— 禁止全网开放 |
| 出 | TCP 443 | `0.0.0.0/0` | 访问 OBS / MaaS / 镜像仓库 |
| — | 3306 / 6379 / 9000 | **绝不放行公网** | 数据库、Redis、算法服务仅内网 |

---

## 3. 代码入库与服务器准备（约 30 分钟）

### 3.1 代码入库

```bash
# 在本地仓库（若尚未初始化）
cd E:\ICT
git init
git add .
git commit -m "chore: M0-M2 交付基线"

# 关联远程仓库（CodeArts / Gitee / GitHub 按 §0 的决策选择）
git remote add origin <仓库地址>
git branch -M main
git push -u origin main
```

> ⚠ 推送前确认 `.env` 未被提交：`git log --all -- .env` 应为空。
> `.vendor/` 与 `node_modules/` 已在 `.gitignore` 中排除，不会入库。

### 3.2 服务器环境

```bash
# SSH 登录（用 §2.5 的弹性公网 IP 与密钥对）
ssh -i <私钥> ubuntu@<公网IP>

# 安装 Docker 与 Compose 插件
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker          # 或重新登录使组权限生效

# 国内网络建议配置镜像加速（可选）
sudo mkdir -p /etc/docker
echo '{"registry-mirrors":["https://docker.mirrors.ustc.edu.cn"]}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker

# 验证
docker --version && docker compose version

# 拉取代码
sudo mkdir -p /opt/shiguang && sudo chown $USER:$USER /opt/shiguang
git clone <仓库地址> /opt/shiguang
cd /opt/shiguang
```

---

## 4. 配置环境变量（约 20 分钟）

### 4.1 生成密钥

```bash
# 应用密钥（≥32 字节）
python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# 数据库 / Redis 密码
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

### 4.2 创建 `.env`

```bash
cd /opt/shiguang
cp .env.example .env
vi .env
```

**必须修改的项（逐项核对）**

```env
APP_ENV=production                          # 必须是 production
APP_SECRET_KEY=<§4.1 生成的 48 字节随机串>
CORS_ORIGINS=https://<你的域名或公网IP>       # 不能留 localhost

# ---- RDS（用内网地址，不要用公网地址）----
DATABASE_URL=mysql+pymysql://shiguang_app:<密码>@<RDS内网地址>:3306/shiguang?charset=utf8mb4

# ---- DAS Redis（内网地址）----
REDIS_URL=redis://:<密码>@<Redis内网地址>:6379/0
REDIS_ENABLED=true

# ---- OBS ----
STORAGE_BACKEND=obs                          # 生产必须是 obs，不能是 local
OBS_ENDPOINT=https://obs.<区域>.myhuaweicloud.com
OBS_REGION=<区域>
OBS_BUCKET=<桶名>
OBS_ACCESS_KEY=<shiguang-app 的 AK>
OBS_SECRET_KEY=<shiguang-app 的 SK>

# ---- MaaS（未开通则保持 false）----
MAAS_ENABLED=false
MAAS_ENDPOINT=https://replace_me
MAAS_API_KEY=replace_me
MAAS_MODEL=replace_me

# ---- 算法服务（容器内固定值，不要改）----
ALGORITHM_SERVICE_URL=http://algorithm:9000

# ---- Compose 编排用 ----
MYSQL_ROOT_PASSWORD=<生成>
MYSQL_USER=shiguang_app
MYSQL_PASSWORD=<生成>
REDIS_PASSWORD=<生成>
HTTP_PORT=80
```

**防错检查**

```bash
# 确认没有残留 replace_me
grep -n "replace_me" .env && echo "⚠ 仍有占位符未替换" || echo "✓ 占位符已全部替换"

# 确认 .env 不会被提交
grep -q "^\.env$" .gitignore && echo "✓ .gitignore 已忽略 .env"
git status --short | grep -q ".env" && echo "⚠ .env 出现在 git 状态中" || echo "✓ .env 未被跟踪"
```

---

## 5. 初始化数据库（约 15 分钟）

### 5.1 授权应用账号

用 RDS 管理员账号连接，执行最小权限授权（**不要**给应用账号 root）：

```sql
CREATE USER IF NOT EXISTS 'shiguang_app'@'%' IDENTIFIED BY '<§4.1 生成的密码>';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`users`       TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`items`       TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`item_images` TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`matches`     TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`claims`      TO 'shiguang_app'@'%';
-- ⚠ 审计日志只给 SELECT/INSERT：从数据库层保证不可篡改
GRANT SELECT, INSERT                 ON `shiguang`.`audit_logs`  TO 'shiguang_app'@'%';
FLUSH PRIVILEGES;
```

> 若在本地已用 `init_db.sql` 建过表，云端**不要**再手工建表，直接用 Alembic 迁移（下一步），
> 否则会出现「表已存在但 alembic_version 为空」的冲突（见 [troubleshooting.md](troubleshooting.md) §6.1）。

### 5.2 执行迁移

```bash
cd /opt/shiguang
docker compose up -d mysql redis algorithm        # 先起依赖（云端实际用 RDS/DAS，这几个容器仅本地编排时用）
docker compose run --rm backend alembic upgrade head
```

> **注意**：云端 `DATABASE_URL` 指向 RDS，compose 里的 mysql 容器不会被使用。
> 若希望完全不启本地 mysql/redis 容器，可用 §6 的「精简部署」变体。

### 5.3 导入演示账号与数据

```bash
# 演示账号（答辩必需）
docker compose run --rm backend python /app/scripts/seed_demo_data.py --users-only

# 演示数据集（60 条物品；答辩演示更丰富）
docker compose run --rm backend python /app/scripts/seed_demo_data.py --items-only
```

**验证数据已入库**

```bash
mysql -h <RDS内网地址> -u shiguang_app -p -e "
SELECT 'users' AS t, COUNT(*) AS n FROM shiguang.users
UNION ALL SELECT 'items', COUNT(*) FROM shiguang.items
UNION ALL SELECT 'matches', COUNT(*) FROM shiguang.matches
UNION ALL SELECT 'claims', COUNT(*) FROM shiguang.claims
UNION ALL SELECT 'audit_logs', COUNT(*) FROM shiguang.audit_logs;"
```

---

## 6. 启动应用（约 15 分钟）

```bash
cd /opt/shiguang

# 构建镜像（首次约 5~10 分钟）
docker compose build

# 启动
docker compose up -d

# 查看状态（frontend 应为 healthy）
docker compose ps

# 跟踪日志确认无报错
docker compose logs -f backend
```

**启动成功的标志**

| 检查 | 期望 |
|---|---|
| `docker compose ps` | backend / frontend 为 `Up`，frontend 最终 `(healthy)` |
| backend 日志 | 出现 `Redis 连接成功` 或 `Redis 不可用，已降级`（两者都可接受） |
| backend 日志 | **不应**出现 `拒绝启动` 或缺失环境变量的报错 |

> 生产环境启动时会**强校验**必填环境变量（`APP_SECRET_KEY` / `DATABASE_URL` / `REDIS_URL` / `OBS_*`），
> 缺失或仍为 `replace_me` 时**拒绝启动**并在日志中列出缺失的变量名（不打印值）。这是设计行为。

---

## 7. 部署后验证（约 20 分钟）—— **不可跳过**

### 7.1 本地健康检查

```bash
# 在服务器上
curl -s http://localhost/health | python3 -m json.tool
```

**逐项确认**（响应中**不得**出现任何密钥）：

| 组件 | 期望 |
|---|---|
| `app.status` | `ok` |
| `database.status` | `ok`（**关键**，指向 RDS） |
| `redis.status` | `ok` 或 `degraded` |
| `obs.status` | `ok`（`STORAGE_BACKEND=obs` 时应为 ok） |
| `algorithm_service.status` | `ok` |
| `maas.status` | `ok` 或 `not_configured` |

### 7.2 外网访问验证

**从你自己的电脑**（不是服务器）执行：

```powershell
# 健康检查
curl.exe -s http://<公网IP>/health

# 端到端验收（推荐，一条命令走完整流程）
python scripts\e2e_demo.py --base-url http://<公网IP>
```

`e2e_demo.py` 会自动验证：发布失物/拾物 → 计算匹配 → 总分等于加权和 → 发起认领 → 错误答案不通过 → 正确答案进入待审核 → 管理员审核通过 → 状态归档 → 审计日志 → 越权拦截。

**全部通过即代表部署成功。**

### 7.3 验收清单（需求 §12.4）

逐项实测并勾选：

- [ ] 外网浏览器可访问首页
- [ ] 演示账号可登录
- [ ] 上传图片成功写入 **OBS**（控制台可见对象，Key 形如 `items/2026/10/<uuid>.jpg`）
- [ ] 业务数据真实写入 **RDS**（用内网地址连接，非本地库）
- [ ] **Redis** 至少一个核心流程真实读写（健康检查 `redis.status=ok`，且匹配缓存命中）
- [ ] AI/算法接口可调用，且断开后能降级
- [ ] 完成一次完整的认领审核流程
- [ ] **重启后数据仍存在**（见 §7.4）
- [ ] `/health` 可分别报告各组件状态
- [ ] `/health` 响应中**不含任何密钥**
- [ ] `.env` 未进入 Git
- [ ] 日志中无密钥、无答案明文、无 Authorization

### 7.4 重启恢复验证（需求 F14）

```bash
cd /opt/shiguang
docker compose restart
sleep 20
docker compose ps
python3 scripts/healthcheck.py --url http://localhost:8000 || curl -s http://localhost/health
```

重启后**再次跑一次** `e2e_demo.py --skip-write`，确认数据未丢失。

---

## 8. 加固：HTTPS 与安全收尾（约 20 分钟）

### 8.1 配置 HTTPS

**方式一（推荐）：云负载均衡终止 TLS**

在 ELB 控制台创建负载均衡，绑定域名与证书，后端指向 ECS:80。应用无需改动。

**方式二：Nginx + Let's Encrypt**

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <你的域名>
sudo systemctl status certbot.timer     # 确认自动续期
```

启用后取消 `deploy/nginx.conf` 末尾 HTTPS 段的注释并重启 frontend 容器。

**方式三：自签证书** —— 仅本地演示，浏览器会有警告，**不建议答辩使用**。

### 8.2 安全收尾

- [ ] 关闭 ECS 的密码登录，仅保留密钥对
- [ ] 安全组 22 端口限定到运维固定 IP
- [ ] 确认 OBS 桶为**私有**（控制台查看桶策略，不应有公共读）
- [ ] 确认 RDS / Redis 未开放公网
- [ ] 应用账号对 `audit_logs` **无 UPDATE/DELETE 权限**
- [ ] `APP_ENV=production`（此时演示一键登录接口自动返回 404）
- [ ] 生产环境考虑关闭 `/docs`（Swagger）或加访问控制
- [ ] 开启云监控告警（见 §9）

---

## 9. 可观测性与告警（可选但推荐）

| 指标 | 建议阈值 | 说明 |
|---|---|---|
| 接口 P95 延迟 | > 1s 告警 | 需求 §13.4 的性能基线 |
| 5xx 比例 | > 1% 告警 | |
| 算法服务调用失败率 | > 5% 告警 | 触发降级 |
| 数据库连接数 | > 80% 告警 | |
| ECS 磁盘使用率 | > 85% 告警 | |
| **云额度消耗** | 预算 80% 告警 | 防止演示前额度耗尽 |

**日志查看**

```bash
docker compose logs -f backend          # 实时
docker compose logs --tail=200 backend  # 最近 200 行
```

日志为结构化 JSON，含 `request_id`，可据此串联「Nginx → 后端 → 算法 → audit_logs」全链路。

---

## 10. 常见问题速查

完整排障见 [troubleshooting.md](troubleshooting.md)。部署阶段最常踩的五个坑：

| # | 现象 | 原因 | 处理 |
|---|---|---|---|
| 1 | 容器启动即退出，日志说缺环境变量 | `.env` 有项仍是 `replace_me`，生产强校验拒绝启动 | `grep replace_me .env` 逐项替换 |
| 2 | 健康检查 `database.status=down` | 用了 RDS **公网地址**，或安全组未放行 ECS 网段 | 改用内网地址 |
| 3 | 图片上传报 `STORAGE_SERVICE_ERROR` | Endpoint 区域与桶区域不一致 / AK 无该桶权限 | 核对 `OBS_ENDPOINT` 与桶区域 |
| 4 | 迁移报 `Table 'users' already exists` | 手工建过表但 `alembic_version` 为空 | `alembic stamp 0001`（见 troubleshooting §6.1） |
| 5 | 页面白屏、刷新 404 | Nginx 未配 SPA 回退（`deploy/nginx.conf` 被改） | 恢复 `try_files $uri $uri/ /index.html` |

---

## 11. 更新与回滚

### 11.1 发布新版本

```bash
cd /opt/shiguang
git pull
docker compose build
docker compose up -d
docker compose exec backend alembic upgrade head    # 若有新迁移
python3 scripts/healthcheck.py --url http://localhost:8000
```

### 11.2 回滚

```bash
# 回滚代码
git log --oneline -5
git checkout <上一个可用 commit>
docker compose up -d --build

# 回滚数据库迁移（谨慎：先备份）
docker compose exec backend alembic downgrade -1
```

> ⚠ 回滚数据库前**必须先备份**：`mysqldump` 导出后再执行 downgrade。

---

## 12. 交付与资源释放

### 12.1 交接清单（需求 §14.4）

把以下内容填入 [cloud-resources.md](cloud-resources.md)：

- 云区域、RDS 实例名与库名、Redis 实例信息、OBS 桶名与对象前缀
- 云服务器信息、部署版本号（Git commit SHA）
- 环境变量清单（**只列变量名，值单独交接**）
- 备份方式与恢复演练记录
- 资源到期日与费用说明
- 关闭与释放步骤

**密码与 Secret 必须通过安全渠道单独交接**，不得写进 Markdown、聊天记录或提交记录。

### 12.2 资源释放（比赛结束后）

按此顺序操作，**先备份后释放**：

```bash
# 1) 导出最终数据
mysqldump -h <内网地址> -u shiguang_app -p --single-transaction \
  --routines --triggers shiguang > backup_final_$(date +%Y%m%d).sql
gzip backup_final_*.sql

# 2) 导出编排与版本快照
docker compose config > compose_resolved.yml
docker compose exec backend alembic current > alembic_version.txt

# 3) 停止应用
docker compose down
```

| 顺序 | 操作 | 注意事项 |
|---:|---|---|
| 1 | 停止 ECS | 停止即停止计算计费 |
| 2 | 释放弹性公网 IP / 带宽 | 未绑定的 EIP 仍可能计费 |
| 3 | 删除 ELB | 先解绑后端服务器组 |
| 4 | **先做最终快照**，再删除 RDS | 删除后快照默认保留一段时间 |
| 5 | 删除 DAS Redis | 缓存可重建，无需备份 |
| 6 | 确认已备份后删除 OBS 桶 | 先清空对象，注意生命周期规则 |
| 7 | **删除临时 AK/SK 与 IAM 子用户** | ⚠ **务必执行**，未清理的凭证是长期隐患 |
| 8 | 释放域名与证书 | 注意自动续费 |

---

## 13. 时间估算

| 阶段 | 预估 | 可并行 |
|---|---|---|
| §1 账号与权限 | 30 min | 与 §2 部分并行 |
| §2 开通资源 | 40 min | 各项可并行申请 |
| §3 代码入库与服务器 | 30 min | 可与 §2 并行 |
| §4 配置环境变量 | 20 min | 需 §2 完成 |
| §5 初始化数据库 | 15 min | 需 §2.1 完成 |
| §6 启动应用 | 15 min | 需 §4、§5 完成 |
| §7 部署后验证 | 20 min | — |
| §8 HTTPS 与加固 | 20 min | — |
| **合计** | **约 3.5 小时** | 顺利情况下半天内可完成 |

**建议留出 1 天缓冲**：云资源开通可能因实名认证、额度审批、安全组调试而延迟。

---

## 14. 一句话流程

```text
定账号归属 → 建 IAM 子用户 → 开 RDS/Redis/OBS/MaaS/ECS
    → 代码入库 → ECS 装 Docker → 写 .env（密钥从 §4.1 生成）
    → RDS 授权最小权限 → alembic upgrade head → 导入演示账号
    → docker compose up -d → /health 六项检查 → 外网 e2e_demo.py
    → 配 HTTPS → 安全收尾 → 填 cloud-resources.md → 交接
```

---

## 附录 A · 部署前自检

执行以下命令确认静态前置条件齐备（**不需要 Docker 与云资源**）：

```powershell
cd E:\ICT
python scripts\preflight_cloud.py
```

它会检查：Docker 构建源文件是否齐全、环境变量模板是否合规、编排与网络隔离是否正确、
Nginx 配置是否完整、是否存在硬编码密钥、数据库初始化脚本是否完备、验证工具是否就绪。

**0 阻塞项**才建议进入云端部署。

## 附录 B · 部署后验收一键命令

```powershell
# 1) 产物与契约自检（本地，不需要服务）
python scripts\verify_m0.py

# 2) 健康检查（云端）
python scripts\healthcheck.py --url http://<公网IP>

# 3) 端到端完整流程（云端，会写入测试数据）
python scripts\e2e_demo.py --base-url http://<公网IP>

# 4) 只读复验（重启后使用）
python scripts\e2e_demo.py --base-url http://<公网IP> --skip-write
```

## 附录 C · 变更记录

| 版本 | 日期 | 变更人 | 变更内容 |
|---|---|---|---|
| 1.0 | 2026-10-13 | 部署准备 | 首次编写：账号与权限、资源开通、配置、初始化、启动、验证、加固、交付与释放全流程 |
