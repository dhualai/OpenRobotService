"""departments 表新增部门职责画像字段（AI 派单部门分类用）

Revision ID: a7d1e2f3g4h5
Revises: c4e7b2a91d38, f6a1b2c3d4e5
Create Date: 2026-08-27 00:00:00.000000

- profile_text : 部门职责描述（供 AI 派单 R2 LLM 部门分类）
- examples     : 典型工单示例 JSON（[{title, dept}]）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7d1e2f3g4h5'
down_revision: Union[str, Sequence[str], None] = ('c4e7b2a91d38', 'f6a1b2c3d4e5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('departments', sa.Column('profile_text', sa.Text(), nullable=True, comment='部门职责描述（AI 派单部门分类用）'))
    op.add_column('departments', sa.Column('examples', sa.JSON(), nullable=True, comment='典型工单示例（[{title, dept}]）'))


def downgrade() -> None:
    op.drop_column('departments', 'examples')
    op.drop_column('departments', 'profile_text')
