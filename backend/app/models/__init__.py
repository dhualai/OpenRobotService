"""统一 ORM 导入面（MIGRATION.md Wave 2.1）。

全项目 19 张表的 ORM 定义收敛于 `app/models/` 各子模块；本文件汇总再导出，
提供唯一导入入口：`from app.models import Base, UserDB, Project, Task, ...`。

`env.py`（Alembic）导入本包即触发全部模型注册到 `Base.metadata`。

Wave 2.1 完成：双 Project 合并为单一 `Project` 类（表 `project`，String 主键 `code`）。
"""
from app.models.base import Base

# 身份 / RBAC 底座
from app.models.identity import (
    UserDB,
    Role,
    Permission,
    role_permissions,
    user_project_roles,
)

# DAS 交付管理（含统一 Project 模型）
from app.models.delivery import (
    RealtimeData,
    HistoryData,
    CollectionData,
    Project,
    Risk,
    ProjectDailyReport,
    ProjectLicense,
    ProjectTransportEfficiency,
    ProjectTransportEfficiencyRobot,
)

# 任务（承工单语义升格）
from app.models.task import (
    Task,
    TaskComment,
    TaskStatus,
    TaskPriority,
    TaskType,
    TaskUserMapping,
)

# 会话 / 消息
from app.models.conversation import (
    Conversation,
    Message,
    SceneType,
    MessageRole,
    MessageType,
)

# 工单（AI 诊断生成，独立于 Task）
from app.models.ticket import Ticket

# 资源 / 文件夹
from app.models.resource import (
    Resource,
    ResourceFolder,
    ResourceType,
    ResourceStatus,
    StorageType,
)

# 组织主数据（公司/部门，含审核流程）
from app.models.organization import (
    Company,
    Department,
)

# 「产品→界面→功能」责任模块树（DB 主数据，导出到 config 供 AI Assigner）
from app.models.module_tree import ModuleTree

__all__ = [
    "Base",
    # identity
    "UserDB", "Role", "Permission",
    "role_permissions", "user_project_roles",
    # delivery (含统一 Project)
    "Project",
    "RealtimeData", "HistoryData", "CollectionData",
    "Risk", "ProjectDailyReport", "ProjectLicense",
    "ProjectTransportEfficiency", "ProjectTransportEfficiencyRobot",
    # task
    "Task", "TaskComment", "TaskStatus", "TaskPriority", "TaskType", "TaskUserMapping",
    # conversation
    "Conversation", "Message", "SceneType", "MessageRole", "MessageType",
    # ticket (AI)
    "Ticket",
    # resource
    "Resource", "ResourceFolder", "ResourceType", "ResourceStatus", "StorageType",
    # organization
    "Company", "Department",
    # module tree
    "ModuleTree",
]