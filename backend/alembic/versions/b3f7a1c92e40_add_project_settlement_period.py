"""add_project_settlement_period

项目业绩核算期（来自企业微信「业绩核算期」列，格式 YYYY-MM），
用于按月统计"本月新增项目数"等看板指标。

Revision ID: b3f7a1c92e40
Revises: 85291bac5d4c
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3f7a1c92e40'
down_revision: Union[str, None] = '85291bac5d4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('settlement_period', sa.String(length=20), nullable=True, comment='业绩核算期，格式YYYY-MM'))
    op.create_index('idx_project_settlement_period', 'project', ['settlement_period'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_project_settlement_period', table_name='project')
    op.drop_column('project', 'settlement_period')
