"""身份与 RBAC 底座 ORM 模型。

原定义于 `app/core/database.py`，现迁入此处作为唯一定义点（MIGRATION.md 阶段 1）。
`core/database.py` 改为从本模块再导出，`from app.core.database import Project` 等旧路径保持可用。

含 4 张实体表 + 2 张关联表：
- users / roles / permissions / projects
- role_permissions / user_project_roles
"""
from sqlalchemy import Column, String, Text, ForeignKey, Table, Integer
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy import JSON

from app.models.base import Base


role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('id', String(64), primary_key=True),
    Column('role_id', String(64), ForeignKey('roles.id')),
    Column('permission_id', String(64), ForeignKey('permissions.id'))
)

user_project_roles = Table(
    'user_project_roles',
    Base.metadata,
    Column('id', String(64), primary_key=True),
    Column('user_id', String(64), ForeignKey('users.id')),
    Column('project_id', String(64), ForeignKey('project.id'), nullable=True),
    Column('role_id', String(64), ForeignKey('roles.id')),
    Column('report_to_id', String(64), ForeignKey('users.id'), nullable=True)
)


class Role(Base):
    __tablename__ = "roles"
    id = Column(String(64), primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    role_type = Column(String(20), nullable=False, server_default='project', comment="角色类型：system=系统角色，project=项目角色")


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(String(64), primary_key=True)
    code = Column(String(255), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    resource_type = Column(String(64), nullable=False)
    action = Column(String(64), nullable=False)
    enabled = Column(String(8), default="true", nullable=False)


class UserDB(Base):
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(128), nullable=True)
    status = Column(String(32), default="inactive", nullable=False)
    external_credentials = Column(Text, nullable=True)
    avatar_resource_id = Column(Integer, nullable=True)

    # === 派单人信息（与 AI Assigner 共享）===
    # 旧列（存名称字符串，废弃过渡期保留，迁移完成后删除）
    company = Column(String(128), nullable=True, comment="公司名称（废弃，改用 company_id）")
    department = Column(String(128), nullable=True, comment="部门/团队名称（废弃，改用 department_id）")
    # 新列（外键关联主数据表）
    company_id = Column(String(64), ForeignKey('companies.id'), nullable=True, index=True, comment="公司ID")
    department_id = Column(String(64), ForeignKey('departments.id'), nullable=True, index=True, comment="部门ID")
    responsibility_modules = Column(JSON, nullable=True, comment='责任模块 ["车端","任务调度","地图编辑"...]')
    job_level = Column(TINYINT, default=1, nullable=False, comment="职级，数值越高越不优先接单（1=一线, 2=管理/审核, 3=仅兜底...），默认1")
    duty_text = Column(Text, nullable=True, comment="职责画像文本，供 AI 派单匹配参考")
    supervisor_id = Column(String(64), ForeignKey('users.id'), nullable=True, index=True, comment="直属上级用户ID（全局行政汇报线）")

    # === 微信转发绑定 ===
    # 业务账号绑定的微信 open_id（讨论区消息转发到微信公众号客服消息用）。
    # 微信登录用户（username 形如 wechat_xxx）本身 id 即为 open_id，无需绑定；
    # 业务账号（如 zhangsan）需绑定后才能作为转发接收人。
    wechat_openid = Column(String(128), nullable=True, index=True, comment="绑定的微信open_id（讨论区消息转发到微信用）")
