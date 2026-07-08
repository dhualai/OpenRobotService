from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Index, create_engine
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()


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
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    system_id = Column(String(50), nullable=True, comment='系统ID')
    project_code = Column(String(50), nullable=False, comment='项目代码')
    name = Column(String(100), nullable=False, comment='项目名称')
    description = Column(String(1000), nullable=False, comment='项目描述')
    contact_person = Column(String(50), nullable=False, comment='对接人')
    contact_person_id = Column(String(20), nullable=False, comment='对接人ID')
    status = Column(String(20), nullable=False, comment='状态')
    expected_trend = Column(String(20), nullable=False, comment='预计走向')
    issues = Column(Integer, nullable=False, comment='问题数')
    risks = Column(Integer, nullable=False, comment='风险数')
    personnel_plan = Column(String(50), nullable=False, comment='人员计划')
    risk_list = Column(String(500), nullable=True, comment='风险清单')
    deployment_date = Column(String(20), nullable=False, comment='部署时间')
    deployment_version = Column(String(50), nullable=False, comment='部署版本')
    recent_delivery_date = Column(String(20), nullable=False, comment='近期交付时间')
    recent_delivery_content = Column(String(500), nullable=True, comment='近期交付内容')
    final_delivery_date = Column(String(20), nullable=False, comment='最终交付时间')
    project_summary = Column(String(1000), nullable=True, comment='项目总结')
    task_execution_status = Column(String(50), nullable=True, comment='任务执行情况')
    field_links = Column(String(1000), nullable=True, comment='字段链接(JSON格式)')
    category_basis = Column(String(20), nullable=False, default='重要紧急', comment='分类依据')
    
    __table_args__ = (
        Index('idx_project_code', 'project_code', unique=True),
        Index('idx_project_status', 'status'),
    )
    
    def __repr__(self):
        return f"<Project(id={self.id}, project_code='{self.project_code}', name='{self.name}')>"


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
    apply_time = Column(String(30), nullable=False, comment='申请时间')
    expire_time = Column(String(30), nullable=False, comment='过期时间')
    license_code = Column(String(100), nullable=False, comment='授权码')
    applicant = Column(String(50), nullable=False, comment='申请人')
    applicant_id = Column(String(20), nullable=False, comment='申请人ID')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    
    __table_args__ = (
        Index('idx_license_project_code', 'project_code'),
        Index('idx_license_apply_time', 'apply_time'),
        Index('idx_license_expire_time', 'expire_time'),
    )
    
    def __repr__(self):
        return f"<ProjectLicense(id={self.id}, project_code='{self.project_code}', license_code='{self.license_code}')>"