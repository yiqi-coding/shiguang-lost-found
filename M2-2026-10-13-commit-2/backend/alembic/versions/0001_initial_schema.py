"""初始库表结构（Baseline v1.0）。

本迁移与 `docs/database-design.md` §4 及 `scripts/init_db.sql` **逐字对齐**：

- 表顺序：`users` → `items` → `item_images` → `matches` → `claims` → `audit_logs`；
- 列名 / 类型 / NULL 性 / 默认值 / 注释 / 索引名 / 唯一键名 / 外键名 / CHECK 名全部一致；
- 存储引擎 `InnoDB`、字符集 `utf8mb4`、排序规则 `utf8mb4_0900_ai_ci`；
- 时间字段统一 UTC（`DATETIME`），数据库侧 `CURRENT_TIMESTAMP` 仅作兜底。

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-09-30

⚠ 已合入的迁移**永不修改**，后续变更请追加新迁移文件。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 统一的表参数：InnoDB + utf8mb4 + utf8mb4_0900_ai_ci（支持 emoji 与中文）
TABLE_KWARGS: dict[str, str] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

# 创建时间 / 更新时间列定义（MySQL 8.0 写法）。
# 每个表都需要独立的 Column 实例（Column 不能同时属于两张表），因此用工厂函数。
def _created_at() -> sa.Column:
    """`created_at`：默认 CURRENT_TIMESTAMP。"""
    return sa.Column(
        "created_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
        comment="创建时间(UTC)",
    )


def _updated_at() -> sa.Column:
    """`updated_at`：默认 CURRENT_TIMESTAMP，并在更新时自动刷新。

    注意：MySQL 的 `ON UPDATE CURRENT_TIMESTAMP` 无法用 SQLAlchemy 的
    `server_onupdate` 表达（不会生成该子句），故此处使用原生文本，与 DDL 逐字对齐。
    """
    return sa.Column(
        "updated_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        comment="更新时间(UTC)",
    )


def _bigint_unsigned() -> mysql.BIGINT:
    """BIGINT UNSIGNED（与 DDL 保持一致；主键与所有外键列统一使用）。"""
    return mysql.BIGINT(unsigned=True)


def upgrade() -> None:
    """建表（顺序即外键依赖顺序）。"""

    # ------------------------------------------------------------------
    # 3.1 users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False, comment="用户 ID"),
        sa.Column("username", sa.String(64), nullable=False, comment="昵称/账号"),
        sa.Column(
            "role", sa.String(20), nullable=False, server_default=sa.text("'student'"), comment="student/admin"
        ),
        sa.Column("campus_id", sa.String(64), nullable=True, comment="所属校区"),
        sa.Column("contact_id", sa.String(128), nullable=True, comment="**已脱敏**联系方式"),
        sa.Column("password_hash", sa.String(255), nullable=True, comment="密码哈希（bcrypt/argon2）"),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default=sa.text("'active'"), comment="active/disabled"
        ),
        sa.Column("last_login_at", sa.DateTime(), nullable=True, comment="最近登录时间(UTC)"),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.UniqueConstraint("username", name="uk_users_username"),
        sa.Index("idx_users_role_status", "role", "status"),
        comment="用户表",
        **TABLE_KWARGS,
    )

    # ------------------------------------------------------------------
    # 3.2 items
    # ------------------------------------------------------------------
    op.create_table(
        "items",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False, comment="物品记录 ID"),
        sa.Column("owner_user_id", _bigint_unsigned(), nullable=False, comment="发布者"),
        sa.Column("post_type", sa.String(10), nullable=False, comment="lost/found"),
        sa.Column("category", sa.String(64), nullable=False, comment="类别"),
        sa.Column("title", sa.String(80), nullable=False, comment="标题 5~80 字"),
        sa.Column("description", sa.Text(), nullable=False, comment="原始描述 10~2000 字"),
        sa.Column("color", sa.String(32), nullable=True, comment="颜色"),
        sa.Column("brand", sa.String(64), nullable=True, comment="品牌/型号"),
        sa.Column("location", sa.String(128), nullable=False, comment="校园地点（原始字符串）"),
        sa.Column("location_parsed", sa.JSON(), nullable=True, comment="地点解析 {campus,building,floor,spot}"),
        sa.Column("event_time", sa.DateTime(), nullable=False, comment="事件时间(UTC)"),
        sa.Column("public_features", sa.JSON(), nullable=True, comment="公开特征"),
        sa.Column("hidden_features", sa.JSON(), nullable=False, comment="🔒 隐藏特征（仅发布者与管理员可见）"),
        sa.Column("contact_id", sa.String(128), nullable=True, comment="脱敏联系方式"),
        sa.Column("ai_attributes", sa.JSON(), nullable=True, comment="AI 属性提取结果"),
        sa.Column(
            "ai_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending/success/failed",
        ),
        sa.Column("ai_model_version", sa.String(64), nullable=True, comment="模型名称+版本"),
        sa.Column("ai_prompt_version", sa.String(32), nullable=True, comment="提示词版本"),
        sa.Column("ai_analyzed_at", sa.DateTime(), nullable=True, comment="分析时间(UTC)"),
        sa.Column(
            "attributes_edited_by_user",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
            comment="用户是否改过 AI 属性（用户修改优先）",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'published'"),
            comment="业务状态（见 API 契约 2.3）",
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_items_owner"
        ),
        # 复合索引支撑匹配候选召回；created_at DESC 为 MySQL 8.0 降序索引
        sa.Index("idx_items_match_pool", "post_type", "category", "status", "event_time"),
        sa.Index("idx_items_status_created", "status", sa.text("created_at DESC")),
        sa.Index("idx_items_owner", "owner_user_id", sa.text("created_at DESC")),
        sa.Index("idx_items_event_time", "event_time"),
        sa.Index("idx_items_category", "category"),
        comment="物品记录表",
        **TABLE_KWARGS,
    )

    # ------------------------------------------------------------------
    # 3.2.1 item_images（每条记录最多 3 张；需求 §4.2.4）
    # ------------------------------------------------------------------
    op.create_table(
        "item_images",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False),
        sa.Column("item_id", _bigint_unsigned(), nullable=False, comment="所属物品"),
        sa.Column(
            "object_key",
            sa.String(512),
            nullable=False,
            comment="对象存储 Key：items/{yyyy}/{mm}/{uuid}.{ext}",
        ),
        sa.Column("mime_type", sa.String(64), nullable=True, comment="image/jpeg | image/png | image/webp"),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True, comment="字节数，≤ 10MB"),
        sa.Column("width", sa.BigInteger(), nullable=True, comment="像素宽"),
        sa.Column("height", sa.BigInteger(), nullable=True, comment="像素高"),
        sa.Column("sort_order", sa.BigInteger(), nullable=False, server_default=sa.text("0"), comment="展示顺序"),
        sa.Column(
            "is_primary",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否主图",
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name="fk_item_images_item", ondelete="CASCADE"
        ),
        sa.Index("idx_item_images_item", "item_id", "sort_order"),
        comment="物品图片表（每条记录最多 3 张）",
        **TABLE_KWARGS,
    )

    # ------------------------------------------------------------------
    # 3.3 matches
    # ------------------------------------------------------------------
    op.create_table(
        "matches",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False, comment="匹配 ID"),
        sa.Column("lost_item_id", _bigint_unsigned(), nullable=False, comment="失物 ID"),
        sa.Column("found_item_id", _bigint_unsigned(), nullable=False, comment="拾物 ID"),
        sa.Column(
            "category_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="类别分 [0,1]",
        ),
        sa.Column(
            "attribute_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="属性/文本分 [0,1]",
        ),
        sa.Column(
            "image_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="图像相似度 [0,1]",
        ),
        sa.Column(
            "time_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="时间合理度 [0,1]",
        ),
        sa.Column(
            "location_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="地点合理度 [0,1]",
        ),
        sa.Column(
            "brand_model_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="品牌/型号分 [0,1]",
        ),
        sa.Column(
            "total_score",
            sa.Numeric(6, 5),
            nullable=False,
            server_default=sa.text("0"),
            comment="总分 [0,1] = 六维加权和",
        ),
        sa.Column("evidence", sa.JSON(), nullable=True, comment="正向匹配依据"),
        sa.Column("uncertainties", sa.JSON(), nullable=True, comment="不确定因素"),
        sa.Column("risk_tips", sa.JSON(), nullable=True, comment="风险提示"),
        sa.Column("weights_snapshot", sa.JSON(), nullable=True, comment="权重快照（可复现，支撑消融实验）"),
        sa.Column(
            "model_version",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'baseline-1.0'"),
            comment="算法版本",
        ),
        sa.Column(
            "is_degraded",
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否为降级（规则/Mock）结果",
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.UniqueConstraint("lost_item_id", "found_item_id", name="uk_matches_pair"),
        sa.ForeignKeyConstraint(["lost_item_id"], ["items.id"], name="fk_matches_lost"),
        sa.ForeignKeyConstraint(["found_item_id"], ["items.id"], name="fk_matches_found"),
        sa.CheckConstraint("total_score >= 0 AND total_score <= 1", name="chk_matches_total"),
        sa.CheckConstraint("lost_item_id <> found_item_id", name="chk_matches_self"),
        sa.Index("idx_matches_lost_score", "lost_item_id", sa.text("total_score DESC")),
        sa.Index("idx_matches_found_score", "found_item_id", sa.text("total_score DESC")),
        comment="匹配结果表",
        **TABLE_KWARGS,
    )

    # ------------------------------------------------------------------
    # 3.4 claims
    # ------------------------------------------------------------------
    op.create_table(
        "claims",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False, comment="认领 ID"),
        sa.Column("match_id", _bigint_unsigned(), nullable=False, comment="匹配 ID"),
        sa.Column("applicant_id", _bigint_unsigned(), nullable=False, comment="申请人"),
        sa.Column("questions", sa.JSON(), nullable=False, comment="问题列表（不含答案）"),
        sa.Column("answers_hash", sa.JSON(), nullable=True, comment="🔒 答案加盐哈希"),
        sa.Column("answer_summary", sa.JSON(), nullable=True, comment="答案评分摘要（不含原文）"),
        sa.Column("answers_plain", sa.JSON(), nullable=True, comment="🔒 加密答案（默认 NULL，Baseline 不启用）"),
        sa.Column("verification_score", sa.Numeric(6, 5), nullable=True, comment="隐藏特征答案匹配分 [0,1]"),
        sa.Column("credibility_score", sa.Numeric(6, 5), nullable=True, comment="综合可信度分 [0,1]"),
        sa.Column("risk_level", sa.String(20), nullable=True, comment="low/medium/high"),
        sa.Column("risk_tips", sa.JSON(), nullable=True, comment="风险提示"),
        sa.Column(
            "attempts_used", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="已提交次数"
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3"), comment="最大允许次数"
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'claiming'"),
            comment="认领状态",
        ),
        sa.Column("admin_comment", sa.Text(), nullable=True, comment="管理员备注"),
        sa.Column("reviewed_by", _bigint_unsigned(), nullable=True, comment="审核人"),
        _created_at(),
        # 注意：`claims.updated_at` 见 scripts/init_db.sql（模型 TimestampMixin 同样需要），
        # docs/database-design.md §3.4 的字段表未列出，属文档遗漏而非设计差异。
        _updated_at(),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True, comment="审核时间(UTC)"),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], name="fk_claims_match"),
        sa.ForeignKeyConstraint(["applicant_id"], ["users.id"], name="fk_claims_applicant"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], name="fk_claims_reviewer"),
        sa.CheckConstraint(
            "credibility_score IS NULL OR (credibility_score >= 0 AND credibility_score <= 1)",
            name="chk_claims_credibility",
        ),
        sa.Index("idx_claims_match", "match_id"),
        sa.Index("idx_claims_applicant", "applicant_id", sa.text("created_at DESC")),
        sa.Index("idx_claims_status_created", "status", "created_at"),
        sa.Index("idx_claims_risk", "risk_level", "status"),
        comment="认领记录表",
        **TABLE_KWARGS,
    )

    # ------------------------------------------------------------------
    # 3.5 audit_logs（只增不改）
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", _bigint_unsigned(), autoincrement=True, nullable=False, comment="日志 ID"),
        sa.Column("operator_id", _bigint_unsigned(), nullable=True, comment="操作人（系统动作为 NULL）"),
        sa.Column("operator_role", sa.String(20), nullable=True, comment="操作时角色快照"),
        sa.Column("target_type", sa.String(32), nullable=False, comment="item/claim/user/config"),
        sa.Column("target_id", _bigint_unsigned(), nullable=True, comment="对象 ID"),
        sa.Column("action", sa.String(64), nullable=False, comment="动作（见 API 契约 2.8）"),
        sa.Column("detail", sa.JSON(), nullable=True, comment="**已脱敏**详情"),
        sa.Column("request_id", sa.String(64), nullable=True, comment="请求追踪 ID"),
        sa.Column("ip_masked", sa.String(64), nullable=True, comment="脱敏 IP，如 10.1.*.*"),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="PRIMARY"),
        sa.ForeignKeyConstraint(["operator_id"], ["users.id"], name="fk_audit_operator"),
        sa.Index("idx_audit_target", "target_type", "target_id", sa.text("created_at DESC")),
        sa.Index("idx_audit_operator", "operator_id", sa.text("created_at DESC")),
        sa.Index("idx_audit_action_created", "action", sa.text("created_at DESC")),
        sa.Index("idx_audit_request", "request_id"),
        comment="审计日志表（只增不改）",
        **TABLE_KWARGS,
    )


def downgrade() -> None:
    """回滚：按「先删子表、后删父表」的逆序删除，避免外键约束报错。"""
    op.drop_table("audit_logs")
    op.drop_table("claims")
    op.drop_table("matches")
    op.drop_table("item_images")
    op.drop_table("items")
    op.drop_table("users")
