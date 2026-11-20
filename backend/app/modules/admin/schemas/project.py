"""再导出 shim（MIGRATION.md 阶段 1）。真实契约已迁至 `app/schemas/project.py`。"""
from app.schemas.project import (
    ProjectBase, ProjectCreate, ProjectUpdate, Project, ProjectUserRoleAssignment,
)

__all__ = ["ProjectBase", "ProjectCreate", "ProjectUpdate", "Project", "ProjectUserRoleAssignment"]
