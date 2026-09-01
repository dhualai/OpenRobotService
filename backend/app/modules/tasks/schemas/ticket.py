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
    # 用于操作日志识别，不入库
    operation_type: Optional[str] = Field(None, description="操作类型：escalate/return/reassign/update")


class TicketCommentBase(BaseModel):
    content: str = Field(..., description="评论内容")
    is_public: bool = Field(default=True, description="是否公开")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")
    reply_to: Optional[int] = Field(None, description="引用的评论ID（消息引用/回复）")


class TicketCommentCreate(TicketCommentBase):
    pass


class TicketCommentUpdate(BaseModel):
    content: Optional[str] = Field(None, description="评论内容")
    is_public: Optional[bool] = Field(None, description="是否公开")
    attachments: Optional[List[AttachmentItem]] = Field(None, description="附件列表")


class QuotedComment(BaseModel):
    id: int
    content: str
    created_by_name: Optional[str] = None


class TicketCommentResponse(TicketCommentBase):
    id: int
    ticket_id: int
    created_by: str
    created_by_name: Optional[str] = None
    created_by_avatar_resource_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    quoted: Optional[QuotedComment] = Field(None, description="被引用评论的简要信息")

    class Config:
        from_attributes = True


class RedispatchCandidate(BaseModel):
    """R2 重派弹窗候选（精排 Top10 快照）"""
    rank: int = Field(..., description="按权重顺序的排名")
    engineer_id: str = Field(..., description="工程师 users.id")
    name: str = Field(..., description="工程师姓名")
    department: Optional[str] = Field(None, description="部门")
    job_level: Optional[int] = Field(None, description="职级")
    modules: Optional[List[str]] = Field(None, description="责任模块")
    duty: Optional[str] = Field(None, description="职责一句话")
    # 画像缺失英文字段（department/job_level/responsibility_modules），供前端权威判定"待补充画像"
    missing: Optional[List[str]] = Field(None, description="缺失画像字段（全空数组=画像完整）")
    scores: Optional[Dict[str, float]] = Field(None, description="各维度分 {llm,semantic,history,total}")
    tags: Optional[List[str]] = Field(None, description="标记如 项目对接人/上次倾向")


class RedispatchProfile(BaseModel):
    """被派人画像 + 完整性"""
    dept: Optional[str] = None
    job_level: Optional[int] = None
    modules: Optional[List[str]] = None
    duty: Optional[str] = None
    missing: Optional[List[str]] = Field(None, description="缺失画像字段（缺则为空数组）")


class RedispatchResult(BaseModel):
    """R3 派单结果信息（结果卡片/提醒数据源）"""
    assigned_id: str = Field(..., description="实际接单人 users.id")
    assigned_name: Optional[str] = Field(None, description="实际接单人姓名")
    preferred_id: Optional[str] = Field(None, description="意向处理人 users.id（首次派单可为 None）")
    preferred_name: Optional[str] = Field(None, description="意向处理人姓名")
    confidence: Optional[float] = Field(None, description="置信度（拼音命中略降 0.85）")
    decision_type: Optional[str] = Field(None, description="auto/recommend/fallback")
    reasoning: Optional[str] = Field(None, description="派单理由")
    profile: Optional[RedispatchProfile] = Field(None, description="被派人画像+缺失字段（R4 补画像用）")
    matched_pref: Optional[bool] = Field(None, description="是否派到意向人")
    name_collision: Optional[bool] = Field(None, description="是否同名命中（同名提醒）")
    pinyin_match: Optional[bool] = Field(None, description="是否拼音近似名命中（近似名提醒）")
    tip_detail: Optional[str] = Field(None, description="未派到指定人时的完整情商话术（含换人理由与重新派单引导，仅 matched_pref=false 有）")


class TicketRedispatch(BaseModel):
    """详情接口 redispatch 子对象（最新一轮派单完整评估）"""
    dispatch_round: int = Field(..., description="派单轮次")
    candidates: Optional[List[RedispatchCandidate]] = Field(None, description="R2 本轮精排 Top10 快照")
    result: Optional[RedispatchResult] = Field(None, description="R3 本轮派单结果信息")


class TicketResponse(TicketBase):
    id: int
    status: TicketStatus
    redispatch: Optional[TicketRedispatch] = Field(None, description="最新一轮派单评估（无记录为 None）")
    created_by: str
    created_by_name: Optional[str] = None
    assigned_to: Optional[str]
    assigned_to_name: Optional[str] = None
    reporter_name: Optional[str] = None
    assignee_name: Optional[str] = None
    customer: Optional[str]
    customer_name: Optional[str] = None
    team: Optional[str]
    project_name: Optional[str]
    created_at: datetime
    updated_at: datetime
    resolved_at: Optional[datetime]
    closed_at: Optional[datetime]
    deadline_at: Optional[datetime]
    # --- 协商阶段（工单阶段性处理：当前节点 + 节点结束时间）---
    curr_step_id: Optional[int] = Field(None, description="当前协商节点ID")
    curr_step_name: Optional[str] = Field(None, description="当前协商节点名称")
    curr_step_endtime: Optional[datetime] = Field(None, description="当前协商节点结束时间（naive UTC）")
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
    curr_step_id: Optional[int] = Field(None, description="当前协商节点ID")
    curr_step_name: Optional[str] = Field(None, description="当前协商节点名称")
    curr_step_endtime: Optional[datetime] = Field(None, description="当前协商节点结束时间（naive UTC）")
    reply_count: int
    view_count: int
    redispatch_tip: Optional[str] = Field(None, description="派单结果提醒一句话摘要（无提醒为 None，见 §3.6）")

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


class TicketCreateNotificationRequest(BaseModel):
    """新建工单通知请求（内部接口，供 AI 派单服务调用）。
    调用方只传 task_id，后端查库组装标题/项目/截止时间/受理人等完整字段后发通知。"""
    task_id: int = Field(..., description="工单ID")


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


class ProjectMemberResponse(BaseModel):
    """项目成员（用于 @ 提及选择），复用 user_project_roles 表数据。"""
    id: str
    username: str
    name: Optional[str] = None
    role_name: Optional[str] = None