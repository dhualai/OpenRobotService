"""合并多 head 并为 tasks 表新增 curr_step_agreed 列

Revision ID: f1c8d2a7b3e9
Revises: a9b8c7d6e5f4, ff72f57c4e73
Create Date: 2026-09-02 20:00:00.000000

合并既有双 head（a9b8c7d6e5f4 / ff72f57c4e73），同时为 tasks 表新增
`curr_step_agreed` 布尔列，标记当前协商节点是否已协商一致：
- 处理人/创建人点击「确认同意」(respond) → 置 True
- 「协商节点时间」(negotiate-step) 或「当前阶段完成」(complete-step) → 重置为 False

存量工单默认 False，即视为「未一致」，需相应角色重新确认同意后才会进入协商一致状态。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'f1c8d2a7b3e9'
down_revision: Union[str, Sequence[str], None] = ('a9b8c7d6e5f4', 'ff72f57c4e73')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = :tbl "
            "AND COLUMN_NAME = :name"
        ),
        {"tbl": table_name, "name": column_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    # 幂等：列已存在则跳过（本地开发库可能已通过 _schema_diff.py 补齐）
    if not _column_exists('tasks', 'curr_step_agreed'):
        op.add_column(
            'tasks',
            sa.Column(
                'curr_step_agreed',
                sa.Boolean(),
                nullable=False,
                server_default=sa.text('0'),
                comment='当前协商节点是否已协商一致：respond 置 True；negotiate-step/complete-step 重置为 False',
            ),
        )
    if not _column_exists('tasks', 'escalate_count'):
        op.add_column(
            'tasks',
            sa.Column(
                'escalate_count',
                sa.Integer(),
                nullable=False,
                server_default=sa.text('0'),
                comment='升级上报次数：>0 表示已升级，协商回合重置为1且不再受限',
            ),
        )
    # 协商回合初始值从 1 开始：将存量 0 修正为 1
    op.execute(text("UPDATE tasks SET step_negotiation_round = 1 WHERE step_negotiation_round = 0 OR step_negotiation_round IS NULL"))
    # 修正 server_default 为 1（新建工单默认 1）
    op.alter_column('tasks', 'step_negotiation_round',
                    server_default=sa.text('1'))


def downgrade() -> None:
    op.drop_column('tasks', 'curr_step_agreed')
