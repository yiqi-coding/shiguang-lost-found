# 拾光 · 云资源清单与交接记录

> 状态：**待填写**（M4 华为云部署阶段完成后填写完整）
>
> ⚠ **安全红线**：本文件**只记录资源标识与配置方式**，任何密码、AK/SK、Token、连接串中的密码段
> **一律不得**写入本文件、其他 Markdown、聊天记录或提交记录。密钥必须通过安全渠道单独交接
> （如密码管理器、加密压缩包线下交付、云平台密钥管理服务）。
>
> 版本：Baseline v1.0 ｜ 创建日期：2026-09-30

---

## 1. 资源总览

| # | 资源 | 服务 | 区域 | 规格 | 用途 | 状态 |
|---|---|---|---|---|---|---|
| 1 | RDS MySQL 实例 | RDS for MySQL 8.0 | 待填 | 待填 | 业务数据持久化 | ⬜ 未开通 |
| 2 | Redis 实例 | DAS Redis | 待填 | 待填 | 匹配/AI 属性缓存、认领会话 | ⬜ 未开通 |
| 3 | 对象存储桶 | OBS | 待填 | 标准存储 | 图片存储 | ⬜ 未开通 |
| 4 | 模型服务 | MaaS | 待填 | 按额度 | 属性提取、问题生成、可信度辅助 | ⬜ 未开通 |
| 5 | 云服务器 | ECS | 待填 | 2 vCPU / 4 GB | 运行前后端容器 | ⬜ 未开通 |
| 6 | 负载均衡（可选） | ELB | 待填 | 待填 | HTTPS 终止 | ⬜ 未开通 |
| 7 | 域名（可选） | DNS | — | — | 外网访问 | ⬜ 未开通 |

---

## 2. 逐项详情（M4 填写）

### 2.1 RDS MySQL

| 项 | 值 |
|---|---|
| 云区域 / 可用区 | 待填 |
| 实例名称 | 待填 |
| 实例 ID | 待填 |
| 内网地址（主机） | 待填 |
| 端口 | 3306 |
| 数据库名 | `shiguang` |
| 字符集 / 排序规则 | `utf8mb4` / `utf8mb4_0900_ai_ci` |
| 应用账号 | `shiguang_app`（密码单独交接） |
| 账号权限 | 业务表 SELECT/INSERT/UPDATE/DELETE；`audit_logs` 仅 SELECT/INSERT |
| 是否开放公网 | ❌ 否（仅内网访问） |
| 自动备份 | 待填（建议开启，保留 7 天） |
| 备份窗口 | 待填（建议凌晨低峰） |
| 参数组调整 | `wait_timeout` 待填（需大于 `DB_POOL_RECYCLE=3600`） |
| 数据表清单 | `users` / `items` / `item_images` / `matches` / `claims` / `audit_logs` / `alembic_version` |
| 迁移版本 | 待填（`alembic current` 的输出） |

### 2.2 Redis

| 项 | 值 |
|---|---|
| 云区域 | 待填 |
| 实例名称 / ID | 待填 |
| 内网地址 | 待填 |
| 端口 | 6379 |
| 库索引 | 0 |
| 密码 | 单独交接 |
| 最大内存 / 淘汰策略 | 建议 256MB / `allkeys-lru` |
| 是否开放公网 | ❌ 否 |
| 使用的键空间 | `ai:attributes:item:*` / `match:item:*` / `claim:session:*` / `rate:*` |
| 持久化 | 建议开启 AOF（缓存可重建，非关键） |

### 2.3 OBS

| 项 | 值 |
|---|---|
| 云区域 | 待填 |
| 桶名 | 待填（**不得**为公共读） |
| Endpoint | 待填 |
| 对象前缀 | `items/` |
| 对象 Key 规则 | `items/{yyyy}/{mm}/{uuid}.{ext}` |
| 桶权限 | **私有**；访问一律使用签名 URL |
| 签名 URL 有效期 | 900 秒（`OBS_SIGNED_URL_EXPIRE`） |
| CORS 配置 | 待填（若使用前端直传，需允许前端域名与 `PUT/POST`） |
| 生命周期规则 | 待填（建议演示结束后转归档或删除，控制成本） |
| 访问凭证 | 建议使用 IAM 子账号 + 最小权限策略；AK/SK 单独交接 |
| 预估存储量 | 演示规模 < 1 GB |

### 2.4 MaaS / 模型服务

| 项 | 值 |
|---|---|
| 云区域 | 待填 |
| Endpoint | 待填 |
| 模型名称 | 待填 |
| API Key | 单独交接 |
| 额度 / 计费方式 | 待填 |
| 额度预警阈值 | 待填 |
| 调用场景 | ① 属性提取 ② 验证问题生成 ③ 匹配解释 ④ 可信度辅助 |
| 降级策略 | 未配置或不可用时自动降级为规则实现（`MAAS_ENABLED=false`） |
| 提示词版本 | 属性提取 `v1.2`；问题生成 `v1.0`（记录于 `items.ai_prompt_version`） |

### 2.5 云服务器 / 容器

| 项 | 值 |
|---|---|
| 云区域 / 可用区 | 待填 |
| 实例规格 | 待填 |
| 操作系统 | 待填（建议 Ubuntu 22.04 LTS） |
| 公网 IP | 待填 |
| 弹性公网带宽 | 待填 |
| 安全组规则 | 入：443（`0.0.0.0/0`）、80（`0.0.0.0/0`）、22（运维固定 IP）；出：443（访问 OBS/MaaS） |
| 部署目录 | `/opt/shiguang` |
| 容器编排 | Docker Compose（5 个服务：mysql/redis/algorithm/backend/frontend） |
| 部署版本号 | 待填（建议用 Git commit SHA 标记） |
| 日志查看 | `docker compose logs -f backend` |

### 2.6 访问入口

| 项 | 值 |
|---|---|
| 前端访问地址 | 待填（如 `https://demo.example.com`） |
| 后端 API 基址 | 待填（如 `https://demo.example.com/api/v1`） |
| Swagger 文档 | 待填（生产环境建议关闭或加访问控制） |
| 健康检查 | 待填（`https://.../health`） |
| HTTPS 终止方式 | 待填（ELB / Nginx + Let's Encrypt / 自签） |
| 证书到期日 | 待填 |

---

## 3. 环境变量交接清单

**只列变量名与说明，值单独交接。**

| 变量名 | 是否敏感 | 说明 |
|---|:--:|---|
| `APP_ENV` | 否 | 生产环境填 `production` |
| `APP_SECRET_KEY` | 🔴 **敏感** | ≥32 字节随机串，JWT 签发与答案加盐共用 |
| `DATABASE_URL` | 🔴 **敏感** | 含数据库密码 |
| `REDIS_URL` | 🔴 **敏感** | 含 Redis 密码 |
| `OBS_ENDPOINT` / `OBS_BUCKET` / `OBS_REGION` | 否 | 资源标识 |
| `OBS_ACCESS_KEY` / `OBS_SECRET_KEY` | 🔴 **敏感** | 存储访问凭证 |
| `MAAS_ENDPOINT` / `MAAS_MODEL` | 否 | 服务地址与模型名 |
| `MAAS_API_KEY` | 🔴 **敏感** | 模型服务密钥 |
| `MAAS_ENABLED` | 否 | 是否启用真实模型 |
| `ALGORITHM_SERVICE_URL` | 否 | 容器内固定为 `http://algorithm:9000` |
| `ALGORITHM_FALLBACK_ENABLED` | 否 | 是否允许降级 |
| `STORAGE_BACKEND` | 否 | 生产必须为 `obs` |
| `MATCH_TOP_K` / `MATCH_MIN_DISPLAY_SCORE` | 否 | 匹配展示参数 |
| `CLAIM_MAX_ATTEMPTS` | 否 | 认领最大验证次数 |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` / `REDIS_PASSWORD` | 🔴 **敏感** | 仅 Docker Compose 编排使用 |

> 🔴 敏感项**共 8 个**（`APP_SECRET_KEY`、`DATABASE_URL`、`REDIS_URL`、`OBS_ACCESS_KEY`、
> `OBS_SECRET_KEY`、`MAAS_API_KEY`、`MYSQL_PASSWORD`、`REDIS_PASSWORD`，root 密码另计）。
> 交接时逐一核对，确认接收方已能独立启动服务后再销毁交接渠道。

---

## 4. 备份方式

| 项 | 方案 | 频率 | 保留期 | 责任人 |
|---|---|---|---|---|
| RDS 自动备份 | 控制台开启 | 每日 | 7 天 | BE |
| 手动快照 | 演示前 + 结构调整前 | 按需 | 至项目结束 | BE |
| 逻辑备份 | `mysqldump --single-transaction --routines --triggers` → 上传 OBS | 每周 | 30 天 | BE |
| 配置备份 | `.env` 加密后存于密码管理器 | 变更时 | 至项目结束 | PM |
| 代码备份 | Git 远程仓库 + 每人本地克隆 | 每次提交 | 永久 | 全员 |
| OBS 图片 | 桶版本控制或跨区域复制（可选） | — | — | BE |

**恢复演练记录**

| 日期 | 操作 | 结果 | 执行人 |
|---|---|---|---|
| 待填 | 从备份恢复到新实例 → 校验行数 → 重启应用 → 跑健康检查 | 待填 | 待填 |

---

## 5. 到期与费用

| 项 | 值 |
|---|---|
| 云资源到期日 | 待填 |
| 免费额度 / 优惠券 | 待填 |
| 预估日费用 | 待填 |
| 计费项明细 | RDS 实例费 + Redis 实例费 + OBS 存储与流量费 + ECS 计算与带宽费 + MaaS 调用费 |
| 费用预警 | 建议设置在预算 80% 处告警 |
| 谁负责缴费 | 待填 |

---

## 6. 关闭与释放步骤

**比赛结束或需要停用时，按以下顺序操作**（先备份，后释放）：

```bash
# 步骤 1：导出最终数据
mysqldump -h <内网地址> -u shiguang_app -p --single-transaction \
  --routines --triggers shiguang > backup_final_$(date +%Y%m%d).sql
gzip backup_final_*.sql

# 步骤 2：导出编排与配置快照（不含密钥值）
docker compose config > compose_resolved.yml
docker compose exec backend alembic current > alembic_version.txt

# 步骤 3：停止应用
cd /opt/shiguang && docker compose down

# 步骤 4：确认备份可用（在临时实例上恢复验证后再继续）
```

| 顺序 | 操作 | 控制台位置 | 注意事项 |
|---:|---|---|---|
| 1 | 停止 ECS / 容器实例 | ECS 控制台 | 停止即停止计算计费 |
| 2 | 释放弹性公网 IP / 带宽 | EIP 控制台 | 未绑定的 EIP 仍可能计费 |
| 3 | 删除 ELB | ELB 控制台 | 先解绑后端服务器组 |
| 4 | **先做最终快照**，再删除 RDS | RDS 控制台 | 删除后快照默认保留一段时间 |
| 5 | 删除 DAS Redis 实例 | DAS 控制台 | 缓存可重建，无需备份 |
| 6 | 确认已备份后删除 OBS 桶 | OBS 控制台 | 先删生命周期规则，再清空对象 |
| 7 | 删除临时 AK/SK 与 IAM 子账号 | IAM 控制台 | **务必执行**，防止凭证残留 |
| 8 | 释放域名与证书 | DNS / 证书控制台 | 记录到期日，避免自动续费 |

> ⚠ **不要跳过第 7 步**。未清理的访问凭证是长期安全隐患，即使资源已释放。

---

## 7. 交接确认

| 交接项 | 交付方 | 接收方 | 日期 | 确认 |
|---|---|---|---|---|
| 云区域与资源清单（本文件 §1–2） | 待填 | 待填 | 待填 | ⬜ |
| 环境变量清单（§3） | 待填 | 待填 | 待填 | ⬜ |
| 敏感凭证（安全渠道，**不在本文件**） | 待填 | 待填 | 待填 | ⬜ |
| 部署版本号与 Git commit | 待填 | 待填 | 待填 | ⬜ |
| 备份文件与恢复方式（§4） | 待填 | 待填 | 待填 | ⬜ |
| 到期与费用说明（§5） | 待填 | 待填 | 待填 | ⬜ |
| 关闭与释放说明（§6） | 待填 | 待填 | 待填 | ⬜ |
| 接收方独立完成一次部署与重启恢复 | 待填 | 待填 | 待填 | ⬜ |

> **交接完成标准**：接收方在**不依赖交付方口头指导**的情况下，能够独立完成
> ① 从源码构建 ② 执行迁移 ③ 导入数据 ④ 启动服务 ⑤ 通过外网访问 ⑥ 完成一次完整认领流程
> ⑦ 重启后数据不丢失。

---

## 8. 变更记录

| 日期 | 变更人 | 变更内容 |
|---|---|---|
| 2026-09-30 | M0 需求冻结会议 | 创建模板，列出待填写项与安全红线 |
