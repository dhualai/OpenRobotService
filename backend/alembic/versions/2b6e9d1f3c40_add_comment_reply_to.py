"""add_comment_reply_to

讨论区消息引用：task_comments 新增 reply_to 列（引用的评论ID）。

Revision ID: 2b6e9d1f3c40
Revises: 26021ebb27b1
Create Date: 2026-08-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2b6e9d1f3c40'
down_revision: Union[str, None] = '26021ebb27b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'task_comments',
        sa.Column('reply_to', sa.BigInteger(), nullable=True, comment='引用的评论ID（消息引用/回复）'),
    )
    op.create_index('ix_task_comments_reply_to', 'task_comments', ['reply_to'])


def downgrade() -> None:
    op.drop_index('ix_task_comments_reply_to', table_name='task_comments')
    op.drop_column('task_comments', 'reply_to')
