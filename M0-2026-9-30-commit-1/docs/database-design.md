# 「拾光」数据库设计（Baseline v1.0）

> 状态：**已冻结**（M0 里程碑交付物 D3）
>
> 目标数据库：MySQL 8.0（本地开发）/ 华为云 RDS MySQL 8.0（云端）
>
> 迁移工具：Alembic（`backend/alembic/versions/`）
>
> 字符集与排序规则：`utf8mb4` / `utf8mb4_0900_ai_ci`（支持 emoji 与中文，大小写不敏感）
>
> 存储引擎：InnoDB
>
> 版本：与 `docs/api-contract.md` **v1.0 同步冻结**，字段名变更需三方确认。

---

## 1. 设计原则

1. **数据库是最终事实源**，Redis 只做缓存，任何缓存丢失都不影响数据正确性；
2. **敏感字段分表/分列存放**：`items.hidden_features` 与 `claims` 的标准答案永不出现在普通用户接口的查询路径中；
3. **状态只能由后端状态机推进**，不提供「直接改状态」的通用接口；
4. **所有时间字段统一 UTC 存储**（`DATETIME` 不带时区，应用层保证写入 UTC），API 输出时转 ISO 8601 带时区；
5. **金额/分数使用 `DECIMAL`**，不使用 `FLOAT`，保证 `total_score = Σ(权重×维度分)` 的可复现性；
6. **逻辑删除优先**：物品与用户使用 `status` 字段标记，不做物理删除（审计需要）；
7. **一切索引服务于已知查询**，不预建无用索引。

---

## 2. 实体关系

```text
users ──1:N──> items            (owner_user_id)
users ──1:N──> claims           (applicant_id)
users ──1:N──> audit_logs       (operator_id)

items(lost) ──┐
              ├──N:M──> matches ──1:N──> claims ──> audit_logs(target_type='claim')
items(found)──┘            │
                           └──> 冗余保存 6 个维度分 + 总分，便于审核页快速展示
```

约束规则：

- `matches.lost_item_id` 必须指向 `post_type='lost'` 的记录，`found_item_id` 必须指向 `post_type='found'`（由应用层校验 + 可选触发器兜底）；
- 同一对 `(lost_item_id, found_item_id)` 只保留一条记录，重算时 **UPSERT 更新分数**而非新增行；
- 一个 `match` 可被多个 `claim` 引用（例如多方尝试认领），但同一 `applicant_id` 对同一 `match_id` 同时只能有一个未关闭的认领（应用层 + 唯一索引部分实现）。

---

## 3. 表结构

### 3.1 `users`

| 字段 | 类型 | 约束 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | BIGINT UNSIGNED | PK, AUTO_INCREMENT | — | 用户 ID |
| `username` | VARCHAR(64) | NOT NULL, UNIQUE | — | 昵称/账号 |
| `role` | VARCHAR(20) | NOT NULL | `student` | `student` / `admin` |
| `campus_id` | VARCHAR(64) | NULL | NULL | 所属校区，如 `main` / `east` |
| `contact_id` | VARCHAR(128) | NULL | NULL | **已脱敏**联系方式（如 `138****8888`） |
| `password_hash` | VARCHAR(255) | NULL | NULL | bcrypt/argon2 哈希，禁用 MD5/SHA1 |
| `status` | VARCHAR(20) | NOT NULL | `active` | `active` / `disabled` |
| `last_login_at` | DATETIME | NULL | NULL | 最近登录时间（UTC） |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP ON UPDATE | 修改时间 |

索引：

- `PRIMARY KEY (id)`
- `UNIQUE KEY uk_users_username (username)`
- `KEY idx_users_role_status (role, status)`

> 说明：需求说明书 5.1 未含 `last_login_at`，此处为运营/审计需要**新增**（非破坏性扩展，已在变更记录登记）。

---

### 3.2 `items`

| 字段 | 类型 | 约束 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | BIGINT UNSIGNED | PK, AUTO_INCREMENT | — | 物品记录 ID |
| `owner_user_id` | BIGINT UNSIGNED | NOT NULL, FK→users(id) | — | 发布者 |
| `post_type` | VARCHAR(10) | NOT NULL | — | `lost` / `found` |
| `category` | VARCHAR(64) | NOT NULL | — | 类别枚举（见 API 契约 2.2） |
| `title` | VARCHAR(80) | NOT NULL | — | 标题 5~80 字 |
| `description` | TEXT | NOT NULL | — | 原始描述 10~2000 字 |
| `color` | VARCHAR(32) | NULL | NULL | 颜色 |
| `brand` | VARCHAR(64) | NULL | NULL | 品牌/型号 |
| `location` | VARCHAR(128) | NOT NULL | — | 校园地点（原始字符串） |
| `location_parsed` | JSON | NULL | NULL | 地点解析结果 `{campus,building,floor,spot}`，**新增字段**，用于地点分计算与缓存 |
| `event_time` | DATETIME | NOT NULL | — | 丢失/拾取时间（UTC） |
| `public_features` | JSON | NULL | NULL | 公开特征（数组或对象） |
| `hidden_features` | JSON | NOT NULL | — | **隐藏特征，严格权限**（数组） |
| `contact_id` | VARCHAR(128) | NULL | NULL | 发布者脱敏联系方式（冗余自 users，便于审核页展示） |
| `ai_attributes` | JSON | NULL | NULL | AI 属性提取结果（结构与算法契约 4.2 一致） |
| `ai_status` | VARCHAR(20) | NOT NULL | `pending` | `pending` / `success` / `failed` |
| `ai_model_version` | VARCHAR(64) | NULL | NULL | 模型名称+版本，便于复现与答辩 |
| `ai_prompt_version` | VARCHAR(32) | NULL | NULL | 提示词版本 |
| `ai_analyzed_at` | DATETIME | NULL | NULL | 分析时间 |
| `attributes_edited_by_user` | TINYINT(1) | NOT NULL | 0 | 用户是否手工修改过 AI 属性（**用户修改优先级高于 AI**） |
| `status` | VARCHAR(32) | NOT NULL | `published` | 业务状态，见 API 契约 2.3 |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

索引（对应已知查询）：

- `PRIMARY KEY (id)`
- `KEY idx_items_match_pool (post_type, category, status, event_time)` — 支撑匹配候选召回的结构化过滤
- `KEY idx_items_status_created (status, created_at DESC)` — 支撑列表页默认排序
- `KEY idx_items_owner (owner_user_id, created_at DESC)` — 支撑「我的发布」
- `KEY idx_items_event_time (event_time)` — 支撑时间范围过滤
- `KEY idx_items_category (category)` — 支撑类别筛选
- `CONSTRAINT fk_items_owner FOREIGN KEY (owner_user_id) REFERENCES users(id)`

> **`hidden_features` 为什么不单独建表？**
> Baseline 阶段一条记录的隐藏特征是个位数条目，独立成表会显著增加联接与权限校验复杂度；改为**应用层序列化控制 + 数据库账号最小权限 + 接口测试**三重保障。若后续需要「逐条特征的独立授权/审计」，再迁移到 `item_hidden_features` 表（已在「后续演进」记录）。

---

### 3.3 `matches`

| 字段 | 类型 | 约束 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | BIGINT UNSIGNED | PK, AUTO_INCREMENT | — | 匹配 ID |
| `lost_item_id` | BIGINT UNSIGNED | NOT NULL, FK→items(id) | — | 失物 ID |
| `found_item_id` | BIGINT UNSIGNED | NOT NULL, FK→items(id) | — | 拾物 ID |
| `category_score` | DECIMAL(6,5) | NOT NULL | 0 | 类别分 [0,1] |
| `attribute_score` | DECIMAL(6,5) | NOT NULL | 0 | 属性/文本分 [0,1] |
| `image_score` | DECIMAL(6,5) | NOT NULL | 0 | 图像相似度 [0,1] |
| `time_score` | DECIMAL(6,5) | NOT NULL | 0 | 时间合理度 [0,1] |
| `location_score` | DECIMAL(6,5) | NOT NULL | 0 | 地点合理度 [0,1] |
| `brand_model_score` | DECIMAL(6,5) | NOT NULL | 0 | 品牌/型号分 [0,1] |
| `total_score` | DECIMAL(6,5) | NOT NULL | 0 | 总分 [0,1]，= 加权和 |
| `evidence` | JSON | NULL | NULL | 正向匹配依据列表 |
| `uncertainties` | JSON | NULL | NULL | 不确定因素列表 |
| `risk_tips` | JSON | NULL | NULL | 风险提示（**新增字段**） |
| `weights_snapshot` | JSON | NULL | NULL | **新增字段**：计算时的权重快照，便于复现与消融实验 |
| `model_version` | VARCHAR(64) | NOT NULL | `baseline-1.0` | 算法版本 |
| `is_degraded` | TINYINT(1) | NOT NULL | 0 | **新增字段**：是否为降级（规则/Mock）结果 |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 首次计算时间 |
| `updated_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP ON UPDATE | 最近重算时间 |

索引：

- `PRIMARY KEY (id)`
- `UNIQUE KEY uk_matches_pair (lost_item_id, found_item_id)` — 同一对记录只存一行，重算走 UPSERT
- `KEY idx_matches_lost_score (lost_item_id, total_score DESC)` — 支撑 Top-K 查询
- `KEY idx_matches_found_score (found_item_id, total_score DESC)` — 反向查询
- `CONSTRAINT fk_matches_lost FOREIGN KEY (lost_item_id) REFERENCES items(id)`
- `CONSTRAINT fk_matches_found FOREIGN KEY (found_item_id) REFERENCES items(id)`
- `CONSTRAINT chk_matches_total CHECK (total_score >= 0 AND total_score <= 1)`
- `CONSTRAINT chk_matches_self CHECK (lost_item_id <> found_item_id)`

> **为什么冗余总分而不每次现算？** 审核页需要秒级展示；且分数一旦用于审核决策就应**可追溯**——`weights_snapshot` 保证权重调整后历史结果仍可复现，这是答辩「数值由程序计算、可解释、可复现」的关键证据。

---

### 3.4 `claims`

| 字段 | 类型 | 约束 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | BIGINT UNSIGNED | PK, AUTO_INCREMENT | — | 认领 ID |
| `match_id` | BIGINT UNSIGNED | NOT NULL, FK→matches(id) | — | 关联匹配 |
| `applicant_id` | BIGINT UNSIGNED | NOT NULL, FK→users(id) | — | 申请人 |
| `questions` | JSON | NOT NULL | — | 问题列表 `[{question_id, question_text, source, feature_ref}]`，**不含答案** |
| `answers_hash` | JSON | NULL | NULL | **新增字段**：每题答案的加盐哈希 `{q1:"sha256:..."}`，用于比对 |
| `answer_summary` | JSON | NULL | NULL | 答案摘要/评分结果 `[{question_id, matched, score, reason}]`，**不含答案原文** |
| `answers_plain` | JSON | NULL | NULL | **可选的加密存储**（仅管理员+审计需要时启用，默认 NULL，键由 `APP_SECRET_KEY` 派生，AES-GCM） |
| `verification_score` | DECIMAL(6,5) | NULL | NULL | 隐藏特征答案匹配分 [0,1] |
| `credibility_score` | DECIMAL(6,5) | NULL | NULL | 综合可信度分 [0,1]（**新增字段**，与 API 契约 3.9 一致） |
| `risk_level` | VARCHAR(20) | NULL | NULL | `low` / `medium` / `high` |
| `risk_tips` | JSON | NULL | NULL | 风险提示列表 |
| `attempts_used` | INT | NOT NULL | 0 | **新增字段**：已提交验证次数 |
| `max_attempts` | INT | NOT NULL | 3 | 最大允许次数（来自配置） |
| `status` | VARCHAR(32) | NOT NULL | `claiming` | `claiming`/`pending_review`/`request_more_info`/`approved`/`rejected` |
| `admin_comment` | TEXT | NULL | NULL | 管理员备注 |
| `reviewed_by` | BIGINT UNSIGNED | NULL | NULL | **新增字段**：审核人 |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 创建时间 |
| `updated_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP ON UPDATE | **新增字段**：更新时间（支撑重试计数与状态变更的时间追踪） |
| `reviewed_at` | DATETIME | NULL | NULL | 审核时间 |

索引：

- `PRIMARY KEY (id)`
- `KEY idx_claims_match (match_id)`
- `KEY idx_claims_applicant (applicant_id, created_at DESC)` — 支撑「我的认领」
- `KEY idx_claims_status_created (status, created_at)` — 支撑审核台待办列表
- `KEY idx_claims_risk (risk_level, status)` — 支撑高风险优先审核
- `CONSTRAINT fk_claims_match FOREIGN KEY (match_id) REFERENCES matches(id)`
- `CONSTRAINT fk_claims_applicant FOREIGN KEY (applicant_id) REFERENCES users(id)`
- `CONSTRAINT fk_claims_reviewer FOREIGN KEY (reviewed_by) REFERENCES users(id)`
- `CONSTRAINT chk_claims_credibility CHECK (credibility_score IS NULL OR (credibility_score >= 0 AND credibility_score <= 1))`

> **答案存储策略（对齐需求说明书 4.5.4 与安全要求 7）**
> - 默认**不存答案明文**（`answers_plain` 保持 NULL），只存 `answers_hash`（加盐 SHA-256）与 `answer_summary`（是否命中 + 分数 + 理由）；
> - 语义匹配需要原文时，通过 Redis `claim:session:{claim_id}`（TTL 30min）短期持有，过期即天然清理；
> - 应用日志、审计日志、`audit_logs.detail` **一律不得**出现答案原文；
> - 若答辩需要「展示答案对比」，使用演示数据中已脱敏的固定样例，不使用真实用户输入。

---

### 3.5 `audit_logs`

| 字段 | 类型 | 约束 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | BIGINT UNSIGNED | PK, AUTO_INCREMENT | — | 日志 ID |
| `operator_id` | BIGINT UNSIGNED | NULL | NULL | 操作人（系统动作可为 NULL） |
| `operator_role` | VARCHAR(20) | NULL | NULL | **新增字段**：操作时角色快照 |
| `target_type` | VARCHAR(32) | NOT NULL | — | `item` / `claim` / `user` / `config` |
| `target_id` | BIGINT UNSIGNED | NULL | NULL | 对象 ID |
| `action` | VARCHAR(64) | NOT NULL | — | 见 API 契约 2.8 |
| `detail` | JSON | NULL | NULL | **已脱敏**详情（长度、摘要、前后状态） |
| `request_id` | VARCHAR(64) | NULL | NULL | 请求追踪 ID |
| `ip_masked` | VARCHAR(64) | NULL | NULL | **新增字段**：脱敏 IP（如 `10.1.*.*`） |
| `created_at` | DATETIME | NOT NULL | CURRENT_TIMESTAMP | 时间 |

索引：

- `PRIMARY KEY (id)`
- `KEY idx_audit_target (target_type, target_id, created_at DESC)` — 支撑按对象查历史
- `KEY idx_audit_operator (operator_id, created_at DESC)` — 支撑按人查
- `KEY idx_audit_action_created (action, created_at DESC)` — 支撑按动作类型统计
- `KEY idx_audit_request (request_id)` — 支撑按请求追踪（排障关键）
- `CONSTRAINT fk_audit_operator FOREIGN KEY (operator_id) REFERENCES users(id)`

> **日志表只增不改**：应用账号对 `audit_logs` 只授予 `INSERT`/`SELECT`，**不授予 `UPDATE`/`DELETE`**（云上通过 RDS 独立账号实现最小权限）。

---

## 4. 完整 DDL（MySQL 8.0）

```sql
-- ============================================================
-- 「拾光」校园多模态失物检索与可信认领平台
-- Baseline v1.0 · MySQL 8.0 · utf8mb4
-- 执行顺序：users → items → matches → claims → audit_logs
-- ============================================================

CREATE DATABASE IF NOT EXISTS `shiguang`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `shiguang`;

-- ---------- 3.1 users ----------
CREATE TABLE IF NOT EXISTS `users` (
  `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户 ID',
  `username`       VARCHAR(64)     NOT NULL                COMMENT '昵称/账号',
  `role`           VARCHAR(20)     NOT NULL DEFAULT 'student' COMMENT 'student/admin',
  `campus_id`      VARCHAR(64)     NULL                    COMMENT '校区',
  `contact_id`     VARCHAR(128)    NULL                    COMMENT '脱敏联系方式',
  `password_hash`  VARCHAR(255)    NULL                    COMMENT '密码哈希（bcrypt/argon2）',
  `status`         VARCHAR(20)     NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
  `last_login_at`  DATETIME        NULL                    COMMENT '最近登录时间(UTC)',
  `created_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_users_username` (`username`),
  KEY `idx_users_role_status` (`role`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户表';

-- ---------- 3.2 items ----------
CREATE TABLE IF NOT EXISTS `items` (
  `id`                      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '物品记录 ID',
  `owner_user_id`           BIGINT UNSIGNED NOT NULL              COMMENT '发布者',
  `post_type`               VARCHAR(10)     NOT NULL              COMMENT 'lost/found',
  `category`                VARCHAR(64)     NOT NULL              COMMENT '类别',
  `title`                   VARCHAR(80)     NOT NULL              COMMENT '标题',
  `description`             TEXT            NOT NULL              COMMENT '原始描述',
  `color`                   VARCHAR(32)     NULL                  COMMENT '颜色',
  `brand`                   VARCHAR(64)     NULL                  COMMENT '品牌/型号',
  `location`                VARCHAR(128)    NOT NULL              COMMENT '校园地点',
  `location_parsed`         JSON            NULL                  COMMENT '地点解析 {campus,building,floor,spot}',
  `event_time`              DATETIME        NOT NULL              COMMENT '事件时间(UTC)',
  `public_features`         JSON            NULL                  COMMENT '公开特征',
  `hidden_features`         JSON            NOT NULL              COMMENT '隐藏特征（严格权限）',
  `contact_id`              VARCHAR(128)    NULL                  COMMENT '脱敏联系方式',
  `ai_attributes`           JSON            NULL                  COMMENT 'AI 属性结果',
  `ai_status`               VARCHAR(20)     NOT NULL DEFAULT 'pending' COMMENT 'pending/success/failed',
  `ai_model_version`        VARCHAR(64)     NULL                  COMMENT '模型版本',
  `ai_prompt_version`       VARCHAR(32)     NULL                  COMMENT '提示词版本',
  `ai_analyzed_at`          DATETIME        NULL                  COMMENT '分析时间',
  `attributes_edited_by_user` TINYINT(1)    NOT NULL DEFAULT 0    COMMENT '用户是否改过AI属性',
  `status`                  VARCHAR(32)     NOT NULL DEFAULT 'published' COMMENT '业务状态',
  `created_at`              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`              DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_items_match_pool` (`post_type`, `category`, `status`, `event_time`),
  KEY `idx_items_status_created` (`status`, `created_at` DESC),
  KEY `idx_items_owner` (`owner_user_id`, `created_at` DESC),
  KEY `idx_items_event_time` (`event_time`),
  KEY `idx_items_category` (`category`),
  CONSTRAINT `fk_items_owner` FOREIGN KEY (`owner_user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='物品记录表';

-- ---------- 3.3 matches ----------
CREATE TABLE IF NOT EXISTS `matches` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '匹配 ID',
  `lost_item_id`      BIGINT UNSIGNED NOT NULL              COMMENT '失物 ID',
  `found_item_id`     BIGINT UNSIGNED NOT NULL              COMMENT '拾物 ID',
  `category_score`    DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '类别分',
  `attribute_score`   DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '属性/文本分',
  `image_score`       DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '图像相似度',
  `time_score`        DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '时间合理度',
  `location_score`    DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '地点合理度',
  `brand_model_score` DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '品牌/型号分',
  `total_score`       DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '总分',
  `evidence`          JSON            NULL                  COMMENT '匹配依据',
  `uncertainties`     JSON            NULL                  COMMENT '不确定因素',
  `risk_tips`         JSON            NULL                  COMMENT '风险提示',
  `weights_snapshot`  JSON            NULL                  COMMENT '权重快照（可复现）',
  `model_version`     VARCHAR(64)     NOT NULL DEFAULT 'baseline-1.0' COMMENT '算法版本',
  `is_degraded`       TINYINT(1)      NOT NULL DEFAULT 0    COMMENT '是否降级结果',
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_matches_pair` (`lost_item_id`, `found_item_id`),
  KEY `idx_matches_lost_score` (`lost_item_id`, `total_score` DESC),
  KEY `idx_matches_found_score` (`found_item_id`, `total_score` DESC),
  CONSTRAINT `fk_matches_lost`  FOREIGN KEY (`lost_item_id`)  REFERENCES `items` (`id`),
  CONSTRAINT `fk_matches_found` FOREIGN KEY (`found_item_id`) REFERENCES `items` (`id`),
  CONSTRAINT `chk_matches_total` CHECK (`total_score` >= 0 AND `total_score` <= 1),
  CONSTRAINT `chk_matches_self`  CHECK (`lost_item_id` <> `found_item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='匹配结果表';

-- ---------- 3.4 claims ----------
CREATE TABLE IF NOT EXISTS `claims` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '认领 ID',
  `match_id`           BIGINT UNSIGNED NOT NULL              COMMENT '匹配 ID',
  `applicant_id`       BIGINT UNSIGNED NOT NULL              COMMENT '申请人',
  `questions`          JSON            NOT NULL              COMMENT '问题列表（不含答案）',
  `answers_hash`       JSON            NULL                  COMMENT '答案加盐哈希',
  `answer_summary`     JSON            NULL                  COMMENT '答案评分摘要（不含原文）',
  `answers_plain`      JSON            NULL                  COMMENT '加密答案（默认 NULL，不启用）',
  `verification_score` DECIMAL(6,5)    NULL                  COMMENT '隐藏特征答案匹配分',
  `credibility_score`  DECIMAL(6,5)    NULL                  COMMENT '综合可信度分',
  `risk_level`         VARCHAR(20)     NULL                  COMMENT 'low/medium/high',
  `risk_tips`          JSON            NULL                  COMMENT '风险提示',
  `attempts_used`      INT             NOT NULL DEFAULT 0    COMMENT '已提交次数',
  `max_attempts`       INT             NOT NULL DEFAULT 3    COMMENT '最大次数',
  `status`             VARCHAR(32)     NOT NULL DEFAULT 'claiming' COMMENT '认领状态',
  `admin_comment`      TEXT            NULL                  COMMENT '管理员备注',
  `reviewed_by`        BIGINT UNSIGNED NULL                  COMMENT '审核人',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `reviewed_at`        DATETIME        NULL                  COMMENT '审核时间',
  PRIMARY KEY (`id`),
  KEY `idx_claims_match` (`match_id`),
  KEY `idx_claims_applicant` (`applicant_id`, `created_at` DESC),
  KEY `idx_claims_status_created` (`status`, `created_at`),
  KEY `idx_claims_risk` (`risk_level`, `status`),
  CONSTRAINT `fk_claims_match`      FOREIGN KEY (`match_id`)     REFERENCES `matches` (`id`),
  CONSTRAINT `fk_claims_applicant`  FOREIGN KEY (`applicant_id`) REFERENCES `users` (`id`),
  CONSTRAINT `fk_claims_reviewer`   FOREIGN KEY (`reviewed_by`)  REFERENCES `users` (`id`),
  CONSTRAINT `chk_claims_credibility` CHECK (`credibility_score` IS NULL OR (`credibility_score` >= 0 AND `credibility_score` <= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='认领记录表';

-- ---------- 3.5 audit_logs ----------
CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志 ID',
  `operator_id`   BIGINT UNSIGNED NULL                    COMMENT '操作人',
  `operator_role` VARCHAR(20)     NULL                    COMMENT '操作时角色快照',
  `target_type`   VARCHAR(32)     NOT NULL                COMMENT 'item/claim/user/config',
  `target_id`     BIGINT UNSIGNED NULL                    COMMENT '对象 ID',
  `action`        VARCHAR(64)     NOT NULL                COMMENT '动作',
  `detail`        JSON            NULL                    COMMENT '脱敏详情',
  `request_id`    VARCHAR(64)     NULL                    COMMENT '请求追踪 ID',
  `ip_masked`     VARCHAR(64)     NULL                    COMMENT '脱敏 IP',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_audit_target` (`target_type`, `target_id`, `created_at` DESC),
  KEY `idx_audit_operator` (`operator_id`, `created_at` DESC),
  KEY `idx_audit_action_created` (`action`, `created_at` DESC),
  KEY `idx_audit_request` (`request_id`),
  CONSTRAINT `fk_audit_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='审计日志表';

-- ---------- 最小权限账号（云端 RDS 使用） ----------
-- 应用账号：业务表可读写，但审计日志只可插入与查询
-- CREATE USER 'shiguang_app'@'%' IDENTIFIED BY '<由环境变量注入，不得写入本文件>';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`users`   TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`items`   TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`matches` TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`claims`  TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT                ON `shiguang`.`audit_logs` TO 'shiguang_app'@'%';
-- FLUSH PRIVILEGES;
```

---

## 5. 与需求说明书的差异登记

| 表 | 字段 | 类型 | 变更类型 | 原因 |
|---|---|---|---|---|
| `users` | `last_login_at` | DATETIME | 新增 | 审计与活跃度统计 |
| `items` | `location_parsed` | JSON | 新增 | 地点分计算需结构化字段，避免每次解析字符串 |
| `items` | `contact_id` | VARCHAR(128) | 新增 | 审核台需展示脱敏联系方式，避免跨表联接 |
| `items` | `ai_model_version` / `ai_prompt_version` / `ai_analyzed_at` | — | 新增 | 需求 4.3 要求记录模型名、提示词版本、分析时间以便复现 |
| `items` | `attributes_edited_by_user` | TINYINT | 新增 | 需求 4.3 要求「用户修改优先级高于 AI」需可判定 |
| `matches` | `risk_tips` | JSON | 新增 | API 契约 3.6 返回风险提示 |
| `matches` | `weights_snapshot` | JSON | 新增 | 权重可复现，支撑消融实验与答辩 |
| `matches` | `is_degraded` | TINYINT | 新增 | 降级结果需明确标记（需求 13.5） |
| `matches` | `updated_at` | DATETIME | 新增 | 支持 UPSERT 重算的时间追踪 |
| `claims` | `credibility_score` | DECIMAL(6,5) | 新增 | 需求 4.5.3 可信度分需落库供审核与统计 |
| `claims` | `risk_tips` / `attempts_used` / `max_attempts` | — | 新增 | 需求 4.5.4 限次重试与风险标记 |
| `claims` | `answers_hash` | JSON | 新增 | 需求 4.5.4 建议保存答案摘要或加盐哈希，不存明文 |
| `claims` | `answers_plain` | JSON | 新增（默认 NULL） | 预留加密存储能力，Baseline 不启用 |
| `claims` | `reviewed_by` | BIGINT | 新增 | 审核责任可追溯 |
| `claims` | `created_at` / `updated_at` | DATETIME | 新增 `updated_at` | 需求 §5.4 只列了 `created_at`；`updated_at` 用于追踪重试与状态变更时间 |
| `audit_logs` | `operator_role` / `ip_masked` | — | 新增 | 安全审计需要 |

> 所有差异均为**非破坏性扩展**，未删除或改名需求说明书中的任何字段。

---

## 6. 迁移策略（Alembic）

### 6.1 目录与命令

```text
backend/alembic/
├── env.py                  # 读取 app.core.config.settings.DATABASE_URL
├── script.py.mako
└── versions/
    └── 0001_initial_schema.py   # 本设计对应的首个迁移
```

```bash
# 生成迁移（模型改动后）
cd backend && alembic revision --autogenerate -m "add xxx"

# 应用迁移（本地）
alembic upgrade head

# 回滚一步
alembic downgrade -1

# 查看当前版本
alembic current
```

### 6.2 规则

1. **永不修改已合入的迁移文件**，只追加新迁移；
2. 每个迁移必须实现 `upgrade()` **和** `downgrade()`；
3. 生产环境迁移前**必须备份**（RDS 自动备份 + 手动快照）；
4. 大表加索引用 `ALGORITHM=INPLACE, LOCK=NONE`，避免锁表；
5. 首个迁移 `0001_initial_schema.py` 与本文第 4 节 DDL **逐字对齐**，并由 `scripts/init_db` 复用；
6. 演示数据导入使用独立脚本 `scripts/seed_demo_data.py`，**不放进迁移**（保证迁移幂等且可重复执行）。

### 6.3 初始化与数据导入

```bash
# 1) 建库建表（本地或云端）
mysql -h <host> -u <user> -p < scripts/init_db.sql
# 或
cd backend && alembic upgrade head

# 2) 导入脱敏演示数据集（≥60 条物品 + 正负样本标注）
python scripts/seed_demo_data.py --source data/raw/demo_items.json --with-ai-attributes

# 3) 预置演示账号（student / finder / admin）
python scripts/seed_demo_data.py --users-only
```

---

## 7. 性能与容量估算（演示规模）

| 项 | 估算 | 依据 |
|---|---|---|
| `items` 行数 | 60 ~ 500 | 演示数据集 60 条起 |
| `matches` 行数 | ≤ items²/4 ≈ 6.25 万（实际上限） | 一对记录一行，按类别过滤后实际远小于此 |
| `claims` 行数 | ≤ 200 | 演示规模 |
| `audit_logs` 行数 | ≤ 5000 | 每次关键操作一行 |
| 单表数据量 | < 50 MB | 远低于关注阈值 |
| 匹配查询 P95 | < 300 ms | `idx_items_match_pool` 过滤 + Redis 缓存命中 |
| 列表查询 P95 | < 100 ms | `idx_items_status_created` 覆盖排序 |

> 需求 13.4 要求普通列表/详情 P95 < 1 秒、缓存匹配结果 P95 < 1 秒，当前设计有充足余量。

---

## 8. 备份与恢复

| 项 | 方案 |
|---|---|
| 云端自动备份 | RDS 开启自动备份，保留 7 天，备份窗口选凌晨低峰 |
| 手动快照 | 每次结构调整与演示前各一次 |
| 逻辑备份 | `mysqldump --single-transaction --routines --triggers` 后上传 OBS |
| 恢复演练 | 验收前至少执行一次「删除实例数据 → 从备份恢复到新实例 → 校验行数」 |
| 审计日志保护 | 独立账号只增不改；备份保留周期长于业务表 |

---

## 9. 后续演进（Baseline 之后，非本期交付）

1. `item_hidden_features` 独立表 —— 支持逐条特征的授权与访问审计；
2. `match_feedback` 表 —— 记录管理员/用户对匹配结果的反馈，用于调参与评估；
3. `items.status_history` 表 —— 独立记录状态流转轨迹（当前依赖 `audit_logs` 重建）；
4. 向量列（`items.image_embedding` / `text_embedding`）—— 若改用支持向量索引的存储（如 GaussDB(for MySQL) 或独立向量库），可下推召回；
5. 分区表 —— `audit_logs` 按 `created_at` 月度分区，支撑长期增长。

## 附录 · 变更记录

| 版本 | 日期 | 变更人 | 变更内容 | 破坏兼容 |
|---|---|---|---|---|
| 1.0 | 2026-09-30 | M0 需求冻结会议 | 首次冻结：5 张表、完整 DDL、索引、迁移与备份策略 | — |
