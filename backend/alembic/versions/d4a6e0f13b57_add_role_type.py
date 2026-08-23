"""add_role_type

角色类型（系统角色 / 项目角色），用于「角色管理」页面分区展示，
以及项目授权「选择角色」仅拉取项目角色。既有角色行统一回填为 'system'，
后续可在角色管理界面手动改为 'project'。

Revision ID: d4a6e0f13b57
Revises: b3f7a1c92e40
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4a6e0f13b57'
down_revision: Union[str, None] = 'b3f7a1c92e40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('roles', sa.Column('role_type', sa.String(length=20), nullable=False,
                                      server_default='system', comment='角色类型：system=系统角色，project=项目角色'))


def downgrade() -> None:
    op.drop_column('roles', 'role_type')
