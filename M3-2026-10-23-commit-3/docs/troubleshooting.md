# 拾光 · 排障手册

> 适用对象：开发、部署与运维人员
>
> 版本：Baseline v1.0 ｜ 更新日期：2026-09-30
>
> 使用方式：先用 **§1 快速定位表** 找到症状，再跳到对应章节。

---

## 1. 快速定位表

| 症状 | 最可能的原因 | 直接跳转 |
|---|---|---|
| 页面白屏 / 打开是 404 | 前端未构建、Nginx 未配 SPA 回退 | §2.1 |
| 接口全部 401 | Token 过期 / `APP_SECRET_KEY` 变更 | §2.2 |
| 接口 403 | 越权访问（正常拦截） | §2.3 |
| 接口 500 | 未捕获异常 —— 看日志中的 `request_id` | §2.4 |
| 参数报错却返回 500 | 异常处理器未覆盖该异常类型 | §2.4 |
| 发布物品报「图片上传失败」 | OBS 配置错误 / 桶权限 / 网络不通 | §3.1 |
| 上传被拒「不支持格式」 | 校验五重拦截（正常行为） | §3.2 |
| AI 属性一直是 pending | MaaS 未启用 / 异步任务未执行 | §3.3 |
| 匹配结果为空 | 无候选 / 阈值过滤 / 算法服务异常 | §4.1 |
| 匹配分一直是 0 或明显不合理 | 权重配置未加载 / 地点未解析 | §4.2 |
| 提示「降级结果」 | 算法服务不可达，已走规则降级 | §4.3 |
| 缓存没生效 / 数据更新后仍是旧值 | 缓存未失效 | §5.1 |
| Redis 报错但业务正常 | 已按设计降级（正常） | §5.2 |
| 迁移报「表已存在」 | 手工执行过 DDL 但 Alembic 版本表为空 | §6.1 |
| 数据库连接超时 | 安全组 / 内网地址 / 连接池耗尽 | §6.2 |
| 状态改不动 / 报 409 | 状态机拦截了非法跳转（正常） | §7 |
| 普通用户看到了隐藏特征 | **严重安全问题** | §8 |
| 日志里出现密钥或答案明文 | **严重安全问题** | §8 |
| 容器启动即退出 | 环境变量缺失（生产环境强校验） | §9.1 |
| 健康检查 503 | 关键依赖 down | §9.2 |

---

## 2. 接口与请求层

### 2.1 页面白屏或刷新后 404

**原因**：单页应用使用 history 路由，服务器没有把所有路径回退到 `index.html`。

**排查**

```bash
# 确认前端产物存在
docker compose exec frontend ls -l /usr/share/nginx/html

# 确认 Nginx 配置含 SPA 回退
docker compose exec frontend grep -n "try_files" /etc/nginx/conf.d/default.conf
```

**修复**：`deploy/nginx.conf` 中 `location /` 必须包含：

```nginx
try_files $uri $uri/ /index.html;
```

修改后 `docker compose restart frontend`。

---

### 2.2 所有接口返回 401

**可能原因与处理**

| 原因 | 判断方法 | 处理 |
|---|---|---|
| Token 过期（默认 2 小时） | 重新登录后正常 | 正常行为；前端应自动跳登录 |
| `APP_SECRET_KEY` 被改过 | 所有旧 Token 同时失效 | 重新登录；**生产环境变更密钥前需公告** |
| 请求未带 `Authorization` 头 | 浏览器开发者工具看请求头 | 检查前端 axios 拦截器 |
| 反向代理丢弃了请求头 | Nginx 日志 | 确认 `proxy_set_header` 配置 |

```bash
# 直接验证后端鉴权是否正常
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo_student","password":"Demo@2026"}' | python -m json.tool
```

---

### 2.3 接口返回 403

这是**正常拦截**，通常意味着：

- 普通用户在读取他人记录的 `hidden_features`；
- 非管理员调用了 `/api/v1/admin/*`；
- 非本人尝试查看/操作他人的认领记录。

> ⚠ 如果**业务上本应允许**却返回 403，才需要排查权限判断逻辑（`backend/app/api/deps.py`）。

---

### 2.4 接口返回 500

**第一步：拿到 `request_id`**

错误响应体会带 `request_id`，页面提示中也会显示。用它检索日志：

```bash
docker compose logs backend | grep "req_7f3a9c21b0d4"
```

**第二步：对照常见原因**

| 日志特征 | 原因 | 修复 |
|---|---|---|
| `ValidationError` 未被处理却走了 500 | 异常处理器未覆盖 | 确认 `main.py` 注册了 `RequestValidationError` 处理器 |
| `OperationalError` / `Lost connection` | 数据库连接中断 | 见 §6.2 |
| `KeyError` / `AttributeError` | 代码缺陷 | 按堆栈修复，并补一条回归测试 |
| `Redis ConnectionError` 出现在业务路径 | 该处未做降级 | 缓存调用必须走 `services/cache.py`（内部已吞异常） |

> **红线**：参数错误必须返回 4xx。若发现参数错误返回 500，属于**必须修复的缺陷**（需求 §13.2）。

---

## 3. 文件与 AI

### 3.1 图片上传失败

**排查顺序**

```bash
# 1) 检查存储后端配置
docker compose exec backend python -c "
from app.core.config import settings
print('backend:', settings.STORAGE_BACKEND)
print('bucket:', settings.OBS_BUCKET)
print('endpoint:', settings.OBS_ENDPOINT)
print('key 是否配置:', bool(settings.OBS_ACCESS_KEY), bool(settings.OBS_SECRET_KEY))
"

# 2) 检查 OBS 连通性
docker compose exec backend python -c "
from app.services.storage import get_storage
print(get_storage().check_health())
"
```

**常见原因**

| 现象 | 原因 | 处理 |
|---|---|---|
| `StorageServiceError: OBS 配置缺失` | 环境变量未注入 | 检查 `.env` 与容器环境变量 |
| `OBS 上传失败（HTTP 403）` | AK/SK 无该桶写权限 | 检查 IAM 策略与桶策略 |
| `OBS 上传失败（HTTP 404）` | 桶名或区域错误 | 核对 `OBS_BUCKET` 与 `OBS_ENDPOINT` 的区域是否一致 |
| 连接超时 | 安全组未放行出方向 443 | 检查安全组与 NAT 网关 |
| 本地开发正常、云端失败 | 仍在使用 `STORAGE_BACKEND=local` | 改为 `obs` 并重启 |

> **设计上的降级**：含图片的请求失败会返回 `502 STORAGE_SERVICE_ERROR` 并提示「可去掉图片重试」；**无图片的发布不受影响**。

---

### 3.2 上传被拒（「不支持格式」/「文件扩展名与实际内容不符」）

这是**五重校验**在正常工作：

1. 扩展名白名单 → 2. 声明 Content-Type → 3. 魔数嗅探 → 4. 大小 ≤ 10MB → 5. Pillow 可解析

**验证一张图是否合法**

```bash
docker compose exec backend python -c "
from app.services.file_service import validate_image
data = open('/tmp/test.jpg','rb').read()
try:
    r = validate_image(data, filename='test.jpg', declared_mime='image/jpeg')
    print('通过：', r.mime_type, r.width, 'x', r.height)
except Exception as e:
    print('被拒：', e)
"
```

**注意**：把 `.png` 改名成 `.jpg` 会被第 3/7 步拦截，这是设计意图，不要放宽。

---

### 3.3 AI 属性一直显示 pending 或 failed

```bash
# 检查 MaaS 配置状态（不会消耗额度）
docker compose exec backend python -c "
from app.clients.maas_client import get_maas_client
print(get_maas_client().check_health())
"
```

| 结果 | 含义 | 处理 |
|---|---|---|
| `not_configured` | `MAAS_ENABLED=false` 或缺少 Endpoint/Key | 补齐环境变量并重启 |
| `ok` 但提取仍失败 | Endpoint 路径错误或模型名错误 | 手工调用一次看错误详情 |
| 调用超时 | 网络或额度问题 | 默认超时 30 秒可调 `MAAS_TIMEOUT` |

**手工触发一次提取**（会消耗额度）

```bash
curl -s -X POST http://localhost:8000/api/v1/items/1001/extract-attributes \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

> **重要**：AI 失败**不会**导致发布失败，只会把 `ai_status` 标为 `failed` 并允许手动填写属性。这是需求强制的降级行为，不要改成阻断。

---

## 4. 匹配与算法

### 4.1 匹配结果为空

**排查顺序**

```bash
# 1) 算法服务是否存活
curl -s http://localhost:9000/algorithm/health | python -m json.tool

# 2) 后端到算法服务是否通
docker compose exec backend python -c "
from app.clients.algorithm_client import get_algorithm_client
print(get_algorithm_client().check_health())
"

# 3) 是否有候选池（另一侧 post_type 的记录）
docker compose exec mysql mysql -u root -p -e "
SELECT post_type, category, status, COUNT(*) FROM shiguang.items GROUP BY post_type, category, status;
"
```

**常见原因**

| 原因 | 判断 | 处理 |
|---|---|---|
| 确实没有对应类别的候选 | 候选池为空 | 用演示数据补全 |
| 全部候选低于展示阈值 | 总分都 < 0.35 | 调整 `MATCH_MIN_DISPLAY_SCORE` 或补充描述 |
| 候选记录状态被排除 | 状态为 `closed` 等 | 检查候选召回的过滤条件 |
| 算法服务返回空 results | 算法日志 | 见 §4.3 |

---

### 4.2 匹配分明显不合理

**先验证权重是否被正确加载**

```bash
docker compose exec algorithm python -c "
from app.config_loader import load_config
cfg = load_config()
print('weights:', cfg.weights)
print('warnings:', getattr(cfg, 'warnings', []))
"
```

**检查三类常见问题**

| 现象 | 原因 | 处理 |
|---|---|---|
| 时间分恒为 0.5 | 时间未解析成功 | 检查 `event_time` 格式是否在 `matching.yaml` 的 `event_time_formats` 列表中 |
| 地点分恒为 0.5 | 地点未解析 | 检查 `config/locations.yaml` 是否包含该地点；补充地点表 |
| 图像分恒定 | 缺少图片，走了文本兜底 | 正常行为；补图可提升区分度 |
| 总分与加权和对不上 | 权重快照与当前配置不一致 | 查 `matches.weights_snapshot`，点「重新计算」刷新 |

**验证加权和（容差 1e-6）**

```bash
docker compose exec backend python -c "
from app.services.scoring import weighted_total, get_weights
w = get_weights()
s = {'category_score':1.0,'attribute_score':0.92,'image_score':0.81,'time_score':0.95,'location_score':1.0,'brand_model_score':0.5}
print('权重:', w, '总和:', sum(w.values()))
print('加权和:', weighted_total(s))
"
```

---

### 4.3 页面提示「降级结果」

**含义**：算法服务不可达，系统已自动降级为本地规则/Mock 匹配，**业务不中断**，但精度下降，且响应中 `degraded=true`。

**排查**

```bash
# 从后端容器内测试算法服务连通性
docker compose exec backend curl -s -m 5 http://algorithm:9000/algorithm/health

# 看算法服务日志
docker compose logs --tail=100 algorithm
```

**常见原因**

| 原因 | 处理 |
|---|---|
| 算法容器未启动 | `docker compose up -d algorithm` |
| 容器名/端口写错 | 确认 `ALGORITHM_SERVICE_URL=http://algorithm:9000` |
| 算法服务 OOM 被重启 | 看 `docker compose ps` 的退出码；减少 worker 数 |
| 内网隔离导致跨网络不通 | 确认 backend 与 algorithm 都在 `internal` 网络 |

> 若希望算法故障时**直接报错**而非降级，设置 `ALGORITHM_FALLBACK_ENABLED=false`。演示环境建议保持 `true`。

---

## 5. 缓存（Redis）

### 5.1 缓存不生效或数据更新后仍是旧值

**按设计**，以下操作必须触发缓存失效：

| 触发点 | 清理的键 |
|---|---|
| 物品描述/图片/状态变更 | `match:item:{id}:*`、`ai:attributes:item:{id}:*` |
| 认领验证提交完成 | `claim:session:{claim_id}` |
| 管理员审核完成 | 上述两类 |

**手工检查**

```bash
# 查看某物品的相关缓存键
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" --scan --pattern "match:item:1001:*"

# 手工清理（排障用）
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" --scan --pattern "match:item:1001:*" | xargs -r docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL
```

> TTL 本身也是兜底：匹配缓存 10 分钟、AI 属性 24 小时、认领会话 30 分钟。

---

### 5.2 Redis 报错但业务仍正常

这是**预期行为**。`services/cache.py` 的所有操作都捕获异常并静默降级为直连数据库，健康检查会显示 `degraded: true`。

若要让 Redis 恢复：

```bash
docker compose restart redis
# 或让后端重连
docker compose exec backend python -c "from app.services.cache import cache; print(cache.reconnect())"
```

---

## 6. 数据库

### 6.1 迁移报错

| 报错 | 原因 | 处理 |
|---|---|---|
| `Table 'users' already exists` | 手工执行过 `init_db.sql`，但 `alembic_version` 表为空 | 见下方「标记基线」 |
| `Can't locate revision` | 迁移文件缺失或版本号不一致 | 确认 `alembic/versions/` 完整 |
| `Duplicate column name` | 迁移被重复执行 | 检查 `alembic current` |

**标记基线（手工建表后让 Alembic 认为已是最新）**

```bash
docker compose exec backend alembic stamp 0001
docker compose exec backend alembic current
```

**从零重建（⚠ 会丢数据，仅演示环境）**

```bash
docker compose down -v          # 删除数据卷
docker compose up -d mysql
docker compose exec backend alembic upgrade head
docker compose exec backend python /app/scripts/seed_demo_data.py --all
```

---

### 6.2 数据库连接问题

```bash
# 1) 从后端容器测试连通性
docker compose exec backend python -c "
from app.db.session import check_database
import json; print(json.dumps(check_database(), ensure_ascii=False, indent=2))
"
```

| 现象 | 原因 | 处理 |
|---|---|---|
| `Access denied` | 账号密码错误 / 账号无该库权限 | 核对 `.env` 与 RDS 账号；检查最小权限 GRANT |
| `Unknown database` | 库未创建 | 执行 `scripts/init_db.sql` |
| 连接超时（云端） | 用了公网地址 / 安全组未放行 | 必须使用**内网地址**，安全组只允许 ECS 网段访问 3306 |
| `Lost connection during query` | 连接被 RDS 回收 | 已设 `pool_pre_ping` + `pool_recycle`；若仍出现，减小 `DB_POOL_RECYCLE` |
| `Too many connections` | 连接池过大 | 降低 `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`，或调大实例规格 |
| 迁移时锁表 | 大表加索引 | 使用 `ALGORITHM=INPLACE, LOCK=NONE` |

---

## 7. 状态流转报 409

**这是正常的**。请求的跳转不在状态机允许范围内，例如：

- 试图把 `published` 直接改成 `claimed`（必须先经匹配 → 认领 → 审核）；
- 试图把已经 `claimed` 的物品改回 `matched`；
- 同一用户对同一匹配重复发起认领。

**查看当前允许的下一状态**

```bash
docker compose exec backend python -c "
from app.services.state_machine import ITEM_TRANSITIONS
for k, v in ITEM_TRANSITIONS.items():
    print(f'{k:20} → {sorted(v) or \"（终态）\"}')
"
```

> **红线**：不要为了「让演示跑通」而绕过状态机直接改数据库。验收明确禁止通过修改数据库模拟关键流程。

---

## 8. 🚨 安全问题应急处理

### 8.1 普通用户看到了隐藏特征

**立即处理顺序**

1. **确认泄露路径**：是接口返回的，还是前端写死/缓存？
   ```bash
   curl -s http://localhost:8000/api/v1/items/1001 -H "Authorization: Bearer $STUDENT_TOKEN" \
     | python -c "import json,sys; d=json.load(sys.stdin); print('泄露!' if 'hidden_features' in str(d) else '未泄露')"
   ```
2. 若接口泄露 → 检查该路由是否绕过了 `services/serializers.py`，**回收到统一序列化函数**；
3. 若前端泄露 → 检查是否有本地缓存（`localStorage`/`sessionStorage`）存了完整响应；
4. 补一条 `tests/test_serializers.py` 的回归测试；
5. 评估影响范围（谁在什么时间访问过），必要时通知管理员与用户。

### 8.2 日志中出现密钥或答案明文

```bash
# 全量扫描日志
docker compose logs --no-color | grep -iE "bearer |password=|secret|api_key|Authorization|hidden_features" | head -50
```

1. 定位是哪条日志语句（`request_id` 能帮你串起来）；
2. 确认 `app/core/logging.py` 的 `SensitiveFilter` 覆盖了该字段名；若字段名特殊，**加入 `SENSITIVE_KEYS`**；
3. 清理已泄露的日志文件；
4. 若泄露的是**真实密钥** → **立即轮换密钥**（OBS AK/SK、MaaS Key、数据库密码、`APP_SECRET_KEY`），并检查是否有异常调用。

### 8.3 `.env` 被提交进 Git

```bash
# 确认是否真的进了历史
git log --all --oneline -- .env

# 从当前索引移除（若只是刚 add 还没 commit）
git rm --cached .env

# 若已进入历史：必须轮换所有密钥，然后用 git filter-repo / BFG 清理历史
# ⚠ 清理历史是破坏性操作，需团队协调后执行
```

> **原则**：泄露过的密钥一律视为已失效，必须轮换，不能只删文件。

### 8.4 审计日志被篡改

审计日志表在数据库层面**不授予应用账号 UPDATE/DELETE 权限**。若发现被改：

1. 检查应用账号权限是否被误放大；
2. 从 RDS 备份中恢复；
3. 排查是谁使用了 root 或高权限账号。

---

## 9. 部署与运行时

### 9.1 容器启动即退出

**生产环境会强校验必填环境变量**，缺失则拒绝启动（这是设计行为）。

```bash
docker compose logs backend | head -40
```

日志会列出缺失的**变量名**（不会打印值）。补齐后重启：

```bash
docker compose up -d backend
```

| 缺失项 | 处理 |
|---|---|
| `APP_SECRET_KEY` | 生成 ≥32 字节随机串：`python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` / `REDIS_URL` | 检查 `.env` 是否被 compose 正确加载（`env_file: .env`） |
| `OBS_*` | 检查 AK/SK 与桶名 |
| 值仍是 `replace_me` | 强校验会视为缺失，必须替换 |

---

### 9.2 健康检查返回 503

```bash
curl -s http://localhost:8000/health | python -m json.tool
# 或用仓库脚本（带退出码，适合 CI/监控）
python scripts/healthcheck.py --url http://localhost:8000
```

**判定规则**

| 依赖 | down 时的影响 |
|---|---|
| `database` | **关键**，服务不可用（503） |
| `app` | 进程本身异常 |
| `redis` | 可降级，业务正常但变慢 |
| `obs` | 含图发布失败，无图发布正常 |
| `algorithm_service` | 匹配降级为规则实现 |
| `maas` | AI 提取降级为规则提取 |

> `/health/live` 只测进程存活（用于存活探针），`/health/ready` 要求数据库可用（用于就绪探针）。

---

### 9.3 云端外网访问不通

```bash
# 1) 本机 → 云端 80/443
curl -v -m 10 http://<公网IP>/health

# 2) 云端服务器内部
ssh <user>@<host> "curl -s localhost/health | head -20"
ssh <user>@<host> "docker compose ps"
```

| 检查项 | 期望 |
|---|---|
| 安全组入方向 | 放行 80/443（源 `0.0.0.0/0`）；22 仅限运维 IP |
| 容器端口映射 | `frontend` 映射 `${HTTP_PORT}:80` |
| Nginx 运行 | `docker compose ps` 中 frontend 为 `Up (healthy)` |
| 后端健康 | `curl localhost/health` 返回 200 |
| 域名解析 | `nslookup <域名>` 指向正确 IP |

---

### 9.4 重启后数据丢失

| 数据 | 是否应持久化 | 检查 |
|---|---|---|
| MySQL 数据 | ✅ 应保留 | `docker volume ls` 是否有 `shiguang_mysql_data` |
| Redis 数据 | ⭕ 可有可无（缓存可重建） | 是否有 `shiguang_redis_data` 与 `--appendonly yes` |
| 本地存储图片 | ✅ 若用 `local` 后端 | 是否有 `shiguang_backend_storage` 卷 |
| OBS 图片 | ✅ 云端持久 | 与容器无关 |

> ⚠ 若执行过 `docker compose down -v`，数据卷会被删除，数据不可恢复 —— 这正是它只用于重建环境的原因。
>
> **验收要求（F14）**：普通 `docker compose restart` 后业务数据必须仍在。上线前务必实测一次。

---

## 10. 排障速查卡

```bash
# ---------- 一键体检 ----------
python scripts/verify_m0.py                      # 产物与契约自检
python scripts/healthcheck.py --url http://localhost:8000

# ---------- 服务状态 ----------
docker compose ps
docker compose logs --tail=200 backend
docker compose logs --tail=200 algorithm

# ---------- 跟随某一次请求 ----------
docker compose logs backend | grep "<request_id>"

# ---------- 数据库快检 ----------
docker compose exec mysql mysql -u root -p -e "
SELECT 'users' t, COUNT(*) n FROM shiguang.users
UNION ALL SELECT 'items', COUNT(*) FROM shiguang.items
UNION ALL SELECT 'matches', COUNT(*) FROM shiguang.matches
UNION ALL SELECT 'claims', COUNT(*) FROM shiguang.claims
UNION ALL SELECT 'audit_logs', COUNT(*) FROM shiguang.audit_logs;"

# ---------- 缓存快检 ----------
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" INFO keyspace
docker compose exec redis redis-cli -a "$REDIS_PASSWORD" --scan --pattern "match:*" | head

# ---------- 完全重建（⚠ 丢数据，仅演示环境） ----------
docker compose down -v && docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python /app/scripts/seed_demo_data.py --all
```

---

## 11. 上报问题时请附带

1. **时间点**（精确到分钟）；
2. **`request_id`**（页面错误提示或响应头 `X-Request-ID` 中）；
3. **复现步骤**（点了什么、期望什么、实际什么）；
4. **相关日志片段**（已脱敏，注意不要粘密钥）；
5. **环境**（本地 / 云端，`APP_ENV`，版本号 `GET /health` 中的 `version`）。
