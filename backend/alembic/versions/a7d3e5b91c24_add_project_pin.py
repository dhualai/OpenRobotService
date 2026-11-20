"""add_project_pin

新增 project_pin 表：项目「置顶」标注（个人置顶，项目进度管理页长按卡片 → 置顶）。

置顶按人隔离（主键 (project_id, operator)），与 project_info_node_mark（信息节点关注）
同一套口径：同一项目可被多人各存一行，列表顺序按当前登录人过滤。

做法（幂等）：本仓库建表是 create_all 与 Alembic 双轨并行——应用启动时
`Base.metadata.create_all` 已经会按 ORM 定义把这表建出来，走过启动的库再跑本迁移
会撞 "table already exists"。所以这里先查 information_schema，表在就整段跳过。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'a7d3e5b91c24'
down_revision: Union[str, None] = '6f2c8a1d9b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = 'project_pin'


def _table_exists() -> bool:
    bind = op.get_bind()
    result = bind.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"
    ), {"t": TABLE}).scalar()
    return bool(result)


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        TABLE,
        sa.Column('project_id', sa.String(length=64), nullable=False, comment='被置顶的项目ID'),
        sa.Column('operator', sa.String(length=64), nullable=False, comment='置顶人登录名（置顶列表按人隔离）'),
        sa.Column('operator_name', sa.String(length=64), nullable=True, comment='置顶人显示名'),
        sa.Column('created_at', sa.String(length=30), nullable=False, comment='置顶时间'),
        sa.PrimaryKeyConstraint('project_id', 'operator'),
    )
    op.create_index('idx_project_pin_operator', TABLE, ['operator', 'created_at'])


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_index('idx_project_pin_operator', table_name=TABLE)
    op.drop_table(TABLE)
