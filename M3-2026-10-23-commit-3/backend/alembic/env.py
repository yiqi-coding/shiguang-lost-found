"""Alembic 环境配置。

要点：

1. 连接串来自 `app.core.config.settings.DATABASE_URL`（环境变量 / `.env`），
   **绝不写死在 `alembic.ini`**；
2. `target_metadata = Base.metadata`，`--autogenerate` 才能感知模型变化；
3. 同时支持 offline（只生成 SQL）与 online（直连数据库）两种模式。

安全：本文件不打印连接串；`%` 需要转义为 `%%`，否则 ConfigParser 会做插值解析而报错。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证 `import app.*` 可用（alembic 通常在 backend/ 目录下执行）
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402 - 必须在 sys.path 调整之后导入
from app.models import Base  # noqa: E402 - 导入全部模型以填充 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 把真实连接串注入 Alembic 配置（转义 % 以避免 ConfigParser 插值）
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))

# autogenerate 的比对目标
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：不连数据库，仅按 URL 生成 SQL（`alembic upgrade head --sql`）。"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：建立连接并执行迁移。

    使用 `NullPool`：迁移是一次性任务，不需要维护连接池。
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
