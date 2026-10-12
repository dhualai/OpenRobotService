"""公司工作日历 ORM 模型。

范围说明（按需求确认结果）：
- **仅公司级**日历，不做部门/用户级覆盖
- 工作时段（work_start/work_end）与工作日（workdays）挂在 `companies` 表
- 本表只承载「法定节假日 / 调休补班」这类**按日期的例外**

`type` 取值：
- `holiday`：放假（即便落在 workdays 内也不工作）
- `workday_override`：调休补班（即便落在周末/节假日也工作）
"""
from sqlalchemy import Column, String, Date, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.sql import func

from app.models.base import Base


class CompanyHoliday(Base):
    __tablename__ = "company_holidays"

    id = Column(String(64), primary_key=True)
    company_id = Column(String(64), ForeignKey("companies.id"), nullable=False,
                        comment="所属公司ID")
    holiday_date = Column(Date, nullable=False, comment="日期")
    name = Column(String(64), nullable=True, comment="名称，如 国庆节 / 国庆调休补班")
    type = Column(String(16), nullable=False, server_default="holiday",
                  comment="holiday=放假；workday_override=调休补班")
    created_by = Column(String(64), nullable=True, comment="创建人用户ID")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("company_id", "holiday_date", name="uq_company_holiday_date"),
        Index("ix_company_holidays_company_id", "company_id"),
    )
