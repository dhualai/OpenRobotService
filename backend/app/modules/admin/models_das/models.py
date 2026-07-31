"""DAS 交付管理模型——再导出 shim（MIGRATION.md Wave 2.1）。

真实 ORM 定义已迁至 `app/models/delivery.py`。本文件保持 `from app.modules.das.models.models
import Project, Risk, ...` 等旧导入可用。

Wave 2.1 完成：双 Project 合并为单一 `Project` 类（表 `project`）。
"""
from app.models.base import Base
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

__all__ = [
    "Base",
    "RealtimeData",
    "HistoryData",
    "CollectionData",
    "Project",
    "Risk",
    "ProjectDailyReport",
    "ProjectLicense",
    "ProjectTransportEfficiency",
    "ProjectTransportEfficiencyRobot",
]
