"""DAS 交付管理 ORM 模型。

原定义于 `app/modules/das/models/models.py`，现迁入此处作为唯一定义点（MIGRATION.md 阶段 1）。
`das/models/models.py` 改为从本模块再导出（其中本文件的 `ProjectDelivery` 以 `Project` 名再导出，
使 `das/services/*` 的旧导入保持不变）。

注意：DAS 的项目类在此更名为 `ProjectDelivery`，以与身份底座的 `app.models.identity.Project`
共存于同一 `app.models` 命名空间；**物理表名 `project` 保持不变**（双表合并留待 Wave 2）。

含 7 张表：realtime_data / history_data / collection_data / project / risk /
project_daily_report / project_license
"""
from sqlalchemy import Column, Integer, BigInteger, String, Text, Float, Index

from app.models.base import Base


class RealtimeData(Base):
    __tablename__ = 'realtime_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    data = Column(String(10000), nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)

    __table_args__ = (
        Index('idx_project_indicator', 'project', 'indicator'),
        Index('idx_collection_time', 'collection_time'),
    )

    def __repr__(self):
        return f"<RealtimeData(id={self.id}, project='{self.project}', indicator='{self.indicator}')>"


class HistoryData(Base):
    __tablename__ = 'history_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    data = Column(String(10000), nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)
    time_str = Column(String(100), nullable=False)
    start_time = Column(BigInteger, nullable=False)
    end_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('idx_hist_project_indicator', 'project', 'indicator'),
        Index('idx_hist_collection_time', 'collection_time'),
        Index('idx_hist_unique_key', 'project', 'indicator', 'start_time', 'end_time'),
    )

    def __repr__(self):
        return f"<HistoryData(id={self.id}, project='{self.project}', indicator='{self.indicator}', start_time='{self.start_time}', end_time='{self.end_time}')>"


class CollectionData(Base):
    __tablename__ = 'collection_data'

    id = Column(Integer, primary_key=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    start_time_int = Column(BigInteger, nullable=False, comment='数据采集开始时间戳用于查询')
    end_time_int = Column(BigInteger, nullable=False, comment='数据采集结束时间戳用于查询')
    data = Column(String(10000), nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)
    time_str = Column(String(100), nullable=False)

    __table_args__ = (
        Index('idx_coll_unique_key', 'project', 'indicator', 'start_time_int', 'end_time_int'),
        Index('idx_coll_time', 'start_time_int'),
    )

    def __repr__(self):
        return f"<CollectionData(id={self.id}, project='{self.project}', indicator='{self.indicator}', start_time_int='{self.start_time_int}', end_time_int='{self.end_time_int}')>"


class Project(Base):
    __tablename__ = 'project'

    id = Column(String(64), primary_key=True, comment='项目ID/代码，与code一致')
    code = Column(String(64), unique=True, nullable=False, comment='项目代码')
    name = Column(String(128), nullable=False, comment='项目名称')

    system_id = Column(String(50), nullable=True, comment='系统ID')
    description = Column(Text, nullable=True, comment='项目描述')
    contact_person = Column(String(50), nullable=True, comment='对接人')
    contact_person_id = Column(String(20), nullable=True, comment='对接人ID')
    project_contact = Column(String(50), nullable=True, comment='对接人')
    status = Column(String(20), nullable=False, default='active', comment='状态')
    expected_trend = Column(String(20), nullable=True, comment='预计走向')
    issues = Column(Integer, nullable=False, default=0, comment='问题数')
    risks = Column(Integer, nullable=False, default=0, comment='风险数')
    personnel_plan = Column(String(50), nullable=True, comment='人员计划')
    risk_list = Column(Text, nullable=True, comment='风险清单')
    deployment_date = Column(String(20), nullable=True, comment='部署时间')
    deployment_version = Column(String(50), nullable=True, comment='部署版本')
    recent_delivery_date = Column(String(20), nullable=True, comment='近期交付时间')
    recent_delivery_content = Column(Text, nullable=True, comment='近期交付内容')
    final_delivery_date = Column(String(20), nullable=True, comment='最终交付时间')
    project_summary = Column(Text, nullable=True, comment='项目总结')
    task_execution_status = Column(String(50), nullable=True, comment='任务执行情况')
    field_links = Column(Text, nullable=True, comment='字段链接(JSON格式)')
    category_basis = Column(String(20), nullable=False, default='重要紧急', comment='分类依据')

    project_type = Column(String(20), nullable=True, comment='项目类型（企业微信项目类型字段原值）')
    stage_notes = Column(Text, nullable=True, comment='生命周期各阶段补充说明(JSON格式，键为阶段名)')
    risk_carrying_type = Column(String(20), nullable=True, comment='风险承接类型')
    special_attention = Column(Text, nullable=True, comment='特别关注说明')
    risk_task_description = Column(Text, nullable=True, comment='风险和任务描述')
    management_strategy = Column(Text, nullable=True, comment='项目管理策略')
    project_documents = Column(Text, nullable=True, comment='项目文档(JSON格式，[{name,resource_id,url}])')
    sales = Column(String(50), nullable=True, comment='销售')
    pre_sales = Column(String(50), nullable=True, comment='售前')
    project_manager = Column(String(50), nullable=True, comment='项目经理')
    field_engineer = Column(String(50), nullable=True, comment='实施工程师')

    internal_code = Column(String(50), nullable=True, comment='内部编号')
    project_region = Column(String(30), nullable=True, comment='项目区域/地点')
    total_vehicle_count = Column(Integer, nullable=True, comment='总车数')
    controller_vendor = Column(String(30), nullable=True, comment='控制器选择')
    system_integration = Column(Text, nullable=True, comment='系统/外设对接(JSON数组)')
    server_deployment_status = Column(String(30), nullable=True, comment='服务器部署')
    settlement_period = Column(String(20), nullable=True, comment='业绩核算期，格式YYYY-MM')

    __table_args__ = (
        Index('idx_project_code', 'code', unique=True),
        Index('idx_project_status', 'status'),
        Index('idx_project_settlement_period', 'settlement_period'),
    )

    def __repr__(self):
        return f"<Project(code='{self.code}', name='{self.name}')>"


class Risk(Base):
    __tablename__ = 'risk'

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_code = Column(String(50), nullable=False, unique=True, comment='风险代码')
    project_code = Column(String(50), nullable=False, comment='项目代码')
    project_name = Column(String(100), nullable=False, comment='项目名称')
    risk_category = Column(String(50), nullable=False, comment='风险分类')
    custom_category = Column(String(50), nullable=True, comment='自定义分类')
    description = Column(String(1000), nullable=False, comment='风险描述')
    risk_level = Column(String(20), nullable=False, comment='风险等级')
    response_measure = Column(String(1000), nullable=True, comment='应对措施')
    progress = Column(String(100), nullable=True, comment='进度')
    responsible_person = Column(String(50), nullable=False, comment='负责人')
    responsible_person_id = Column(String(20), nullable=False, comment='负责人ID')
    status = Column(String(20), nullable=False, comment='状态')
    discovery_time = Column(String(20), nullable=False, comment='发现时间')
    close_time = Column(String(30), nullable=True, comment='关闭时间')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=False, comment='更新时间')

    __table_args__ = (
        Index('idx_risk_project', 'project_code', 'project_name'),
        Index('idx_risk_status', 'status'),
        Index('idx_risk_discovery_time', 'discovery_time'),
    )

    def __repr__(self):
        return f"<Risk(id={self.id}, project_code='{self.project_code}', description='{self.description[:20]}...')>"


class ProjectDailyReport(Base):
    __tablename__ = 'project_daily_report'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='日报日期')
    report_content = Column(String(10000), nullable=False, comment='日报内容(JSON格式)')
    reporter = Column(String(50), nullable=False, comment='报告人')
    reporter_id = Column(String(20), nullable=False, comment='报告人ID')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=True, comment='更新时间')

    __table_args__ = (
        Index('idx_report_project_code', 'project_code'),
        Index('idx_report_date', 'report_date'),
        Index('idx_report_unique', 'project_code', 'report_date', unique=True),
    )

    def __repr__(self):
        return f"<ProjectDailyReport(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}')>"


class ProjectLicense(Base):
    __tablename__ = 'project_license'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    machine_code = Column(String(200), nullable=True, comment='机器码/MAC地址')
    apply_time = Column(String(30), nullable=False, comment='申请时间')
    expire_time = Column(String(30), nullable=False, comment='过期时间')
    license_code = Column(Text, nullable=False, comment='授权码')
    applicant = Column(String(50), nullable=False, comment='申请人')
    applicant_id = Column(String(20), nullable=False, comment='申请人ID')
    max_vehicles = Column(Integer, nullable=True, comment='允许最大车数，为空表示不限制')
    created_at = Column(String(30), nullable=False, comment='创建时间')

    __table_args__ = (
        Index('idx_license_project_code', 'project_code'),
        Index('idx_license_apply_time', 'apply_time'),
        Index('idx_license_expire_time', 'expire_time'),
    )

    def __repr__(self):
        return f"<ProjectLicense(id={self.id}, project_code='{self.project_code}', license_code='{self.license_code}')>"


class ProjectTransportEfficiency(Base):
    __tablename__ = 'project_transport_efficiency'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='数据日期')
    total_tasks = Column(Integer, nullable=True, comment='总任务数')
    carry_task_count = Column(Integer, nullable=True, comment='搬运任务数量')
    effective_work_hours = Column(Float, nullable=True, comment='有效工作时长(小时)')
    fault_hours = Column(Float, nullable=True, comment='机器人故障时长(小时)')
    idle_hours = Column(Float, nullable=True, comment='空闲无任务时间(小时)')
    avg_error_count = Column(Float, nullable=True, comment='平均错误次数')
    avg_fault_duration_minutes = Column(Float, nullable=True, comment='平均单次故障时间(分钟)')
    avg_carry_duration_minutes = Column(Float, nullable=True, comment='平均单次搬运任务时间(分钟)')
    avg_manual_switch_count = Column(Float, nullable=True, comment='平均切手动次数')
    manual_intervention_rate = Column(Float, nullable=True, comment='人工干预率(0-1小数)')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=True, comment='更新时间')

    __table_args__ = (
        Index('idx_te_project_date', 'project_code', 'report_date', unique=True),
    )

    def __repr__(self):
        return f"<ProjectTransportEfficiency(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}')>"


class ProjectTransportEfficiencyRobot(Base):
    __tablename__ = 'project_transport_efficiency_robot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='数据日期')
    robot_model = Column(String(50), nullable=False, comment='AGV机器人型号')
    carry_task_total = Column(Integer, nullable=True, comment='搬运任务总数(个)')
    effective_work_hours = Column(Float, nullable=True, comment='有效工作时长(h)')
    effective_efficiency = Column(Float, nullable=True, comment='有效搬运效率(小时/个)')
    fault_hours = Column(Float, nullable=True, comment='机器人故障时间(小时)')
    idle_hours = Column(Float, nullable=True, comment='无工作时间(小时)')
    avg_fault_duration_minutes = Column(Float, nullable=True, comment='平均单次故障(分钟)')
    avg_carry_duration_minutes = Column(Float, nullable=True, comment='平均单次搬运时间(分钟)')
    created_at = Column(String(30), nullable=False, comment='创建时间')

    __table_args__ = (
        Index('idx_ter_project_date_model', 'project_code', 'report_date', 'robot_model', unique=True),
    )

    def __repr__(self):
        return f"<ProjectTransportEfficiencyRobot(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}', robot_model='{self.robot_model}')>"
