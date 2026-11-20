"""资源文件夹模型——再导出 shim（MIGRATION.md 阶段 1）。

真实 ORM 定义已迁至 `app/models/resource.py`。保持
`from app.modules.admin.resource_manager.models.resource_folder import ResourceFolder` 旧导入可用。
"""
from app.models.resource import ResourceFolder

__all__ = ["ResourceFolder"]
