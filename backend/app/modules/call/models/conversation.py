"""会话模型——再导出 shim（MIGRATION.md 阶段 1）。

真实 ORM 定义已迁至 `app/models/conversation.py`。保持
`from app.modules.call.models.conversation import Conversation, SceneType` 等旧导入可用。
"""
from app.models.conversation import Conversation, SceneType

__all__ = ["Conversation", "SceneType"]
