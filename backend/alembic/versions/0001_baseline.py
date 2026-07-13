"""baseline: 19 tables (Wave 1 consolidation)

MIGRATION.md 阶段 1 基线修订。

策略：本修订以统一后的 `app.models.Base.metadata` 为准，
- 全新空库：`alembic upgrade head` 调 `create_all` 建出全部 19 张表；
- 现有库（已由旧 `create_all` 建表）：改用 `alembic stamp head` 标记到本修订，不重复建表。

Wave 2 的双 Project 合并 / tickets->tasks 等结构变更，以显式 op.* 修订从本基线派生。

Revision ID: 0001_baseline
Revises:
Create Date: 2026-07-10
"""
from typing import Sequence, Union

from alembic import op

from app.models import Base


# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 以统一 metadata 建出全部表（仅对全新空库执行；现有库应走 stamp）。
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
