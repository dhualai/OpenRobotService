"""create_user_info

新增用户信息表 `user_info`：id 主键 / user_info JSON / created_time 仅年月日（DATE）。
同时合并双 head（c4e7b2a91d38 + f6a1b2c3d4e5），收敛为单 head。

列说明见 backend/app/models/user_info.py。

Revision ID: a9b8c7d6e5f4
Revises: c4e7b2a91d38, f6a1b2c3d4e5
Create Date: 2026-08-31 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, Sequence[str], None] = ('c4e7b2a91d38', 'f6a1b2c3d4e5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
        ),
        {"name": table_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    if _table_exists("user_info"):
        return  # 已存在（可能由 create_all 自动建），幂等跳过

    op.create_table(
        "user_info",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="主键"),
        sa.Column("user_info", sa.JSON(), nullable=False, comment="用户信息（JSON 格式）"),
        sa.Column("created_time", sa.Date(), server_default=sa.text("(CURRENT_DATE)"), nullable=False, comment="创建日期（仅年月日）"),
    )


def downgrade() -> None:
    if _table_exists("user_info"):
        op.drop_table("user_info")
