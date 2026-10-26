-- =============================================================================
-- 拾光 · 数据库初始化脚本（MySQL 8.0）
-- -----------------------------------------------------------------------------
-- 用途：
--   1) docker-compose 启动时自动执行（挂载到 /docker-entrypoint-initdb.d/）
--   2) 本地/云端手动初始化：mysql -h <host> -u <user> -p < scripts/init_db.sql
--
-- 权威来源：docs/database-design.md §4（本文件与其逐字对齐）
-- 表结构变更必须同时：① 改 docs/database-design.md ② 新增 Alembic 迁移 ③ 同步本文件
--
-- ⚠ 本文件不得包含任何真实密码。最小权限账号见文件末尾注释。
-- =============================================================================

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE DATABASE IF NOT EXISTS `shiguang`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE `shiguang`;

-- =============================================================================
-- 3.1 users
-- =============================================================================
CREATE TABLE IF NOT EXISTS `users` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户 ID',
  `username`      VARCHAR(64)     NOT NULL                COMMENT '昵称/账号',
  `role`          VARCHAR(20)     NOT NULL DEFAULT 'student' COMMENT 'student/admin',
  `campus_id`     VARCHAR(64)     NULL                    COMMENT '所属校区',
  `contact_id`    VARCHAR(128)    NULL                    COMMENT '**已脱敏**联系方式',
  `password_hash` VARCHAR(255)    NULL                    COMMENT '密码哈希（bcrypt/argon2）',
  `status`        VARCHAR(20)     NOT NULL DEFAULT 'active' COMMENT 'active/disabled',
  `last_login_at` DATETIME        NULL                    COMMENT '最近登录时间(UTC)',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间(UTC)',
  `updated_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间(UTC)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_users_username` (`username`),
  KEY `idx_users_role_status` (`role`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户表';

-- =============================================================================
-- 3.2 items
-- =============================================================================
CREATE TABLE IF NOT EXISTS `items` (
  `id`                        BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '物品记录 ID',
  `owner_user_id`             BIGINT UNSIGNED NOT NULL              COMMENT '发布者',
  `post_type`                 VARCHAR(10)     NOT NULL              COMMENT 'lost/found',
  `category`                  VARCHAR(64)     NOT NULL              COMMENT '类别',
  `title`                     VARCHAR(80)     NOT NULL              COMMENT '标题 5~80 字',
  `description`               TEXT            NOT NULL              COMMENT '原始描述 10~2000 字',
  `color`                     VARCHAR(32)     NULL                  COMMENT '颜色',
  `brand`                     VARCHAR(64)     NULL                  COMMENT '品牌/型号',
  `location`                  VARCHAR(128)    NOT NULL              COMMENT '校园地点（原始字符串）',
  `location_parsed`           JSON            NULL                  COMMENT '地点解析 {campus,building,floor,spot}',
  `event_time`                DATETIME        NOT NULL              COMMENT '事件时间(UTC)',
  `public_features`           JSON            NULL                  COMMENT '公开特征',
  `hidden_features`           JSON            NOT NULL              COMMENT '🔒 隐藏特征（仅发布者与管理员可见）',
  `contact_id`                VARCHAR(128)    NULL                  COMMENT '脱敏联系方式',
  `ai_attributes`             JSON            NULL                  COMMENT 'AI 属性提取结果',
  `ai_status`                 VARCHAR(20)     NOT NULL DEFAULT 'pending' COMMENT 'pending/success/failed',
  `ai_model_version`          VARCHAR(64)     NULL                  COMMENT '模型名称+版本',
  `ai_prompt_version`         VARCHAR(32)     NULL                  COMMENT '提示词版本',
  `ai_analyzed_at`            DATETIME        NULL                  COMMENT '分析时间(UTC)',
  `attributes_edited_by_user` TINYINT(1)      NOT NULL DEFAULT 0    COMMENT '用户是否改过 AI 属性（用户修改优先）',
  `status`                    VARCHAR(32)     NOT NULL DEFAULT 'published' COMMENT '业务状态',
  `created_at`                DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间(UTC)',
  `updated_at`                DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间(UTC)',
  PRIMARY KEY (`id`),
  KEY `idx_items_match_pool` (`post_type`, `category`, `status`, `event_time`),
  KEY `idx_items_status_created` (`status`, `created_at` DESC),
  KEY `idx_items_owner` (`owner_user_id`, `created_at` DESC),
  KEY `idx_items_event_time` (`event_time`),
  KEY `idx_items_category` (`category`),
  CONSTRAINT `fk_items_owner` FOREIGN KEY (`owner_user_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='物品记录表';

-- =============================================================================
-- 3.2.1 item_images（每条记录最多 3 张；需求 §4.2.4）
-- =============================================================================
CREATE TABLE IF NOT EXISTS `item_images` (
  `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `item_id`     BIGINT UNSIGNED NOT NULL              COMMENT '所属物品',
  `object_key`  VARCHAR(512)    NOT NULL              COMMENT '对象存储 Key：items/{yyyy}/{mm}/{uuid}.{ext}',
  `mime_type`   VARCHAR(64)     NULL                  COMMENT 'image/jpeg | image/png | image/webp',
  `size_bytes`  BIGINT          NULL                  COMMENT '字节数，≤ 10MB',
  `width`       BIGINT          NULL                  COMMENT '像素宽',
  `height`      BIGINT          NULL                  COMMENT '像素高',
  `sort_order`  BIGINT          NOT NULL DEFAULT 0    COMMENT '展示顺序',
  `is_primary`  TINYINT(1)      NOT NULL DEFAULT 0    COMMENT '是否主图',
  `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_item_images_item` (`item_id`, `sort_order`),
  CONSTRAINT `fk_item_images_item` FOREIGN KEY (`item_id`) REFERENCES `items` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='物品图片表';

-- =============================================================================
-- 3.3 matches
-- =============================================================================
CREATE TABLE IF NOT EXISTS `matches` (
  `id`                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '匹配 ID',
  `lost_item_id`      BIGINT UNSIGNED NOT NULL              COMMENT '失物 ID',
  `found_item_id`     BIGINT UNSIGNED NOT NULL              COMMENT '拾物 ID',
  `category_score`    DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '类别分 [0,1]',
  `attribute_score`   DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '属性/文本分 [0,1]',
  `image_score`       DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '图像相似度 [0,1]',
  `time_score`        DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '时间合理度 [0,1]',
  `location_score`    DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '地点合理度 [0,1]',
  `brand_model_score` DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '品牌/型号分 [0,1]',
  `total_score`       DECIMAL(6,5)    NOT NULL DEFAULT 0    COMMENT '总分 [0,1] = 六维加权和',
  `evidence`          JSON            NULL                  COMMENT '正向匹配依据',
  `uncertainties`     JSON            NULL                  COMMENT '不确定因素',
  `risk_tips`         JSON            NULL                  COMMENT '风险提示',
  `weights_snapshot`  JSON            NULL                  COMMENT '权重快照（可复现，支撑消融实验）',
  `model_version`     VARCHAR(64)     NOT NULL DEFAULT 'baseline-1.0' COMMENT '算法版本',
  `is_degraded`       TINYINT(1)      NOT NULL DEFAULT 0    COMMENT '是否为降级（规则/Mock）结果',
  `created_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '首次计算时间(UTC)',
  `updated_at`        DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最近重算时间(UTC)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_matches_pair` (`lost_item_id`, `found_item_id`),
  KEY `idx_matches_lost_score` (`lost_item_id`, `total_score` DESC),
  KEY `idx_matches_found_score` (`found_item_id`, `total_score` DESC),
  CONSTRAINT `fk_matches_lost`  FOREIGN KEY (`lost_item_id`)  REFERENCES `items` (`id`),
  CONSTRAINT `fk_matches_found` FOREIGN KEY (`found_item_id`) REFERENCES `items` (`id`),
  CONSTRAINT `chk_matches_total` CHECK (`total_score` >= 0 AND `total_score` <= 1),
  CONSTRAINT `chk_matches_self`  CHECK (`lost_item_id` <> `found_item_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='匹配结果表';

-- =============================================================================
-- 3.4 claims
-- =============================================================================
CREATE TABLE IF NOT EXISTS `claims` (
  `id`                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '认领 ID',
  `match_id`           BIGINT UNSIGNED NOT NULL              COMMENT '匹配 ID',
  `applicant_id`       BIGINT UNSIGNED NOT NULL              COMMENT '申请人',
  `questions`          JSON            NOT NULL              COMMENT '问题列表（不含答案）',
  `answers_hash`       JSON            NULL                  COMMENT '🔒 答案加盐哈希',
  `answer_summary`     JSON            NULL                  COMMENT '答案评分摘要（不含原文）',
  `answers_plain`      JSON            NULL                  COMMENT '🔒 加密答案（默认 NULL，Baseline 不启用）',
  `verification_score` DECIMAL(6,5)    NULL                  COMMENT '隐藏特征答案匹配分 [0,1]',
  `credibility_score`  DECIMAL(6,5)    NULL                  COMMENT '综合可信度分 [0,1]',
  `risk_level`         VARCHAR(20)     NULL                  COMMENT 'low/medium/high',
  `risk_tips`          JSON            NULL                  COMMENT '风险提示',
  `attempts_used`      INT             NOT NULL DEFAULT 0    COMMENT '已提交次数',
  `max_attempts`       INT             NOT NULL DEFAULT 3    COMMENT '最大允许次数',
  `status`             VARCHAR(32)     NOT NULL DEFAULT 'claiming' COMMENT '认领状态',
  `admin_comment`      TEXT            NULL                  COMMENT '管理员备注',
  `reviewed_by`        BIGINT UNSIGNED NULL                  COMMENT '审核人',
  `created_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间(UTC)',
  `updated_at`         DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间(UTC)',
  `reviewed_at`        DATETIME        NULL                  COMMENT '审核时间(UTC)',
  PRIMARY KEY (`id`),
  KEY `idx_claims_match` (`match_id`),
  KEY `idx_claims_applicant` (`applicant_id`, `created_at` DESC),
  KEY `idx_claims_status_created` (`status`, `created_at`),
  KEY `idx_claims_risk` (`risk_level`, `status`),
  CONSTRAINT `fk_claims_match`     FOREIGN KEY (`match_id`)     REFERENCES `matches` (`id`),
  CONSTRAINT `fk_claims_applicant` FOREIGN KEY (`applicant_id`) REFERENCES `users` (`id`),
  CONSTRAINT `fk_claims_reviewer`  FOREIGN KEY (`reviewed_by`)  REFERENCES `users` (`id`),
  CONSTRAINT `chk_claims_credibility` CHECK (`credibility_score` IS NULL OR (`credibility_score` >= 0 AND `credibility_score` <= 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='认领记录表';

-- =============================================================================
-- 3.5 audit_logs（只增不改）
-- =============================================================================
CREATE TABLE IF NOT EXISTS `audit_logs` (
  `id`            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '日志 ID',
  `operator_id`   BIGINT UNSIGNED NULL                    COMMENT '操作人（系统动作为 NULL）',
  `operator_role` VARCHAR(20)     NULL                    COMMENT '操作时角色快照',
  `target_type`   VARCHAR(32)     NOT NULL                COMMENT 'item/claim/user/config',
  `target_id`     BIGINT UNSIGNED NULL                    COMMENT '对象 ID',
  `action`        VARCHAR(64)     NOT NULL                COMMENT '动作',
  `detail`        JSON            NULL                    COMMENT '**已脱敏**详情',
  `request_id`    VARCHAR(64)     NULL                    COMMENT '请求追踪 ID',
  `ip_masked`     VARCHAR(64)     NULL                    COMMENT '脱敏 IP，如 10.1.*.*',
  `created_at`    DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '时间(UTC)',
  PRIMARY KEY (`id`),
  KEY `idx_audit_target` (`target_type`, `target_id`, `created_at` DESC),
  KEY `idx_audit_operator` (`operator_id`, `created_at` DESC),
  KEY `idx_audit_action_created` (`action`, `created_at` DESC),
  KEY `idx_audit_request` (`request_id`),
  CONSTRAINT `fk_audit_operator` FOREIGN KEY (`operator_id`) REFERENCES `users` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='审计日志表（只增不改）';

-- =============================================================================
-- Alembic 版本表（若使用 alembic upgrade head 建表则由 Alembic 自行创建；
-- 本脚本手工建表时也保留此表以免 Alembic 误判为「已是最新」）
-- =============================================================================
CREATE TABLE IF NOT EXISTS `alembic_version` (
  `version_num` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`version_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- =============================================================================
-- 最小权限账号（云端 RDS 使用）
-- ⚠ 密码必须由部署平台通过环境变量注入，**不得**写在本文件中。
-- 应用账号对 audit_logs 只授予 SELECT / INSERT，从数据库层保证审计日志不可篡改。
-- =============================================================================
-- CREATE USER IF NOT EXISTS 'shiguang_app'@'%' IDENTIFIED BY '<从密钥管理注入>';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`users`       TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`items`       TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`item_images` TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`matches`     TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT, UPDATE, DELETE ON `shiguang`.`claims`      TO 'shiguang_app'@'%';
-- GRANT SELECT, INSERT                 ON `shiguang`.`audit_logs`  TO 'shiguang_app'@'%';
-- FLUSH PRIVILEGES;

-- =============================================================================
-- 初始化完成标记
-- =============================================================================
SELECT
  '拾光数据库初始化完成' AS message,
  (SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'shiguang') AS table_count;
