"""消息模型——再导出 shim（MIGRATION.md 阶段 1）。

真实 ORM 定义已迁至 `app/models/conversation.py`（会话与消息合并一处）。保持
`from app.modules.call.models.message import Message, MessageRole, MessageType` 等旧导入可用。
"""
from app.models.conversation import Message, MessageRole, MessageType

__all__ = ["Message", "MessageRole", "MessageType"]
