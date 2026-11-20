"""项目契约（Pydantic）。原 `app/modules/aas/schemas/project.py`，迁入底座（MIGRATION.md 阶段 1）。"""
from typing import Optional, List, Dict
from pydantic import BaseModel

class ProjectBase(BaseModel):
    name: str

class ProjectCreate(ProjectBase):
    name: Optional[str] = None
    code: str

class ProjectUpdate(ProjectBase):
    pass

class Project(ProjectBase):
    id: str

    class Config:
        from_attributes = True

class ProjectUserRoleAssignment(BaseModel):
    project_id: str
    organization_ids: List[Dict[str, str]]
    # 可选：是否发送"账号授权通过提醒"微信模板消息（给每个被授权用户推模板 12）
    # true 时需同时提供 notify_user_names / notify_project_name
    send_notify: Optional[bool] = False
    notify_project_name: Optional[str] = None   # 通知里的项目名称（thing1），优先用前端传的
    notify_project_code: Optional[str] = None   # 项目编码（兜底用）
    notify_ticket_id: Optional[int] = None      # 授权工单 ID，通知 URL 指向工单详情页
