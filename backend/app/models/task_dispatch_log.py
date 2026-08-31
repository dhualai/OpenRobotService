"""任务派单日志表（append-only，1:N）。

承载「二次派单感知增强」方案（需求方案：`.agents/.../需求方案/二次派单感知增强设计方案.md`）：
- R2：每轮派单的精排 Top10 候选快照（`candidates`）
- R3：每轮派单结果解释（`assigned_id/reasoning/confidence/profile/...`）
- R4：同名命中（`name_collision`）/ 拼音近似名命中（`pinyin_match`）标记、
      被派人画像完整性（`profile.missing`）提醒

设计要点：
- **append-only**：每轮派单（含首次）写一条，历史保留不清，前端只读最新一条
  （`ORDER BY dispatch_round DESC LIMIT 1`）。
- **不设 (task_id, dispatch_round) 唯一约束**：幂等依赖 `tasks.status`（派单成功→in_progress
  不再扫描；重派由用户主动发起回 new），防止 Worker 双通道（PubSub + 定时扫描）重复写。
- `dispatch_round` 由 Worker 落库时 `MAX(round)+1` 计算。
"""

from sqlalchemy import (
    Column, BigInteger, Integer, String, Float, Boolean, Text, JSON,
    DateTime, ForeignKey,
)
from sqlalchemy.sql import func

from app.models.base import Base


class TaskDispatchLog(Base):
    """任务派单日志（每轮派单一条完整评估）"""

    __tablename__ = "task_dispatch_log"

    id = Column(BigInteger, primary_key=True, index=True, comment="日志ID")
    task_id = Column(
        BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False, index=True, comment="任务ID（1 工单 → N 轮派单）",
    )
    dispatch_round = Column(Integer, nullable=False, comment="派单轮次（第 1 次派单=1，重派自增）")

    # 本轮派单上下文
    preferred_id = Column(String(50), nullable=True, comment="意向处理人 users.id（首次派单可为 NULL）")
    assigned_id = Column(String(50), nullable=False, comment="实际接单人 users.id")

    # 派单结果解释（R3）
    confidence = Column(Float, nullable=True, comment="派单置信度（拼音命中略降 0.85）")
    decision_type = Column(String(20), nullable=True, comment="auto / recommend / fallback")
    reasoning = Column(Text, nullable=True, comment="派单理由")

    # 被派人画像 + 完整性（R4）
    profile = Column(
        JSON, nullable=True,
        comment="被派人画像 {dept, job_level, modules, duty, missing:[...]}；missing=缺失画像字段",
    )

    # R2：本轮精排 Top10 候选快照
    candidates = Column(
        JSON, nullable=True,
        comment="本轮精排 Top10 快照：[{rank, engineer_id, name, scores, profile, tags}]",
    )

    # R3/R4 判定标记
    matched_pref = Column(Boolean, default=False, nullable=True, comment="是否派到意向处理人")
    name_collision = Column(Boolean, default=False, nullable=True, comment="是否按姓名命中多人（同名）")
    pinyin_match = Column(Boolean, default=False, nullable=True, comment="是否经拼音/近似名匹配命中")

    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True, comment="派单时间")

    def __repr__(self):
        return (
            f"<TaskDispatchLog(id={self.id}, task_id={self.task_id}, "
            f"round={self.dispatch_round}, assigned={self.assigned_id})>"
        )
