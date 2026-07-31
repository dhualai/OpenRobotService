"""add_transport_efficiency_tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'project_transport_efficiency',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_code', sa.String(length=50), nullable=False, comment='项目代码'),
        sa.Column('report_date', sa.String(length=20), nullable=False, comment='数据日期'),
        sa.Column('total_tasks', sa.Integer(), nullable=True, comment='总任务数'),
        sa.Column('carry_task_count', sa.Integer(), nullable=True, comment='搬运任务数量'),
        sa.Column('effective_work_hours', sa.Float(), nullable=True, comment='有效工作时长(小时)'),
        sa.Column('fault_hours', sa.Float(), nullable=True, comment='机器人故障时长(小时)'),
        sa.Column('idle_hours', sa.Float(), nullable=True, comment='空闲无任务时间(小时)'),
        sa.Column('avg_error_count', sa.Float(), nullable=True, comment='平均错误次数'),
        sa.Column('avg_fault_duration_minutes', sa.Float(), nullable=True, comment='平均单次故障时间(分钟)'),
        sa.Column('avg_carry_duration_minutes', sa.Float(), nullable=True, comment='平均单次搬运任务时间(分钟)'),
        sa.Column('avg_manual_switch_count', sa.Float(), nullable=True, comment='平均切手动次数'),
        sa.Column('manual_intervention_rate', sa.Float(), nullable=True, comment='人工干预率(0-1小数)'),
        sa.Column('created_at', sa.String(length=30), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.String(length=30), nullable=True, comment='更新时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_te_project_date', 'project_transport_efficiency',
        ['project_code', 'report_date'], unique=True,
    )

    op.create_table(
        'project_transport_efficiency_robot',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_code', sa.String(length=50), nullable=False, comment='项目代码'),
        sa.Column('report_date', sa.String(length=20), nullable=False, comment='数据日期'),
        sa.Column('robot_model', sa.String(length=50), nullable=False, comment='AGV机器人型号'),
        sa.Column('carry_task_total', sa.Integer(), nullable=True, comment='搬运任务总数(个)'),
        sa.Column('effective_work_hours', sa.Float(), nullable=True, comment='有效工作时长(h)'),
        sa.Column('effective_efficiency', sa.Float(), nullable=True, comment='有效搬运效率(小时/个)'),
        sa.Column('fault_hours', sa.Float(), nullable=True, comment='机器人故障时间(小时)'),
        sa.Column('idle_hours', sa.Float(), nullable=True, comment='无工作时间(小时)'),
        sa.Column('avg_fault_duration_minutes', sa.Float(), nullable=True, comment='平均单次故障(分钟)'),
        sa.Column('avg_carry_duration_minutes', sa.Float(), nullable=True, comment='平均单次搬运时间(分钟)'),
        sa.Column('created_at', sa.String(length=30), nullable=False, comment='创建时间'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'idx_ter_project_date_model', 'project_transport_efficiency_robot',
        ['project_code', 'report_date', 'robot_model'], unique=True,
    )


def downgrade() -> None:
    op.drop_index('idx_ter_project_date_model', table_name='project_transport_efficiency_robot')
    op.drop_table('project_transport_efficiency_robot')
    op.drop_index('idx_te_project_date', table_name='project_transport_efficiency')
    op.drop_table('project_transport_efficiency')
