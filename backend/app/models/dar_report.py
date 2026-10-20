"""直答率周报存储（ORS-#981：直答率指标上服务号后台）。

数据链：本地 dar_weekly 六步 SOP 产出 processed/weekly_YYYYMMDD.json →
report 步骤落盘后自动 POST 到 /api/admin/dashboard/dar-reports →
本表按 (week_date, env) upsert → 后台「工单数据看板」页 AI 直答率区块读 latest。
payload 为 dar_weekly 周报原样 JSON（dar_rates/kb_gap/wow/this_week/matrix...），
展示侧只取摘要字段，fails 明细（含用户对话）不上服务号页面。
"""
from sqlalchemy import Column, DateTime, func, Integer, String, Text, UniqueConstraint

from app.models.base import Base


class DarWeeklyReport(Base):
    __tablename__ = "dar_weekly_reports"

    id = Column(Integer, primary_key=True, index=True)
    week_date = Column(String(8), nullable=False, comment="周报日期 YYYYMMDD（dar_weekly 生成日）")
    env = Column(String(16), nullable=False, default="prod", comment="环境 prod/test")
    payload = Column(Text, nullable=False, comment="周报原样 JSON（dar_weekly weekly_*.json 内容）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("week_date", "env", name="uq_dar_week_date_env"),
    )
