"""add user avatar field

初始版本：用户自助资料管理
- avatar_resource_id: 用户头像对应的资源管理中心资源ID（图片，可为空）

Revision ID: 20260724c_add_user_avatar_field
Revises: None
Create Date: 2026-07-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260724c_add_user_avatar_field"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_resource_id", sa.Integer(), nullable=True, comment="头像资源ID"))


def downgrade() -> None:
    op.drop_column("users", "avatar_resource_id")
