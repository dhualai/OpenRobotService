"""通知排程（Outbox）ORM 模型。

把原先「命中即发」的通知改为「按接收人工作时间排程发送」的中间载体：
业务侧只负责生成待发记录（含幂等键，天然去重），由独立 Worker 在
`scheduled_at` 到期后投递，避免非工作时间（含凌晨/节假日）打扰接收人。

设计要点：
- `scheduled_at` 统一存 **UTC naive**（与项目其余时间列一致，见 core/db.py 强制 +00:00）
- `idempotency_key` 唯一索引：同一「工单 + 通知类型 + 接收人 + 触发次序」只排程一次
- 不存渲染快照：Worker 发送时重查工单取最新标题/截止时间/逾期天数
"""
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Index
from sqlalchemy.sql import func

from app.models.base import Base


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id = Column(String(64), primary_key=True)
    notify_type = Column(Integer, nullable=False, comment="通知类型（对应 template.yaml 模板编号）")
    task_id = Column(Integer, nullable=True, comment="关联工单ID（统计类通知可为空）")
    recipient_id = Column(String(64), nullable=False, comment="接收人 users.id（微信登录用户即 openid）")
    scheduled_at = Column(DateTime, nullable=False, comment="计划发送时刻（UTC）")
    status = Column(String(16), nullable=False, server_default="pending",
                    comment="pending=待发 / sending=投递中 / sent=已发 / failed=失败 / cancelled=已取消")
    attempt_count = Column(Integer, nullable=False, server_default="0", comment="已投递尝试次数")
    idempotency_key = Column(String(191), nullable=False, unique=True, comment="幂等键，防止重复排程")
    payload = Column(JSON, nullable=True, comment="附加参数（如逾期第 N 天的 occurrence）")
    last_error = Column(Text, nullable=True, comment="最近一次失败原因")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    sent_at = Column(DateTime, nullable=True, comment="实际发送时刻（UTC）")

    __table_args__ = (
        Index("ix_outbox_status_scheduled", "status", "scheduled_at"),
        Index("ix_outbox_task_id", "task_id"),
        Index("ix_outbox_recipient_id", "recipient_id"),
    )
