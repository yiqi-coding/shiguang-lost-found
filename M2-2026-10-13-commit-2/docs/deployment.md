# 「拾光」部署方案（本地开发 + 华为云生产）

> 状态：**已冻结**（M0 里程碑交付物 D6）
>
> 适用版本：Baseline v1.0
>
> 环境现状（开发机实测 2026-09-30）：Python 3.11.9 ✅ · Node 24.16.0 ✅ · Git 2.47.1 ✅ · **Docker 未安装** ⚠
>
> 因此本文给出**两条并行路径**：路径 A「原生安装」用于当前开发机立即可用，路径 B「Docker Compose」用于云端与团队统一环境。

---

## 1. 两种部署形态对比

| 维度 | 路径 A · 原生安装 | 路径 B · Docker Compose |
|---|---|---|
| 适用场景 | 当前开发机（无 Docker）、快速联调 | 云端部署、团队环境统一、答辩环境 |
| 依赖 | 本机装 MySQL 8 / Redis 7 | 仅需 Docker + Compose |
| 启动速度 | 快（服务常驻） | 首次构建慢（镜像拉取） |
| 环境一致性 | 差（版本各异） | 好（镜像锁定） |
| 云端可用性 | 可用（华为云 ECS 裸机） | **推荐**（华为云 ECS + Compose，或 CCE） |
| 交付要求 | 需写清版本与安装步骤 | 必须提供 Dockerfile 与 compose 文件 |

> 需求说明书 §12.1 强制要求交付 Docker 相关文件，因此**路径 B 是必交付项**，路径 A 仅作为开发便利手段。

---

## 2. 组件与端口规划

| 组件 | 端口 | 说明 |
|---|---:|---|
| 前端（Nginx 静态 + 反代） | 80 / 443 | 生产；开发用 Vite 5173 |
| 后端 API（FastAPI/uvicorn） | 8000 | 生产用 gunicorn+uvicorn worker |
| 算法服务（FastAPI） | 9000 | **仅内网暴露**，不开放公网 |
| MySQL | 3306 | 本地容器；云端用 RDS，不暴露公网 |
| Redis | 6379 | 本地容器；云端用 DAS Redis |
| OBS | 443 | 华为云对象存储，走 HTTPS |

**网络暴露原则**

```text
公网 ──► 443 Nginx ──┬──► /            静态前端
                     ├──► /api/v1/*    后端 8000
                     └──► /health      后端 8000
                                  │
                          内网 ───┴──► algorithm:9000
                                     └──► mysql:3306 / redis:6379
算法服务、数据库、Redis 一律不暴露公网。
```

---

## 3. 环境变量清单（`.env.example`）

**下列为变量名与占位符，真实值通过部署平台注入，严禁写入仓库、镜像或文档。**

```env
# ---------- 应用 ----------
APP_ENV=development                # development | demo | production
APP_NAME=shiguang
APP_VERSION=1.0.0
APP_SECRET_KEY=replace_me_with_32_bytes_random
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO                     # DEBUG | INFO | WARNING | ERROR
CORS_ORIGINS=http://localhost:5173

# ---------- 数据库（本地 MySQL 或华为云 RDS） ----------
DATABASE_URL=mysql+pymysql://user:password@host:3306/shiguang?charset=utf8mb4
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_ECHO=false

# ---------- Redis（本地或华为云 DAS Redis） ----------
REDIS_URL=redis://:password@host:6379/0
REDIS_ENABLED=true                 # false 时自动降级为直连数据库
REDIS_TTL_AI_ATTRIBUTES=86400      # 24h
REDIS_TTL_MATCH=600                # 10min
REDIS_TTL_CLAIM_SESSION=1800       # 30min

# ---------- 对象存储（本地目录 或 华为云 OBS） ----------
STORAGE_BACKEND=local              # local | obs
LOCAL_STORAGE_DIR=./storage
OBS_ENDPOINT=https://obs.cn-north-4.myhuaweicloud.com
OBS_REGION=cn-north-4
OBS_BUCKET=replace_me
OBS_ACCESS_KEY=replace_me
OBS_SECRET_KEY=replace_me
OBS_SIGNED_URL_EXPIRE=900          # 签名 URL 有效期（秒）
OBS_KEY_PREFIX=items

# ---------- MaaS / 模型服务 ----------
MAAS_ENDPOINT=https://replace_me
MAAS_API_KEY=replace_me
MAAS_MODEL=replace_me
MAAS_TIMEOUT=30
MAAS_ENABLED=false                 # false 时走规则提取（降级）

# ---------- 算法服务 ----------
ALGORITHM_SERVICE_URL=http://algorithm:9000
ALGORITHM_TIMEOUT=30
ALGORITHM_FALLBACK_ENABLED=true    # 算法不可用时降级为本地规则匹配

# ---------- 匹配与业务参数 ----------
MATCH_TOP_K=5
MATCH_MIN_DISPLAY_SCORE=0.35
MATCH_CACHE_ENABLED=true
CLAIM_MAX_ATTEMPTS=3
UPLOAD_MAX_SIZE_MB=10
UPLOAD_MAX_COUNT=3
PAGE_SIZE_MAX=50

# ---------- 前端（构建期注入） ----------
VITE_API_BASE_URL=/api/v1
VITE_APP_TITLE=拾光
```

**校验要求（启动时执行）**

- `APP_ENV=production` 时：`APP_SECRET_KEY`、`DATABASE_URL`、`OBS_*`、`REDIS_URL` 必须非空且非 `replace_me`，否则**拒绝启动**并打印缺失项名称（不打印值）；
- 所有密钥类变量禁止出现在 `/health` 响应、日志与异常堆栈中；
- `.env` 必须列入 `.gitignore`，仓库只保留 `.env.example`。

---

## 4. 路径 A · 原生安装（当前开发机）

### 4.1 安装依赖

```powershell
# 1) MySQL 8 —— 推荐用 winget 或官方 installer
winget install Oracle.MySQL
# 安装后初始化：设置 root 密码，创建数据库与账号
mysql -u root -p -e "CREATE DATABASE shiguang DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"

# 2) Redis 7 —— Windows 建议用 Memurai 或 WSL2
winget install Memurai.MemuraiDeveloper
# 或 WSL2: wsl --install -d Ubuntu; sudo apt install redis-server

# 3) Python 依赖（已确认 3.11.9 可用）
cd E:\ICT\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 4) 前端依赖（Node 24.16.0 可用）
cd E:\ICT\frontend
npm install

# 5) 算法服务依赖
cd E:\ICT\algorithm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 4.2 初始化数据库与演示数据

```powershell
cd E:\ICT
Copy-Item .env.example .env    # 然后编辑 .env，填入本地 MySQL/Redis 连接串
cd backend
alembic upgrade head           # 建表
cd ..
python scripts\seed_demo_data.py --all   # 演示账号 + 60 条脱敏数据
```

### 4.3 启动（三个终端）

```powershell
# 终端 1 · 算法服务
cd E:\ICT\algorithm; python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload

# 终端 2 · 后端
cd E:\ICT\backend; python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
# Swagger: http://localhost:8000/docs

# 终端 3 · 前端
cd E:\ICT\frontend; npm run dev
# 页面: http://localhost:5173
```

### 4.4 健康检查

```powershell
python scripts\healthcheck.py --url http://localhost:8000/health
# 期望：app/database/redis/obs/algorithm_service 全部 ok；maas 可为 not_configured
```

---

## 5. 路径 B · Docker Compose

### 5.1 `Dockerfile`（多阶段，后端与算法服务共用基础镜像）

```dockerfile
# ---------- 构建阶段 ----------
FROM python:3.11-slim AS builder
WORKDIR /build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends gcc default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.11-slim
LABEL org.opencontainers.image.title="shiguang-backend" \
      org.opencontainers.image.version="1.0.0"

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 10001 appuser

COPY --from=builder /install /usr/local
WORKDIR /app
COPY backend/ /app/

USER appuser
EXPOSE 8000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health/live || exit 1

# 生产：gunicorn + uvicorn worker；本地可改为 uvicorn --reload
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "-w", "2", "-b", "0.0.0.0:8000", \
     "--timeout", "60", "--access-logfile", "-", "--error-logfile", "-"]
```

### 5.2 `frontend/Dockerfile`

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE_URL=/api/v1
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://localhost/ >/dev/null || exit 1
```

### 5.3 `algorithm/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY algorithm/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY algorithm/ /app/
RUN useradd -m -u 10002 algouser && chown -R algouser /app
USER algouser
EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9000/algorithm/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "2"]
```

### 5.4 `docker-compose.yml`

```yaml
services:
  mysql:
    image: mysql:8.0
    command: --default-authentication-plugin=mysql_native_password
             --character-set-server=utf8mb4 --collation-server=utf8mb4_0900_ai_ci
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD:?required}
      MYSQL_DATABASE: shiguang
      MYSQL_USER: ${MYSQL_USER:-shiguang_app}
      MYSQL_PASSWORD: ${MYSQL_PASSWORD:?required}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./scripts/init_db.sql:/docker-entrypoint-initdb.d/01_init.sql:ro
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-p${MYSQL_ROOT_PASSWORD}"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks: [internal]

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?required}", "--appendonly", "yes"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks: [internal]

  algorithm:
    build:
      context: .
      dockerfile: algorithm/Dockerfile
    environment:
      APP_ENV: ${APP_ENV:-production}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    expose: ["9000"]
    networks: [internal]
    restart: unless-stopped

  backend:
    build:
      context: .
      dockerfile: Dockerfile
    env_file: [.env]
    environment:
      DATABASE_URL: mysql+pymysql://${MYSQL_USER:-shiguang_app}:${MYSQL_PASSWORD}@mysql:3306/shiguang?charset=utf8mb4
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      ALGORITHM_SERVICE_URL: http://algorithm:9000
    depends_on:
      mysql: { condition: service_healthy }
      redis: { condition: service_healthy }
      algorithm: { condition: service_started }
    expose: ["8000"]
    networks: [internal, public]
    restart: unless-stopped

  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
        VITE_API_BASE_URL: /api/v1
    ports: ["80:80"]
    depends_on: [backend]
    networks: [public]
    restart: unless-stopped

volumes:
  mysql_data:
  redis_data:

networks:
  public:
  internal:
    internal: true     # 内网隔离：数据库/Redis/算法服务不可访问外网
```

### 5.5 `deploy/nginx.conf`

```nginx
server {
    listen 80;
    server_name _;
    client_max_body_size 12m;          # 略大于 10MB 上限，留出 multipart 开销

    # 安全响应头
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;   # SPA history 路由
    }

    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 60s;
    }

    location /health {
        proxy_pass http://backend:8000/health;
        access_log off;
    }
}
```

### 5.6 编排命令

```bash
# 首次部署
cp .env.example .env && vi .env          # 填入真实值（不得提交）
docker compose build
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python /app/../scripts/seed_demo_data.py --all   # 演示环境

# 日常
docker compose ps
docker compose logs -f backend
docker compose restart backend
docker compose down            # 保留数据卷
docker compose down -v         # ⚠ 删除数据卷，仅限重建环境
```

---

## 6. 华为云部署方案

### 6.1 资源清单

| 服务 | 规格建议 | 用途 | 备注 |
|---|---|---|---|
| RDS for MySQL 8.0 | 2 vCPU / 4 GB，单机或主备 | 业务数据 | 开启自动备份（保留 7 天），**不开放公网** |
| DAS Redis 5.0+ | 1 GB 主备 | 缓存与会话 | 设置密码，仅内网访问 |
| OBS | 标准存储，私有桶 | 图片存储 | 桶策略禁公开读，仅签名 URL |
| MaaS / 模型服务 | 按比赛额度 | 属性提取、问题生成、可信度辅助 | 密钥走环境变量 |
| ECS 或 CCE | 2 vCPU / 4 GB（ECS） | 运行前后端容器 | 安全组只放 80/443 |
| ELB / 域名（可选） | — | HTTPS 终止与负载 | 也可用 Nginx 自签/Let's Encrypt |

### 6.2 部署步骤（ECS + Docker Compose 路径）

```bash
# 1) 登录 ECS，安装 Docker
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# 2) 拉取代码
git clone <仓库地址> /opt/shiguang && cd /opt/shiguang

# 3) 配置环境变量（真实值仅存于服务器，不入库）
cp .env.example .env && vi .env
# 关键项：APP_ENV=production, DATABASE_URL 指向 RDS 内网地址,
#        REDIS_URL 指向 DAS 内网地址, OBS_* 与 MAAS_* 填真实值

# 4) 构建与启动
docker compose build
docker compose up -d

# 5) 执行数据库迁移（首次）
docker compose exec backend alembic upgrade head
docker compose exec backend python scripts/seed_demo_data.py --users-only

# 6) 校验
curl -s http://localhost/health | python -m json.tool
```

### 6.3 安全组与网络

| 安全组规则 | 方向 | 端口 | 源 | 说明 |
|---|---|---|---|---|
| HTTPS | 入 | 443 | 0.0.0.0/0 | 公网访问 |
| HTTP | 入 | 80 | 0.0.0.0/0 | 仅用于跳转 HTTPS |
| SSH | 入 | 22 | **运维固定 IP** | 禁止全网开放 |
| MySQL | 入 | 3306 | ECS 内网网段 | **绝不开放公网** |
| Redis | 入 | 6379 | ECS 内网网段 | **绝不开放公网** |
| 出方向 | 出 | 443 | 0.0.0.0/0 | 访问 OBS/MaaS |

### 6.4 HTTPS

方式一（推荐）：ELB 或 WAF 终止 HTTPS，后端只跑 HTTP；
方式二：Nginx + Let's Encrypt（需公网域名）：
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.example.com
```
方式三：自签证书（仅演示，浏览器会有警告，不推荐答辩使用）。

### 6.5 OBS 配置要点

- 桶权限：**私有**。禁止「公共读」，所有访问走**签名 URL**（默认 15 分钟有效）；
- 对象 Key：`items/{yyyy}/{mm}/{uuid4}.{ext}`，不使用原始文件名；
- 生命周期规则：演示结束后可配置 30 天转归档或删除，控制成本；
- CORS：若使用前端直传，需在桶上配置允许来源为前端域名、方法 `PUT/POST`；
- 密钥：使用**临时 AK/SK（IAM 委托）** 优于长期 AK/SK；至少做到专用子账号 + 最小权限策略。

### 6.6 最小权限数据库账号

```sql
CREATE USER 'shiguang_app'@'%' IDENTIFIED BY '<环境变量注入>';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`users`   TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`items`   TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`matches` TO 'shiguang_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`claims`  TO 'shiguang_app'@'%';
GRANT SELECT, INSERT                ON `shiguang`.`audit_logs` TO 'shiguang_app'@'%';
FLUSH PRIVILEGES;
```

> 应用账号对 `audit_logs` **无 UPDATE/DELETE 权限**，从数据库层保证审计日志不可篡改。

---

## 7. 健康检查与可观测性

### 7.1 探针

| 端点 | 用途 | 判定 |
|---|---|---|
| `GET /health/live` | 存活探针 | 进程可响应即 200 |
| `GET /health/ready` | 就绪探针 | 数据库可用才 200（Redis/OBS 降级不影响） |
| `GET /health` | 详细状态（人工排障与答辩展示） | 全量组件状态，失败时 503 |

### 7.2 日志

- 结构化 JSON 输出到 stdout（容器友好），字段含 `timestamp/level/logger/request_id/user_id/path/status/duration_ms`；
- **脱敏红线**：日志中禁止出现 `Authorization`、`password`、`secret`、`token`、隐藏特征明文、认领答案明文；通过统一的 logging Filter 过滤；
- 保留策略：本地 7 天轮转；云端可对接 LTS（云日志服务）；
- 关键链路用 `request_id` 贯穿：Nginx → 后端 → 算法服务 → `audit_logs`。

### 7.3 关键监控指标

| 指标 | 阈值告警 |
|---|---|
| 接口 P95 延迟 | > 1s 告警（需求 13.4） |
| 5xx 比例 | > 1% 告警 |
| 算法服务调用失败率 | > 5% 告警（触发降级） |
| Redis 命中率 | < 50% 关注 |
| 数据库连接池使用率 | > 80% 告警 |
| 磁盘使用率 | > 85% 告警 |

---

## 8. 失败降级矩阵（需求 13.5 落地）

| 失败服务 | 系统行为 | 用户感知 | 实现要点 |
|---|---|---|---|
| **MaaS** | 保存原始文本，`ai_status=failed` | 「AI 分析失败，可手动补充属性」，**发布成功** | 提取调用包 try/except，异常不冒泡到路由 |
| **算法服务** | 降级为本地规则/Mock 匹配，`degraded=true` | 页面橙色条「当前为规则匹配降级结果」 | `ALGORITHM_FALLBACK_ENABLED=true` |
| **Redis** | 直连数据库，`degraded=true` | 无感知（仅变慢） | 缓存层所有操作包异常捕获，失败即跳过 |
| **OBS** | 含图请求返回清晰错误；允许无图发布 | 「图片上传失败，可先去图发布或稍后重试」 | 存储抽象层切换 local/obs |
| **RDS** | 核心业务不可用 | 维护提示页 `/health/ready` 返回 503 | 连接失败返回 503 + 友好文案 |

**降级演练（M4 必做）**：依次停掉 Redis、算法服务、MaaS 配置，各验证一次核心流程仍可用。

---

## 9. 备份、恢复与容灾

| 项 | 方案 | 频率 | 责任人 |
|---|---|---|---|
| RDS 自动备份 | 控制台开启，保留 7 天 | 每日 | BE |
| 手动全量快照 | 演示前 + 结构调整前 | 按需 | BE |
| 逻辑备份 | `mysqldump --single-transaction` → OBS | 每周 | BE |
| 配置备份 | `.env` 加密存放于密钥管理（**不入库、不入 Git**） | 变更时 | PM |
| 恢复演练 | 恢复到新实例并校验行数与关键流程 | M4 至少一次 | QA |
| 代码备份 | Git 远程仓库 + 本地克隆 | 每次提交 | 全员 |

**恢复步骤**

```bash
# 1) 从快照/RDS 备份恢复实例
# 2) 校验表结构版本
docker compose exec backend alembic current
# 3) 校验数据行数与关键记录
mysql -h <host> -u <user> -p -e "SELECT 'users',COUNT(*) FROM shiguang.users UNION ALL SELECT 'items',COUNT(*) FROM shiguang.items;"
# 4) 重启应用并跑健康检查与端到端脚本
python scripts/healthcheck.py --url https://<域名>/health
python scripts/e2e_demo.py --base-url https://<域名>
```

---

## 10. 部署验收清单（需求 12.4）

- [ ] 外网浏览器可访问首页（HTTPS）
- [ ] 新用户/演示用户可正常登录
- [ ] 上传图片成功写入 **OBS**，数据库仅存对象 Key/URL
- [ ] 业务数据成功写入 **RDS**（非本地库，通过内网地址连接）
- [ ] **Redis** 至少一个核心流程真实读写（匹配缓存命中可观测）
- [ ] AI/算法接口可调用，且断开后能降级
- [ ] 完成一次完整的认领审核流程
- [ ] **重启应用后数据仍存在**（`docker compose restart` 后校验）
- [ ] `/health` 可分别报告应用/数据库/Redis/OBS/算法/MaaS 状态
- [ ] `/health` 响应中**不含任何密钥**
- [ ] `.env` 未进入 Git（`git log --all -- .env` 为空）
- [ ] 日志中无密钥、无答案明文、无 Authorization
- [ ] 云端日志可查看（`docker compose logs` 或 LTS）
- [ ] 资源到期/费用说明已记录，关闭与释放步骤已文档化

---

## 11. 资源释放（比赛结束后）

```bash
# 1) 导出最终数据与配置快照
mysqldump ... > backup_final.sql
docker compose config > compose_resolved.yml

# 2) 停止应用
docker compose down

# 3) 华为云控制台依次释放（按费用影响排序）
#    - ECS / CCE 容器实例（停止计费）
#    - ELB / 公网 IP / 带宽
#    - DAS Redis 实例
#    - RDS 实例（先做最终快照）
#    - OBS 桶（确认数据已备份后删除，注意生命周期规则）
#    - 删除临时 AK/SK 与子账号
```

> **交接要求**（需求 14.4）：云区域、RDS 实例名与库名、Redis 实例信息、OBS 桶名与前缀、云服务器信息、部署版本号、环境变量清单、备份方式、到期与费用说明、关闭释放步骤——**全部写入 `docs/cloud-resources.md`，但密码与 Secret 不得写进任何 Markdown 或聊天记录，须通过安全渠道单独交接。**

## 附录 · 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 破坏兼容 |
|---|---|---|---|---|
| 1.0 | 2026-09-30 | M0 需求冻结会议 | 首次冻结：双路径部署、Docker 全套文件、华为云资源与安全组、降级矩阵、验收清单 | — |
