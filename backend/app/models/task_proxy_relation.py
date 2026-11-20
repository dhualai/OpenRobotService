"""代他人提单（代理提单）关系表 —— tasks 与「代理人/被代理人」的绑定。

需求方案：`docs/PRODUCT/代他人提单（代理提单）功能设计方案.md`

语义要点（与设计文档 §3.2/§3.3 对齐）：
- **一对一、创建时确定、全程不变**：一单最多一条关系（UNIQUE(task_id) 由业务保证，
  唯一键取 (task_id, agent_id, principal_id) 以兼容未来「一人代多单」的查询习惯）。
- **`tasks.created_by` 语义不变**（= 代理人），被代理人的参与权全部由本表派生。
- **状态机 3 态**：`pending` →（`acknowledged` 确认跟进 / `declined` 与我无关）；
  无 `transferred`（接手）与用户层 `revoked`（撤销）——关系按生命周期自然终结。
- **declined 是终态但工单不中断**：代理人兜底推进，`created_by` 身份不受影响。

预留字段（`scope` / `valid_until` / `delegation_id`）供后续「长期委托授权」方案平滑升级，
本期不写入、不参与判定。
"""

from sqlalchemy import (
    Column, BigInteger, String, DateTime, ForeignKey, Index, UniqueConstraint,
)
from sqlalchemy.sql import func

from app.models.base import Base


class ProxyRelationStatus:
    """关系状态常量（不用 SQLEnum：避免 MySQL 原生 ENUM 加值需 DDL）。"""

    PENDING = "pending"            # 已代提，等待被代理人确认（只读态）
    ACKNOWLEDGED = "acknowledged"  # 已确认跟进，获协办权
    DECLINED = "declined"          # 与我无关，关系终态


class ProxyRelationSource:
    """关系来源。"""

    MANUAL = "manual"  # 界面「为他人提单」开关
    AI = "ai"          # AI 对话识别（P4 二期）
    ADMIN = "admin"    # 后台代建


class TaskProxyRelation(Base):
    """代他人提单关系（1 工单 → 至多 1 条生效关系）。"""

    __tablename__ = "task_proxy_relation"

    id = Column(BigInteger, primary_key=True, index=True, comment="关系ID")

    task_id = Column(
        BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="工单ID（tasks.id）",
    )

    # --- 代理人（= tasks.created_by，冗余存 username 与 user_identity 双键语义对齐）---
    agent_id = Column(String(50), nullable=False, index=True, comment="代理人 users.id")
    agent_username = Column(String(100), nullable=True, comment="代理人 username（双键兜底）")

    # --- 被代理人（参与权来源）---
    principal_id = Column(String(50), nullable=False, index=True, comment="被代理人 users.id")
    principal_username = Column(String(100), nullable=True, comment="被代理人 username（双键兜底）")

    # --- 状态机 ---
    relation_status = Column(
        String(20), nullable=False, default=ProxyRelationStatus.PENDING,
        index=True, comment="pending / acknowledged / declined",
    )
    source = Column(String(20), nullable=False, default=ProxyRelationSource.MANUAL,
                    comment="关系来源：manual / ai / admin")
    remark = Column(String(500), nullable=True, comment="备注；declined 时为拒绝原因")

    # --- 时间线（审计留痕）---
    notified_at = Column(DateTime, nullable=True, comment="首次通知被代理人时间（UTC）")
    acked_at = Column(DateTime, nullable=True, comment="被代理人确认跟进时间（UTC）")
    declined_at = Column(DateTime, nullable=True, comment="被代理人拒绝时间（UTC）")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间（UTC）")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(),
        nullable=False, comment="更新时间（UTC）",
    )

    # --- 预留：长期委托授权方案（本期不写入）---
    scope = Column(String(20), nullable=True, comment="预留：single / delegation")
    valid_until = Column(DateTime, nullable=True, comment="预留：委托有效期（UTC）")
    delegation_id = Column(BigInteger, nullable=True, comment="预留：user_delegation.id")

    __table_args__ = (
        UniqueConstraint("task_id", "agent_id", "principal_id",
                         name="uk_task_agent_principal"),
        Index("idx_principal_status", "principal_id", "relation_status"),
    )

    def __repr__(self) -> str:
        return (
            f"<TaskProxyRelation(id={self.id}, task_id={self.task_id}, "
            f"agent={self.agent_id}, principal={self.principal_id}, "
            f"status={self.relation_status})>"
        )
