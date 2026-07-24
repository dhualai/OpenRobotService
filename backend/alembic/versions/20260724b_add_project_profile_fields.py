"""add project basic profile fields

项目基础画像卡片扩展字段：
- internal_code: 内部编号
- project_region: 项目区域/地点
- total_vehicle_count: 总车数
- controller_vendor: 控制器选择
- system_integration: 系统/外设对接（JSON数组，多选）
- server_deployment_status: 服务器部署

Revision ID: 20260724b_add_project_profile_fields
Revises: 20260724_add_project_detail_fields
Create Date: 2026-07-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260724b_add_project_profile_fields"
down_revision: Union[str, None] = "20260724_add_project_detail_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project", sa.Column("internal_code", sa.String(length=50), nullable=True, comment="内部编号"))
    op.add_column("project", sa.Column("project_region", sa.String(length=30), nullable=True, comment="项目区域/地点"))
    op.add_column("project", sa.Column("total_vehicle_count", sa.Integer(), nullable=True, comment="总车数"))
    op.add_column("project", sa.Column("controller_vendor", sa.String(length=30), nullable=True, comment="控制器选择"))
    op.add_column("project", sa.Column("system_integration", sa.String(length=2000), nullable=True, comment="系统/外设对接(JSON数组)"))
    op.add_column("project", sa.Column("server_deployment_status", sa.String(length=30), nullable=True, comment="服务器部署"))


def downgrade() -> None:
    op.drop_column("project", "server_deployment_status")
    op.drop_column("project", "system_integration")
    op.drop_column("project", "controller_vendor")
    op.drop_column("project", "total_vehicle_count")
    op.drop_column("project", "project_region")
    op.drop_column("project", "internal_code")
