"""Wave 2.2: tickets -> tasks 语义升格

MIGRATION.md Wave 2.2: 将工单(tickets/ticket_comments)重命名为任务(tasks/task_comments)，
落地 ARCHITECTURE.md「任务是统一抽象、工单是其类型」。

操作：
- 重命名表：tickets -> tasks, ticket_comments -> task_comments
- 外键约束：删除旧外键，重建新外键
"""
from typing import Sequence, Union

from alembic import op

revision: str = "20260710_wave2_ticket_to_task"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("tickets", "tasks")
    op.rename_table("ticket_comments", "task_comments")
    
    op.drop_constraint("ticket_comments_ticket_id_fkey", "task_comments", type_="foreignkey")
    op.create_foreign_key(
        "task_comments_task_id_fkey",
        "task_comments",
        "tasks",
        ["ticket_id"],
        ["id"],
        ondelete="CASCADE"
    )


def downgrade() -> None:
    op.drop_constraint("task_comments_task_id_fkey", "task_comments", type_="foreignkey")
    op.create_foreign_key(
        "ticket_comments_ticket_id_fkey",
        "ticket_comments",
        "tickets",
        ["ticket_id"],
        ["id"],
        ondelete="CASCADE"
    )
    
    op.rename_table("tasks", "tickets")
    op.rename_table("task_comments", "ticket_comments")