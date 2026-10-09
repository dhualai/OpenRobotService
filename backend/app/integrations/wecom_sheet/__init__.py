"""企业微信表格数据源（通用层）。

与 `app/integrations/sources/wecom/`（遗留的「项目表专用」适配器）的区别：
本包不认识任何具体表格，docid / sheet_id 全部来自 `wecom_sheet_source` 表，
新增一张表不需要改本包任何代码。
"""
from app.integrations.wecom_sheet.client import WecomSheetClient, WecomSheetClientError
from app.integrations.wecom_sheet.mirror import run_sync, sync_source

__all__ = ["WecomSheetClient", "WecomSheetClientError", "run_sync", "sync_source"]
