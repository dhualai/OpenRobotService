"""add user personnel fields for AI assigner

为 users 表新增派单人信息字段，与 AI Assigner 共享：
- department: 部门/团队
- responsibility_modules: 责任模块（JSON）
- job_level: 职级（TINYINT, 默认1）
- duty_text: 职责画像文本

Revision ID: 20260728_add_user_personnel_fields
Revises: 20260724c_add_user_avatar_field
Create Date: 2026-07-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_add_user_personnel_fields"
down_revision: Union[str, None] = "20260724c_add_user_avatar_field"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("department", sa.String(128), nullable=True, comment="部门/团队"))
    op.add_column("users", sa.Column("responsibility_modules", sa.JSON(), nullable=True, comment='责任模块 ["车端","任务调度","地图编辑"...]'))
    op.add_column("users", sa.Column("job_level", sa.TINYINT(), default=1, nullable=False, server_default=sa.text("1"), comment="职级，数值越高越不优先接单（1=一线, 2=管理/审核, 3=仅兜底...），默认1"))
    op.add_column("users", sa.Column("duty_text", sa.Text(), nullable=True, comment="职责画像文本，供 AI 派单匹配参考"))


def downgrade() -> None:
    op.drop_column("users", "duty_text")
    op.drop_column("users", "job_level")
    op.drop_column("users", "responsibility_modules")
    op.drop_column("users", "department")
