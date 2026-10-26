"""${message}

迁移 ID：${up_revision}
父迁移：${down_revision | comma,n}
创建时间：${create_date}

规则（docs/database-design.md §6.2）：
1. **永不修改已合入的迁移文件**，只追加新迁移；
2. 必须同时实现 `upgrade()` 与 `downgrade()`；
3. `downgrade()` 中按「先删子表后删父表」的顺序回滚，保证外键不报错；
4. DDL 变更必须同步更新 `docs/database-design.md` 与 `scripts/init_db.sql`。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """升级：在此处编写前向 DDL。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚：在此处编写逆序 DDL（先删子表、后删父表）。"""
    ${downgrades if downgrades else "pass"}
