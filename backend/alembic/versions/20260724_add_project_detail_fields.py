"""add project detail fields

项目详情页扩展字段：
- project_type: 项目类型（企业微信"项目类型"表头原值，区别于分类依据 category_basis）
- stage_notes: 生命周期各阶段补充说明（JSON，键为阶段名）
- risk_carrying_type / special_attention / risk_task_description / management_strategy: 风险管理卡片
- project_documents: 项目文档（JSON，[{name,resource_id,url}]），文件本体经 /api/admin/resources/ 上传
- sales / pre_sales / project_manager / field_engineer: 责任体系角色

Revision ID: 20260724_add_project_detail_fields
Revises: 20260714_add_task_source_and_mapping
Create Date: 2026-07-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_add_project_detail_fields"
down_revision: Union[str, None] = "20260714_add_task_source_and_mapping"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("project", sa.Column("project_type", sa.String(length=20), nullable=True, comment="项目类型（企业微信项目类型字段原值）"))
    op.add_column("project", sa.Column("stage_notes", sa.String(length=4000), nullable=True, comment="生命周期各阶段补充说明(JSON格式，键为阶段名)"))
    op.add_column("project", sa.Column("risk_carrying_type", sa.String(length=20), nullable=True, comment="风险承接类型"))
    op.add_column("project", sa.Column("special_attention", sa.String(length=1000), nullable=True, comment="特别关注说明"))
    op.add_column("project", sa.Column("risk_task_description", sa.String(length=2000), nullable=True, comment="风险和任务描述"))
    op.add_column("project", sa.Column("management_strategy", sa.String(length=2000), nullable=True, comment="项目管理策略"))
    op.add_column("project", sa.Column("project_documents", sa.String(length=2000), nullable=True, comment="项目文档(JSON格式，[{name,resource_id,url}])"))
    op.add_column("project", sa.Column("sales", sa.String(length=50), nullable=True, comment="销售"))
    op.add_column("project", sa.Column("pre_sales", sa.String(length=50), nullable=True, comment="售前"))
    op.add_column("project", sa.Column("project_manager", sa.String(length=50), nullable=True, comment="项目经理"))
    op.add_column("project", sa.Column("field_engineer", sa.String(length=50), nullable=True, comment="实施工程师"))


def downgrade() -> None:
    op.drop_column("project", "field_engineer")
    op.drop_column("project", "project_manager")
    op.drop_column("project", "pre_sales")
    op.drop_column("project", "sales")
    op.drop_column("project", "project_documents")
    op.drop_column("project", "management_strategy")
    op.drop_column("project", "risk_task_description")
    op.drop_column("project", "special_attention")
    op.drop_column("project", "risk_carrying_type")
    op.drop_column("project", "stage_notes")
    op.drop_column("project", "project_type")
