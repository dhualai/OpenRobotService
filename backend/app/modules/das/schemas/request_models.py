from pydantic import BaseModel, validator, Field
from typing import List, Optional, Dict
import re
from datetime import datetime
from enum import Enum


class ProjectStatus(str, Enum):
    PRE_SALES_SCHEME = "售前方案"
    CONTRACT_NEGOTIATION = "签单洽谈"
    CONTRACT_SIGNED = "已签合同"
    FACTORY_TEST = "出厂测试"
    PENDING_ENTRY = "即将进场"
    DELAYED_ENTRY = "延期进场"
    IN_IMPLEMENTATION = "正在实施"
    IMPLEMENTATION_SUSPENDED = "实施暂停"
    IMPLEMENTATION_RUNNING = "实施运行"
    IN_TRIAL_OPERATION = "试运行中"
    ACCEPTANCE_OPERATION = "验收运营"
    PROJECT_SUSPENDED = "项目中止"
    PROJECT_ENDED = "项目结束"


class ProjectCategory(str, Enum):
    IMPORTANT_URGENT = "重要紧急"
    URGENT_NOT_IMPORTANT = "紧急不重要"
    IMPORTANT_NOT_URGENT = "重要不紧急"
    NOT_IMPORTANT_NOT_URGENT = "不紧急不重要"


class ExecutionStatus(str, Enum):
    NORMAL = "正常"
    DELAYED = "延迟"
    BLOCKED = "阻塞"


class ProjectBase(BaseModel):
    system_id: Optional[str] = None
    project_code: str
    name: str
    description: str
    contact_person: str
    contact_person_id: str
    status: ProjectStatus
    expected_trend: ProjectStatus
    issues: int
    risks: int
    personnel_plan: Optional[str] = '无'
    risk_list: Optional[str] = None
    deployment_date: str
    deployment_version: str
    recent_delivery_date: str
    recent_delivery_content: Optional[str] = None
    final_delivery_date: str
    project_summary: Optional[str] = None
    task_execution_status: Optional[str] = None
    field_links: Optional[Dict[str, str]] = None
    category_basis: ProjectCategory = ProjectCategory.IMPORTANT_URGENT


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    system_id: Optional[str] = None
    project_code: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    contact_person: Optional[str] = None
    contact_person_id: Optional[str] = None
    status: Optional[ProjectStatus] = None
    expected_trend: Optional[str] = None
    issues: Optional[int] = None
    risks: Optional[int] = None
    personnel_plan: Optional[str] = None
    risk_list: Optional[str] = None
    deployment_date: Optional[str] = None
    deployment_version: Optional[str] = None
    recent_delivery_date: Optional[str] = None
    recent_delivery_content: Optional[str] = None
    final_delivery_date: Optional[str] = None
    project_summary: Optional[str] = None
    task_execution_status: Optional[str] = None
    field_links: Optional[Dict[str, str]] = None
    category_basis: Optional[ProjectCategory] = None


class ProjectResponse(ProjectBase):
    id: int
    
    class Config:
        from_attributes = True


class DataAccessRequest(BaseModel):
    project: str
    tag: str
    indicator: List[str]
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    autoflag: Optional[bool] = False
    
    @validator('start_time', pre=True, always=True)
    def validate_and_complete_start_time(cls, v):
        today = datetime.now().strftime('%Y-%m-%d')
        
        if v is None or v == '':
            return f"{today}T00:00:00"
        
        v_str = str(v)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v_str):
            return f"{v_str}T00:00:00"
        elif re.match(r'^\d{4}-\d{2}-\d{2}', v_str):
            return v
        else:
            return f"{today}T00:00:00"
    
    @validator('end_time', pre=True, always=True)
    def validate_and_complete_end_time(cls, v):
        today = datetime.now().strftime('%Y-%m-%d')
        
        if v is None or v == '':
            return f"{today}T23:59:59"
        
        v_str = str(v)
        if re.match(r'^\d{4}-\d{2}-\d{2}$', v_str):
            return f"{v_str}T23:59:59"
        elif re.match(r'^\d{4}-\d{2}-\d{2}', v_str):
            return v
        else:
            return f"{today}T23:59:59"


class RiskBase(BaseModel):
    risk_code: Optional[str] = None
    project_code: str
    project_name: str
    risk_category: str
    custom_category: Optional[str] = None
    description: str
    risk_level: str
    response_measure: Optional[str] = None
    progress: Optional[str] = None
    responsible_person: str
    responsible_person_id: str
    status: str
    discovery_time: str
    close_time: Optional[str] = None


class RiskCreate(RiskBase):
    pass


class RiskUpdate(BaseModel):
    risk_code: Optional[str] = None
    project_code: Optional[str] = None
    project_name: Optional[str] = None
    risk_category: Optional[str] = None
    custom_category: Optional[str] = None
    description: Optional[str] = None
    risk_level: Optional[str] = None
    response_measure: Optional[str] = None
    progress: Optional[str] = None
    responsible_person: Optional[str] = None
    responsible_person_id: Optional[str] = None
    status: Optional[str] = None
    discovery_time: Optional[str] = None
    close_time: Optional[str] = None


class RiskResponse(RiskBase):
    id: int
    created_at: str
    updated_at: str
    
    class Config:
        from_attributes = True


class RiskFilterOptions(BaseModel):
    project_names: List[str]
    risk_categories: List[str]
    custom_categories: List[str]
    risk_levels: List[str]
    statuses: List[str]


class RiskListResponse(BaseModel):
    total: int
    page: int
    pageSize: int
    risks: List[RiskResponse]


class RiskFilterRequest(BaseModel):
    searchTerm: Optional[str] = None
    projectName: Optional[str] = None
    riskCategory: Optional[str] = None
    customCategory: Optional[str] = None
    riskLevel: Optional[str] = None
    status: Optional[str] = None
    page: int = 1
    pageSize: int = 10
    sortBy: str = "discoveryTime"
    sortOrder: str = "desc"


class TextContent(BaseModel):
    content: str


class AtInfo(BaseModel):
    user_names: List[str]
    is_all: bool


class LinkContent(TextContent):
    url: str
    title: str


class NotifyRequest(BaseModel):
    msg_type: str = Field(..., description="消息类型")
    text: Optional[TextContent] = Field(None, description="文本内容")
    link: Optional[LinkContent] = Field(None, description="链接内容")
    at: Optional[AtInfo] = Field(None, description="@信息")


class NotifyResponse(BaseModel):
    status: str
    message: str


class DailyReportBase(BaseModel):
    project_code: str
    report_date: str
    report_content: Dict
    reporter: str
    reporter_id: str


class DailyReportCreate(DailyReportBase):
    pass


class DailyReportUpdate(BaseModel):
    project_code: Optional[str] = None
    report_date: Optional[str] = None
    report_content: Optional[Dict] = None
    reporter: Optional[str] = None
    reporter_id: Optional[str] = None
    updated_at: Optional[str] = None


class DailyReportResponse(DailyReportBase):
    id: int
    created_at: str
    updated_at: Optional[str] = None
    
    class Config:
        from_attributes = True