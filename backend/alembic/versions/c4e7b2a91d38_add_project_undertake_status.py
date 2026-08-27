"""add_project_undertake_status

项目承接状态（来自企业微信项目表「是否承接」列原值：是 / 待定；「否」不入库）。
此前同步只放行「是」，待定项目根本不进库；仪表盘「调度项目看板」月柱图需要同时
展示待定项目数量（浅色段），故新增该列并放行待定记录。

存量项目全部按已承接处理（server_default='是'）。

Revision ID: c4e7b2a91d38
Revises: 1a2b3c4d5e6f
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4e7b2a91d38'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'project',
        sa.Column(
            'undertake_status', sa.String(length=10),
            nullable=False, server_default='是',
            comment='是否承接（是/待定；「否」不入库）',
        ),
    )
    op.create_index('idx_project_undertake_status', 'project', ['undertake_status'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_project_undertake_status', table_name='project')
    op.drop_column('project', 'undertake_status')
