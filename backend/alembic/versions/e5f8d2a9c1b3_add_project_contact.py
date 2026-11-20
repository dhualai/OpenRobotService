"""add_project_contact

项目对接人（区别于项目经理 project_manager / 既有 contact_person），
在 USP 项目详情页顶部「项目经理」下方新增可编辑输入框，字段落库 project_contact。

Revision ID: e5f8d2a9c1b3
Revises: d4a6e0f13b57
Create Date: 2026-08-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f8d2a9c1b3'
down_revision: Union[str, None] = 'd4a6e0f13b57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('project', sa.Column('project_contact', sa.String(length=50), nullable=True, comment='对接人'))


def downgrade() -> None:
    op.drop_column('project', 'project_contact')
