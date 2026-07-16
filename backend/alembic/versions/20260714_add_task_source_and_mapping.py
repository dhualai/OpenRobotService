"""add task source and user mapping

INTEGRATION_DESIGN.md Phase 1:
- tasks 表增加 source / external_id / external_url 字段 + (source, external_id) 唯一约束
- 新建 task_user_mapping 表（外部任务源账号 -> 本平台用户映射，跨源通用）

Revision ID: 20260714_add_task_source_and_mapping
Revises: 20260710_wave2_ticket_to_task
Create Date: 2026-07-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_add_task_source_and_mapping"
down_revision: Union[str, None] = "20260710_wave2_ticket_to_task"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- tasks 表新增字段 ---
    op.add_column(
        "tasks",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
            comment="任务来源：manual / zentao / ...",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("external_id", sa.String(length=64), nullable=True, comment="外部系统任务ID"),
    )
    op.add_column(
        "tasks",
        sa.Column("external_url", sa.String(length=512), nullable=True, comment="外部系统跳转链接"),
    )

    op.create_index("ix_tasks_source", "tasks", ["source"])
    op.create_index("ix_tasks_external_id", "tasks", ["external_id"])
    # MySQL 允许多个 NULL，故 manual 任务（external_id=NULL）不冲突
    op.create_unique_constraint("uq_task_source_external", "tasks", ["source", "external_id"])

    # --- task_user_mapping 表 ---
    op.create_table(
        "task_user_mapping",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="映射ID"),
        sa.Column("source", sa.String(length=32), nullable=False, comment="任务源：zentao / ..."),
        sa.Column("external_account", sa.String(length=64), nullable=False, comment="外部系统账号"),
        sa.Column("external_realname", sa.String(length=128), nullable=True, comment="外部账号姓名"),
        sa.Column("local_user_id", sa.String(length=50), nullable=False, comment="本平台 user_id"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False, comment="更新时间"),
        sa.UniqueConstraint("source", "external_account", name="uq_mapping_src_account"),
        comment="外部任务源账号 -> 本平台用户映射（跨源通用）",
    )
    op.create_index("ix_task_user_mapping_source", "task_user_mapping", ["source"])
    op.create_index("ix_task_user_mapping_local_user_id", "task_user_mapping", ["local_user_id"])


def downgrade() -> None:
    op.drop_index("ix_task_user_mapping_local_user_id", table_name="task_user_mapping")
    op.drop_index("ix_task_user_mapping_source", table_name="task_user_mapping")
    op.drop_table("task_user_mapping")

    op.drop_constraint("uq_task_source_external", "tasks", type_="unique")
    op.drop_index("ix_tasks_external_id", table_name="tasks")
    op.drop_index("ix_tasks_source", table_name="tasks")
    op.drop_column("tasks", "external_url")
    op.drop_column("tasks", "external_id")
    op.drop_column("tasks", "source")
