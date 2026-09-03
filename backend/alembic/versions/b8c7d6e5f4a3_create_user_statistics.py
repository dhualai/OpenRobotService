"""create_user_statistics

新增用户统计表 `user_statistics`：按统计日期（ref_date）+ 用户来源（user_source）
记录每日新增用户（new_user）与取消关注用户（cancel_user）数量。

列说明见 backend/app/models/user_statistics.py。

Revision ID: b8c7d6e5f4a3
Revises: a9b8c7d6e5f4
Create Date: 2026-08-31 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'b8c7d6e5f4a3'
down_revision: Union[str, Sequence[str], None] = 'a9b8c7d6e5f4'
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
    if _table_exists("user_statistics"):
        return  # 已存在（可能由 create_all 自动建），幂等跳过

    op.create_table(
        "user_statistics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="主键"),
        sa.Column("ref_date", sa.Date(), nullable=False, comment="统计日期（仅年月日）"),
        sa.Column("user_source", sa.Integer(), nullable=False, comment="用户来源"),
        sa.Column("new_user", sa.Integer(), nullable=False, comment="新增用户数"),
        sa.Column("cancel_user", sa.Integer(), nullable=False, comment="取消关注用户数"),
    )
    # 统计日期索引（按日期区间拉取统计的常见查询模式）
    op.create_index("ix_user_statistics_ref_date", "user_statistics", ["ref_date"])


def downgrade() -> None:
    if _table_exists("user_statistics"):
        op.drop_index("ix_user_statistics_ref_date", table_name="user_statistics")
        op.drop_table("user_statistics")
