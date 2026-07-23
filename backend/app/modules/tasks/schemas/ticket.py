from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Any, Union
from datetime import datetime

from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType

# 附件可以是字符串（本平台手动上传流程存的是 object_path 字符串），
# 也可以是字典（外部任务源/微信会话写入的 {path, size, filename} 结构）。
# 二者在 tasks/task_comments.attachments(JSON) 列中并存，故统一用联合类型承接。
AttachmentItem = Union[str, Dict[str, Any]]


class TicketBase(BaseModel):
    title: str = Field(..., description="工单标题")
    description: str = Field(..., description="工单描述")
    ticket_type: TicketType = Field(default=TicketType.PROBLEM, description="工单类型")
    priority: TicketPriority = Field(default=TicketPriority.MEDIUM, description="工单优先级")
    customer: Optional[str] = Field(None, description="客户信息")
    team: Optional[str] = Field(None, description="所属团队")
    project_name: Optional[str] = Field(None, description="项目名称")
    project_id: Optional[str] = Field(None, description="项目ID")
    related_resource_id: Optional[int] = Field(None, description="关联资源ID")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    metadata_info: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")
    deadline_at: Optional[datetime] = Field(None, description="截止时间")


class TicketCreate(TicketBase):
    assigned_to: Optional[str] = Field(None, description="处理者ID")


class TicketUpdate(BaseModel):
    title: Optional[str] = Field(None, description="工单标题")
    description: Optional[str] = Field(None, description="工单描述")
    status: Optional[TicketStatus] = Field(None, description="工单状态")
    priority: Optional[TicketPriority] = Field(None, description="工单优先级")
    ticket_type: Optional[TicketType] = Field(None, description="工单类型")
    assigned_to: Optional[str] = Field(None, description="处理者ID")
    customer: Optional[str] = Field(None, description="客户信息")
    team: Optional[str] = Field(None, description="所属团队")
    project_name: Optional[str] = Field(None, description="项目名称")
    project_id: Optional[str] = Field(None, description="项目ID")
    related_resource_id: Optional[int] = Field(None, description="关联资源ID")
    tags: Optional[List[str]] = Field(None, description="标签列表")
    metadata_info: Optional[Dict[str, Any]] = Field(None, description="扩展元数据")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")
    resolved_at: Optional[datetime] = Field(None, description="解决时间")
    deadline_at: Optional[datetime] = Field(None, description="截止时间")


class TicketCommentBase(BaseModel):
    content: str = Field(..., description="评论内容")
    is_public: bool = Field(default=True, description="是否公开")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")


class TicketCommentCreate(TicketCommentBase):
    pass


class TicketCommentUpdate(BaseModel):
    content: Optional[str] = Field(None, description="评论内容")
    is_public: Optional[bool] = Field(None, description="是否公开")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")


class TicketCommentResponse(TicketCommentBase):
    id: int
    ticket_id: int
    created_by: str
    created_by_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TicketResponse(TicketBase):
    id: int
    status: TicketStatus
    created_by: str
    assigned_to: Optional[str]
    customer: Optional[str]
    team: Optional[str]
    project_name: Optional[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime]
    closed_at: Optional[datetime]
    deadline_at: Optional[datetime]
    reply_count: int
    view_count: int
    comments: Optional[List[TicketCommentResponse]] = []

    class Config:
        from_attributes = True


class TicketListItemResponse(TicketBase):
    id: int
    status: TicketStatus
    created_by: str
    created_by_name: Optional[str] = None
    assigned_to: Optional[str]
    assigned_to_name: Optional[str] = None
    customer: Optional[str]
    customer_name: Optional[str] = None
    team: Optional[str]
    project_name: Optional[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime]
    closed_at: Optional[datetime]
    deadline_at: Optional[datetime]
    reply_count: int
    view_count: int

    class Config:
        from_attributes = True


class TicketListResponse(BaseModel):
    items: List[TicketListItemResponse]
    total: int
    page: int
    size: int
    pages: int


class TicketQueryParams(BaseModel):
    page: int = Field(default=1, ge=1, description="页码")
    size: int = Field(default=10, ge=1, le=100, description="每页数量")
    id: Optional[int] = Field(None, description="工单ID")
    id_op: Optional[str] = Field(None, description="工单ID过滤操作：equals|gt|gte|lt|lte|ne")
    title: Optional[str] = Field(None, description="工单标题")
    title_op: Optional[str] = Field(None, description="标题过滤操作：equals|contains|notEquals")
    status: Optional[str] = Field(None, description="工单状态，支持多个状态用逗号分隔")
    priority: Optional[TicketPriority] = Field(None, description="工单优先级")
    ticket_type: Optional[TicketType] = Field(None, description="工单类型")
    created_by: Optional[str] = Field(None, description="创建者ID")
    created_by_op: Optional[str] = Field(None, description="创建者过滤操作")
    created_by_name: Optional[str] = Field(None, description="创建者姓名")
    assigned_to: Optional[str] = Field(None, description="处理者ID")
    assigned_to_op: Optional[str] = Field(None, description="处理者过滤操作")
    assigned_to_name: Optional[str] = Field(None, description="处理者姓名")
    customer: Optional[str] = Field(None, description="客户信息")
    customer_op: Optional[str] = Field(None, description="客户过滤操作")
    customer_name: Optional[str] = Field(None, description="客户姓名")
    keyword: Optional[str] = Field(None, description="关键词搜索")
    related_resource_id: Optional[int] = Field(None, description="关联资源ID")
    related_resource_id_op: Optional[str] = Field(None, description="关联资源ID过滤操作")
    tag: Optional[str] = Field(None, description="标签过滤")
    project_name: Optional[str] = Field(None, description="项目名称")
    project_name_op: Optional[str] = Field(None, description="项目名称过滤操作")
    project_id: Optional[str] = Field(None, description="项目ID")
    project_id_op: Optional[str] = Field(None, description="项目ID过滤操作")
    source: Optional[str] = Field(None, description="任务来源：manual/zentao/...")
    source_op: Optional[str] = Field(None, description="来源过滤操作")
    deadline_at: Optional[datetime] = Field(None, description="截止时间")
    created_at_start: Optional[datetime] = Field(None, description="创建时间起始")
    created_at_end: Optional[datetime] = Field(None, description="创建时间结束")
    updated_at_start: Optional[datetime] = Field(None, description="更新时间起始")
    updated_at_end: Optional[datetime] = Field(None, description="更新时间结束")
    resolved_at_start: Optional[datetime] = Field(None, description="解决时间起始")
    resolved_at_end: Optional[datetime] = Field(None, description="解决时间结束")
    closed_at_start: Optional[datetime] = Field(None, description="关闭时间起始")
    closed_at_end: Optional[datetime] = Field(None, description="关闭时间结束")
    deadline_at_start: Optional[datetime] = Field(None, description="截止时间起始")
    deadline_at_end: Optional[datetime] = Field(None, description="截止时间结束")


class TicketCuibanNotification(BaseModel):
    ticket_id: Optional[int] = Field(None, description="工单ID")
    notify_type: int = Field(..., description="通知类型")
    assigned_to: Optional[str] = Field(None, description="处理者ID")
    to_admin: Optional[bool] = Field(False, description="是否通知管理员")


class TicketFilter(BaseModel):
    field: Optional[str] = Field(None, description="字段名")
    op: Optional[str] = Field(None, description="运算符")
    value: Optional[Any] = Field(None, description="过滤值")
    or_conditions: Optional[List['TicketFilter']] = Field(None, alias='or', description="OR条件列表")
    and_conditions: Optional[List['TicketFilter']] = Field(None, alias='and', description="AND条件列表")

    class Config:
        populate_by_name = True


class TicketSort(BaseModel):
    field: str = Field(..., description="排序字段")
    direction: str = Field(..., description="排序方向：asc 或 desc")


class TicketFilterRequest(BaseModel):
    filters: List[TicketFilter] = Field(default_factory=list, description="过滤条件列表")
    sorts: Optional[List[TicketSort]] = Field(default_factory=list, description="排序条件列表")
    page: int = Field(default=1, ge=1, description="页码")
    size: int = Field(default=10, ge=1, le=100, description="每页数量")