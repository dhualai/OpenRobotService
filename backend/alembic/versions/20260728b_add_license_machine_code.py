"""add project_license machine_code field

项目授权申请功能：
- machine_code: 申请授权码时绑定的机器码/MAC地址（可为空，兼容历史数据）

Revision ID: 20260728b_add_license_machine_code
Revises: 20260728_add_user_personnel_fields
Create Date: 2026-07-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260728b_add_license_machine_code"
down_revision: Union[str, None] = "20260728_add_user_personnel_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project_license", sa.Column("machine_code", sa.String(200), nullable=True, comment="机器码/MAC地址"))


def downgrade() -> None:
    op.drop_column("project_license", "machine_code")
