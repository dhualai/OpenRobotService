from pydantic import BaseModel, validator, Field
from typing import List, Optional, Dict, Union
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


class ProjectType(str, Enum):
    WATCHED = "受关注项目"
    KEY_ACCOUNT = "大客户项目"
    EXHIBITION_DEMO = "展会/演示项目"
    SHOWROOM = "展厅项目"
    PK = "PK项目"
    PILOT = "试点项目"
    TRIAL = "试用项目"
    INTERNAL_TEST = "内部/测试项目"
    NORMAL = "普通项目"
    SUPPLEMENT = "增补项目"


class RiskCarryingType(str, Enum):
    DATA_SYNC_ERROR = "数据同步错误"
    REVIEW_REJECTED = "公司评审不通过"
    MISSING_PREREQUISITE = "缺前置承接"
    HIGH_RISK = "高风险承接"
    MEDIUM_RISK = "中风险承接"
    LOW_RISK = "低风险承接"
    PLAN_CHANGE_NOT_CARRIED = "方案变动不承接"
    SCHEDULING_NOT_CARRIED = "调度主动不承接"


class ProjectRegion(str, Enum):
    CHINA_MAINLAND = "大陆(China Mainland)"
    ASIA = "亚洲(Asia)"
    EUROPE = "欧洲(Europe)"
    NORTH_AMERICA = "北美(North America)"
    SOUTH_AMERICA = "南美(South America)"
    OCEANIA = "大洋洲(Oceania)"
    HK_MACAU_TAIWAN = "港澳台"


class ControllerVendor(str, Enum):
    SELF_DEVELOPED = "自研"
    RUIXINHANG = "睿芯行"
    LEKETAI = "利科钛"
    HIKVISION = "海康"
    HUARUI = "华睿"
    ZTE = "中兴"
    KECONG = "科聪"
    YOUGUANG = "有光"
    TEDING = "特定"


class SystemIntegrationType(str, Enum):
    DAS = "DAS"
    CUSTOMER_WMS = "客户WMS"
    CUSTOMER_MES_ERP = "客户MES/ERP"
    CUSTOMER_SYSTEM = "客户系统"
    DIGITAL_TWIN = "数字孪生"
    PDA = "PDA"
    TABLET = "平板"
    ELEVATOR = "电梯"
    CONVEYOR = "输送线/辊筒线"
    AUTO_DOOR = "自动门"
    TRAFFIC_LIGHT = "红绿灯"
    CALLER = "呼叫器"
    ROBOT_ARM = "机械臂"
    OTHER_PERIPHERAL = "其他外设"
    OTHER = "其他"
    PALLETIZER = "码垛机/叠盘机"
    FILM_WRAPPER = "缠膜机"


class ServerDeploymentStatus(str, Enum):
    DEPLOYED_ZHONGLI = "已布-中力服务器"
    DEPLOYING_ZHONGLI = "在布-中力服务器"
    PENDING_ZHONGLI = "待布-中力服务器"
    DEPLOYED_CUSTOMER = "已布-客户服务器"
    PENDING_CUSTOMER = "待布-客户服务器"
    DEPLOYED_CLOUD = "已布-云服务器"
    PENDING_CLOUD = "待布-云服务器"
    DEPLOYED = "已布"
    PENDING = "待布"


class ProjectBase(BaseModel):
    system_id: Optional[str] = None
    project_code: str
    name: str
    description: Optional[str] = None
    contact_person: Optional[str] = None
    contact_person_id: Optional[str] = None
    status: ProjectStatus = ProjectStatus.PRE_SALES_SCHEME
    expected_trend: Optional[ProjectStatus] = None
    issues: int = 0
    risks: int = 0
    personnel_plan: Optional[str] = '无'
    risk_list: Optional[str] = None
    deployment_date: Optional[str] = None
    deployment_version: Optional[str] = None
    recent_delivery_date: Optional[str] = None
    recent_delivery_content: Optional[str] = None
    final_delivery_date: Optional[str] = None
    project_summary: Optional[str] = None
    task_execution_status: Optional[str] = None
    field_links: Optional[Dict[str, str]] = None
    category_basis: ProjectCategory = ProjectCategory.IMPORTANT_URGENT
    project_type: Optional[ProjectType] = None
    stage_notes: Optional[Dict[str, str]] = None
    risk_carrying_type: Optional[RiskCarryingType] = None
    special_attention: Optional[str] = None
    risk_task_description: Optional[str] = None
    management_strategy: Optional[str] = None
    project_documents: Optional[List[Dict[str, str]]] = None
    sales: Optional[str] = None
    pre_sales: Optional[str] = None
    project_manager: Optional[str] = None
    field_engineer: Optional[str] = None
    internal_code: Optional[str] = None
    project_region: Optional[ProjectRegion] = None
    total_vehicle_count: Optional[int] = None
    controller_vendor: Optional[ControllerVendor] = None
    system_integration: Optional[List[SystemIntegrationType]] = None
    server_deployment_status: Optional[ServerDeploymentStatus] = None


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
    project_type: Optional[ProjectType] = None
    stage_notes: Optional[Dict[str, str]] = None
    risk_carrying_type: Optional[RiskCarryingType] = None
    special_attention: Optional[str] = None
    risk_task_description: Optional[str] = None
    management_strategy: Optional[str] = None
    project_documents: Optional[List[Dict[str, str]]] = None
    sales: Optional[str] = None
    pre_sales: Optional[str] = None
    project_manager: Optional[str] = None
    field_engineer: Optional[str] = None
    internal_code: Optional[str] = None
    project_region: Optional[ProjectRegion] = None
    total_vehicle_count: Optional[int] = None
    controller_vendor: Optional[ControllerVendor] = None
    system_integration: Optional[List[SystemIntegrationType]] = None
    server_deployment_status: Optional[ServerDeploymentStatus] = None


class ProjectResponse(ProjectBase):
    id: str
    status: Union[str, ProjectStatus] = ProjectStatus.PRE_SALES_SCHEME
    expected_trend: Optional[Union[str, ProjectStatus]] = None
    category_basis: Union[str, ProjectCategory] = ProjectCategory.IMPORTANT_URGENT
    project_type: Optional[Union[str, ProjectType]] = None
    risk_carrying_type: Optional[Union[str, RiskCarryingType]] = None
    project_region: Optional[Union[str, ProjectRegion]] = None
    controller_vendor: Optional[Union[str, ControllerVendor]] = None
    system_integration: Optional[List[Union[str, SystemIntegrationType]]] = None
    server_deployment_status: Optional[Union[str, ServerDeploymentStatus]] = None
    
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