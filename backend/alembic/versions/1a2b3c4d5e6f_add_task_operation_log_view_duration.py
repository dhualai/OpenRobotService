"""add_task_operation_log_view_duration

为 task_operation_logs 表补充「查看时长」相关字段：ended_at、duration_seconds。
修复插入操作日志时报 Unknown column 'ended_at' 的错误（模型已定义该列但缺迁移）。

说明：本地开发库可能已通过 backend/_schema_diff.py --apply 手动补齐过这两列，
故 upgrade 采用幂等写法（列已存在则跳过），避免重复执行报错。

Revision ID: 1a2b3c4d5e6f
Revises: e8f9a0b1c2d3
Create Date: 2026-08-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '1a2b3c4d5e6f'
down_revision: Union[str, None] = 'e8f9a0b1c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(column_name: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'task_operation_logs' "
            "AND COLUMN_NAME = :name"
        ),
        {"name": column_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    if not _column_exists('ended_at'):
        op.add_column('task_operation_logs', sa.Column('ended_at', sa.DateTime(), nullable=True, comment='查看结束时间（仅 VIEW 有值）'))
    if not _column_exists('duration_seconds'):
        op.add_column('task_operation_logs', sa.Column('duration_seconds', sa.Integer(), nullable=True, comment='查看时长（秒，仅 VIEW 有值）'))


def downgrade() -> None:
    if _column_exists('duration_seconds'):
        op.drop_column('task_operation_logs', 'duration_seconds')
    if _column_exists('ended_at'):
        op.drop_column('task_operation_logs', 'ended_at')
