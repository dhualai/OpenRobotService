"""tasks 表新增附件分析记忆列

Revision ID: c9d8e7f6a5b4
Revises: 38a88928cc6b
Create Date: 2026-08-14 00:00:00.000000

为 tasks 表新增 attachment_analysis JSON 列，记录每个已分析附件的摘要，
供 AI（U老师）判断每次讨论时需重新分析的附件，避免重复分析历史附件、浪费 token。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d8e7f6a5b4'
down_revision: Union[str, None] = '38a88928cc6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('attachment_analysis', sa.JSON(), nullable=True,
                                     comment='附件分析记忆：{object_path: {filename, kind, summary, analyzed_at}}'))


def downgrade() -> None:
    op.drop_column('tasks', 'attachment_analysis')
