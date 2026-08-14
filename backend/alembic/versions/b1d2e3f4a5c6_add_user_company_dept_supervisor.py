"""users 表新增公司/部门/直属上级关联字段

Revision ID: b1d2e3f4a5c6
Revises: 9f3b7c2a1d40
Create Date: 2026-08-14 00:00:00.000000

补齐 app/models/identity.py UserDB 中已定义但数据库缺失的列：
- company_id    -> companies.id 外键
- department_id -> departments.id 外键
- supervisor_id -> users.id 自引用外键
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1d2e3f4a5c6'
down_revision: Union[str, None] = '9f3b7c2a1d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('company_id', sa.String(length=64), nullable=True, comment='公司ID'))
    op.add_column('users', sa.Column('department_id', sa.String(length=64), nullable=True, comment='部门ID'))
    op.add_column('users', sa.Column('supervisor_id', sa.String(length=64), nullable=True, comment='直属上级用户ID（全局行政汇报线）'))
    op.create_index(op.f('ix_users_company_id'), 'users', ['company_id'], unique=False)
    op.create_index(op.f('ix_users_department_id'), 'users', ['department_id'], unique=False)
    op.create_index(op.f('ix_users_supervisor_id'), 'users', ['supervisor_id'], unique=False)
    op.create_foreign_key('fk_users_company_id', 'users', 'companies', ['company_id'], ['id'])
    op.create_foreign_key('fk_users_department_id', 'users', 'departments', ['department_id'], ['id'])
    op.create_foreign_key('fk_users_supervisor_id', 'users', 'users', ['supervisor_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_users_supervisor_id', 'users', type_='foreignkey')
    op.drop_constraint('fk_users_department_id', 'users', type_='foreignkey')
    op.drop_constraint('fk_users_company_id', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_supervisor_id'), table_name='users')
    op.drop_index(op.f('ix_users_department_id'), table_name='users')
    op.drop_index(op.f('ix_users_company_id'), table_name='users')
    op.drop_column('users', 'supervisor_id')
    op.drop_column('users', 'department_id')
    op.drop_column('users', 'company_id')
