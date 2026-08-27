"""users 表新增手机号列（企业微信通知 @ 人用）

Revision ID: b8e3f9c2a1d4
Revises: a7d1e2f3g4h5
Create Date: 2026-08-27 12:00:00.000000

commit 8f2c947「企业微信通知」在 User 模型加了 phone 列但漏了迁移，
本文件补齐。注意：已存在的库（create_all 不会为已有表补列）需手动执行
ALTER TABLE users ADD COLUMN phone ...，或本迁移 apply。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8e3f9c2a1d4'
down_revision: Union[str, Sequence[str], None] = 'a7d1e2f3g4h5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column('phone', sa.String(20), nullable=True,
                  comment='用户手机号（企业微信通知 @ 人用）'),
    )
    op.create_index('ix_users_phone', 'users', ['phone'])


def downgrade() -> None:
    op.drop_index('ix_users_phone', table_name='users')
    op.drop_column('users', 'phone')
