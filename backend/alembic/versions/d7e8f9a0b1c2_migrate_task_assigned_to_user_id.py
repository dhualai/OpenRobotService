"""tasks.assigned_to 从 users.username 迁到 users.id

Revision ID: d7e8f9a0b1c2
Revises: c9d8e7f6a5b4
Create Date: 2026-08-18 16:40:00.000000

派单标识统一绑定 users.id。存量 tasks.assigned_to 原先存的是 username
（如 wechat_xxx / admin），本迁移按 username 精确匹配回填 users.id。
已是 users.id 的行（assigned_to = users.id）不会被改写，可重复执行。
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c9d8e7f6a5b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tasks t
        INNER JOIN users u ON t.assigned_to = u.username
        SET t.assigned_to = u.id
        WHERE t.assigned_to IS NOT NULL
          AND t.assigned_to <> ''
          AND t.assigned_to <> u.id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE tasks t
        INNER JOIN users u ON t.assigned_to = u.id
        SET t.assigned_to = u.username
        WHERE t.assigned_to IS NOT NULL
          AND t.assigned_to <> ''
          AND t.assigned_to <> u.username
        """
    )
