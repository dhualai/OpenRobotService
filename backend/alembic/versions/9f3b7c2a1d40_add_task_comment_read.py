"""add_task_comment_read

轻量 IM 已读回执：新增 task_comment_read 表。

Revision ID: 9f3b7c2a1d40
Revises: 1120f2c12ed6
Create Date: 2026-08-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9f3b7c2a1d40'
down_revision: Union[str, None] = '1120f2c12ed6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'task_comment_read',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('task_id', sa.BigInteger(), nullable=False, comment='任务ID'),
        sa.Column('username', sa.String(50), nullable=False, comment='用户username'),
        sa.Column('last_read_comment_id', sa.BigInteger(), nullable=False, comment='已读到的最后一条评论ID'),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False, comment='更新时间'),
    )
    op.create_index('ix_task_comment_read_task_id', 'task_comment_read', ['task_id'])
    op.create_index('ix_task_comment_read_username', 'task_comment_read', ['username'])
    op.create_unique_constraint('uq_task_read_user', 'task_comment_read', ['task_id', 'username'])


def downgrade() -> None:
    op.drop_table('task_comment_read')
