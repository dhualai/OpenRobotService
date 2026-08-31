"""创建 task_dispatch_log 表（二次派单感知增强）

Revision ID: a9b8c7d6e5f4
Revises: b8e3f9c2a1d4
Create Date: 2026-08-31 14:00:00.000000

⚠️ 手工最小迁移（非 autogenerate）：本库 ORM 模型与实际 schema 存在历史漂移，
autogenerate 会误生成大量无关改动。故此处仅做"外科手术式"建表 —— 只新增
`task_dispatch_log` 一张表，不触碰任何既有表/数据。

对应模型：`backend/app/models/task_dispatch_log.py::TaskDispatchLog`
用途：每轮派单（含首次）追加一条完整评估（Top10 候选快照 + 派单解释 + 同名/拼音/补画像标记），
append-only；前端只读最新一条。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = 'b8e3f9c2a1d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'task_dispatch_log',
        sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column('task_id', sa.BigInteger(), nullable=False, comment='任务ID（1 工单 → N 轮派单）'),
        sa.Column('dispatch_round', sa.Integer(), nullable=False, comment='派单轮次（第 1 次派单=1，重派自增）'),
        sa.Column('preferred_id', sa.String(length=50), nullable=True, comment='意向处理人 users.id（首次派单可为 NULL）'),
        sa.Column('assigned_id', sa.String(length=50), nullable=False, comment='实际接单人 users.id'),
        sa.Column('confidence', sa.Float(), nullable=True, comment='派单置信度（拼音命中略降 0.85）'),
        sa.Column('decision_type', sa.String(length=20), nullable=True, comment='auto / recommend / fallback'),
        sa.Column('reasoning', sa.Text(), nullable=True, comment='派单理由'),
        sa.Column('profile', sa.JSON(), nullable=True, comment='被派人画像 {dept, job_level, modules, duty, missing:[...]}'),
        sa.Column('candidates', sa.JSON(), nullable=True, comment='本轮精排 Top10 快照'),
        sa.Column('matched_pref', sa.Boolean(), nullable=True, comment='是否派到意向处理人'),
        sa.Column('name_collision', sa.Boolean(), nullable=True, comment='是否按姓名命中多人（同名）'),
        sa.Column('pinyin_match', sa.Boolean(), nullable=True, comment='是否经拼音/近似名匹配命中'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False, comment='派单时间'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_dispatch_log_id', 'task_dispatch_log', ['id'])
    op.create_index('ix_task_dispatch_log_task_id', 'task_dispatch_log', ['task_id'])
    op.create_index('ix_task_dispatch_log_created_at', 'task_dispatch_log', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_task_dispatch_log_created_at', table_name='task_dispatch_log')
    op.drop_index('ix_task_dispatch_log_task_id', table_name='task_dispatch_log')
    op.drop_index('ix_task_dispatch_log_id', table_name='task_dispatch_log')
    op.drop_table('task_dispatch_log')
