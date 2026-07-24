"""身份与 RBAC 底座 ORM 模型。

原定义于 `app/core/database.py`，现迁入此处作为唯一定义点（MIGRATION.md 阶段 1）。
`core/database.py` 改为从本模块再导出，`from app.core.database import Project` 等旧路径保持可用。

含 4 张实体表 + 2 张关联表：
- users / roles / permissions / projects
- role_permissions / user_project_roles
"""
from sqlalchemy import Column, String, Text, ForeignKey, Table

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
