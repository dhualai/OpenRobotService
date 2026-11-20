"""tasks.created_by 从 users.username 迁到 users.id

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-08-18 17:40:00.000000

与 assigned_to 迁移同口径。已是 users.id 的行不会被改写，可重复执行。
生产环境 alembic 历史若不干净，请用 scripts/migrate_task_created_by_user_id.py。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks t
        INNER JOIN users u ON t.created_by = u.username
        SET t.created_by = u.id
        WHERE t.created_by IS NOT NULL
          AND t.created_by <> ''
          AND t.created_by <> u.id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE tasks t
        INNER JOIN users u ON t.created_by = u.id
        SET t.created_by = u.username
        WHERE t.created_by IS NOT NULL
          AND t.created_by <> ''
          AND t.created_by <> u.username
        """
    )
