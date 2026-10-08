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
    ProjectInfoNode,
    ProjectInfoNodeMark,
    ProjectInfoValue,
    ProjectInfoValueHistory,
    ProjectBlockingConfig,
)

# 任务（承工单语义升格）
from app.models.task import (
    Task,
    TaskComment,
    TaskStatus,
    TaskPriority,
    TaskType,
    TaskUserMapping,
    TaskStep,
    TaskRelation,
    RelationType,
    SystemConfig,
)

# 任务派单日志（二次派单感知增强）
from app.models.task_dispatch_log import TaskDispatchLog

# 代他人提单（代理提单）关系
from app.models.task_proxy_relation import (
    TaskProxyRelation,
    ProxyRelationStatus,
    ProxyRelationSource,
)

# 会话 / 消息
from app.models.conversation import (
    Conversation,
    Message,
    SceneType,
    MessageRole,
    MessageType,
)

# 数据助手会话 / 消息（独立于摇人 conversations/messages）
from app.models.dataqa import DataqaConversation, DataqaMessage

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
from app.models.module_tree_edit import ModuleTreeEdit
from app.models.module_tree_node import ModuleTreeNode

# 界面图鉴（标准截图 + 难懂区域标注）
from app.models.ui_atlas import UiAtlasCard

# 用户信息（JSON 快照）
from app.models.user_info import UserInfo

# 用户统计（按日期 + 来源）
from app.models.user_statistics import UserStatistics

# 微信公众号带参数二维码
from app.models.wechat_qrcode import WechatQrcode, QrcodeStatus, QrcodeType

# 可达 USP 内网环境
from app.models.usp_env import UspEnv

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
    "ProjectInfoNode",
    "ProjectInfoNodeMark",
    "ProjectInfoValue",
    "ProjectInfoValueHistory",
    "ProjectBlockingConfig",
    # task
    "Task", "TaskComment", "TaskStatus", "TaskPriority", "TaskType", "TaskUserMapping", "TaskStep",
    "TaskRelation", "RelationType", "SystemConfig",
    # task dispatch log
    "TaskDispatchLog",
    # task proxy relation
    "TaskProxyRelation", "ProxyRelationStatus", "ProxyRelationSource",
    # conversation
    "Conversation", "Message", "SceneType", "MessageRole", "MessageType",
    # dataqa
    "DataqaConversation", "DataqaMessage",
    # ticket (AI)
    "Ticket",
    # resource
    "Resource", "ResourceFolder", "ResourceType", "ResourceStatus", "StorageType",
    # organization
    "Company", "Department",
    # module tree
    "ModuleTree", "ModuleTreeEdit", "ModuleTreeNode",
    # ui atlas
    "UiAtlasCard",
    # user info
    "UserInfo",
    # user statistics
    "UserStatistics",
    # wechat qrcode
    "WechatQrcode", "QrcodeStatus", "QrcodeType",
    # usp env
    "UspEnv",
]