"""通知按接收人工作时间推送：时区 / 工作日历 / 节假日 / Outbox

Revision ID: c3a1d5e7f902
Revises: e1f4a7b0c326
Create Date: 2026-10-12 00:00:00.000000

1. users 新增 4 列：timezone / timezone_locked / timezone_source / timezone_updated_at
2. companies 新增 3 列：work_start / work_end / workdays
3. 新建 company_holidays（法定节假日 / 调休补班）
4. 新建 notification_outbox（按接收人工作时间排程发送）

注：本迁移原先接在 b8c1d4e7f203 之后，rebase 到 test 后改接 e1f4a7b0c326，
    避免出现两个 head（alembic upgrade head 会报 "Multiple head revisions"）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3a1d5e7f902'
down_revision: Union[str, None] = 'e1f4a7b0c326'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) users：通知时区
    op.add_column('users', sa.Column('timezone', sa.String(length=64), nullable=True,
                                     comment='用户时区（IANA，如 Asia/Shanghai）'))
    op.add_column('users', sa.Column('timezone_locked', sa.TINYINT(), nullable=False,
                                     server_default='0', comment='时区锁定：1=锁定，前端自动上报失效'))
    op.add_column('users', sa.Column('timezone_source', sa.String(length=16), nullable=True,
                                     comment='时区来源：browser/manual/system'))
    op.add_column('users', sa.Column('timezone_updated_at', sa.DateTime(), nullable=True,
                                     comment='时区最近更新时间'))

    # 2) companies：公司级工作时段与工作日
    op.add_column('companies', sa.Column('work_start', sa.String(length=5), nullable=True,
                                         server_default='09:00', comment='工作开始时间 HH:MM（本地时间）'))
    op.add_column('companies', sa.Column('work_end', sa.String(length=5), nullable=True,
                                         server_default='18:00', comment='工作结束时间 HH:MM（本地时间）'))
    op.add_column('companies', sa.Column('workdays', sa.String(length=20), nullable=True,
                                         server_default='1,2,3,4,5', comment='工作日（ISO 星期，1=周一，逗号分隔）'))

    # 3) company_holidays：法定节假日 / 调休补班
    op.create_table(
        'company_holidays',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('company_id', sa.String(length=64), nullable=False, comment='所属公司ID'),
        sa.Column('holiday_date', sa.Date(), nullable=False, comment='日期'),
        sa.Column('name', sa.String(length=64), nullable=True, comment='名称，如 国庆节 / 国庆调休补班'),
        sa.Column('type', sa.String(length=16), nullable=False, server_default='holiday',
                  comment='holiday=放假；workday_override=调休补班'),
        sa.Column('created_by', sa.String(length=64), nullable=True, comment='创建人用户ID'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_company_holidays_company_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('company_id', 'holiday_date', name='uq_company_holiday_date'),
    )
    op.create_index('ix_company_holidays_company_id', 'company_holidays', ['company_id'], unique=False)

    # 4) notification_outbox：排程待发
    op.create_table(
        'notification_outbox',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('notify_type', sa.Integer(), nullable=False,
                  comment='通知类型（对应 template.yaml 模板编号）'),
        sa.Column('task_id', sa.Integer(), nullable=True, comment='关联工单ID（统计类通知可为空）'),
        sa.Column('recipient_id', sa.String(length=64), nullable=False,
                  comment='接收人 users.id（微信登录用户即 openid）'),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False, comment='计划发送时刻（UTC）'),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='pending',
                  comment='pending=待发 / sending=投递中 / sent=已发 / failed=失败 / cancelled=已取消'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0',
                  comment='已投递尝试次数'),
        sa.Column('idempotency_key', sa.String(length=191), nullable=False,
                  comment='幂等键，防止重复排程'),
        sa.Column('payload', sa.JSON(), nullable=True, comment='附加参数（如逾期第 N 天的 occurrence）'),
        sa.Column('last_error', sa.Text(), nullable=True, comment='最近一次失败原因'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True, comment='实际发送时刻（UTC）'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_outbox_status_scheduled', 'notification_outbox', ['status', 'scheduled_at'], unique=False)
    op.create_index('ix_outbox_task_id', 'notification_outbox', ['task_id'], unique=False)
    op.create_index('ix_outbox_recipient_id', 'notification_outbox', ['recipient_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_outbox_recipient_id', table_name='notification_outbox')
    op.drop_index('ix_outbox_task_id', table_name='notification_outbox')
    op.drop_index('ix_outbox_status_scheduled', table_name='notification_outbox')
    op.drop_table('notification_outbox')

    op.drop_index('ix_company_holidays_company_id', table_name='company_holidays')
    op.drop_table('company_holidays')

    op.drop_column('companies', 'workdays')
    op.drop_column('companies', 'work_end')
    op.drop_column('companies', 'work_start')

    op.drop_column('users', 'timezone_updated_at')
    op.drop_column('users', 'timezone_source')
    op.drop_column('users', 'timezone_locked')
    op.drop_column('users', 'timezone')
