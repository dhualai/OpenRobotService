"""会话持久化 — MySQL conversations / messages 表

对接后端已有的两张表：
  - conversations: 会话元数据（id, title, user_id, scene_type, service_ticket_id）
  - messages:     消息记录（id, conversation_id, role, content, sequence）

写入时机：memory.add_turn() 调用时同步写入 MySQL（双写）。
读取优先级：Redis → MySQL 降级。

session_id 存储在 conversations.service_ticket_id 中（AI 会话无工单时复用此字段）。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ai.core.logging import get_logger

_log = get_logger(__name__)


def _get_session() -> "Session":
    from ai.core.database import SessionLocal
    return SessionLocal()


def _now():
    return datetime.now()


# ── 公开 API ────────────────────────────────────────────────────────

def ensure_conversation(
    session_id: str,
    title: str = "",
    user_id: str = "",
    scene_type: str = "chat",
) -> int:
    """确保 session_id 对应的 conversations 行存在，返回 conversation_id。

    已存在则更新 updated_at，不存在则创建。
    """
    from ai.core.database import Conversation
    db = _get_session()
    try:
        row = db.query(Conversation).filter(
            Conversation.service_ticket_id == session_id
        ).first()
        if row:
            row.updated_at = _now()
            db.commit()
            return row.id

        row = Conversation(
            title=title or "新会话",
            user_id=user_id,
            scene_type=scene_type,
            service_ticket_id=session_id,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def rename_conversation(session_id: str, title: str) -> None:
    """更新会话标题。

    AI 在第2轮生成标题后调用，同步到 DB conversations.title，供会话列表 / 切回读取，
    保证标题在「当前显示 / 列表 / 刷新后」三处一致。
    """
    if not title:
        return
    from ai.core.database import Conversation
    db = _get_session()
    try:
        row = db.query(Conversation).filter(
            Conversation.service_ticket_id == session_id
        ).first()
        if row:
            row.title = title
            row.updated_at = _now()
            db.commit()
    finally:
        db.close()


def get_conversation_title(session_id: str) -> str:
    """读取会话当前 DB 标题。用于 AI 生成标题前判断用户是否已手动重命名（避免覆盖）。"""
    from ai.core.database import Conversation
    db = _get_session()
    try:
        row = db.query(Conversation).filter(
            Conversation.service_ticket_id == session_id
        ).first()
        return row.title if row else ""
    finally:
        db.close()


def save_message(
    session_id: str,
    role: str,        # "user" / "assistant"
    content: str,
    user_id: str = "",
    message_type: str = "text",
    file_urls: Optional[List[str]] = None,
) -> Optional[int]:
    """保存一条消息到 messages 表。返回 message id，失败返回 None。"""
    from ai.core.database import Message
    conv_id = ensure_conversation(session_id, user_id=user_id)
    if not conv_id:
        return None

    db = _get_session()
    try:
        # 自动推断序号
        last_seq = db.query(Message.sequence).filter(
            Message.conversation_id == conv_id
        ).order_by(Message.sequence.desc()).first()
        seq = (last_seq[0] + 1) if last_seq else 1

        msg = Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            message_type=message_type,
            file_urls=json.dumps(file_urls, ensure_ascii=False) if file_urls else None,
            sequence=seq,
            created_at=_now(),
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg.id
    except Exception as e:
        _log.error(f"save_message 失败: session_id={session_id}, role={role}, error={e}", exc_info=True)
        db.rollback()
        return None
    finally:
        db.close()


def get_history(session_id: str) -> List[dict]:
    """从 MySQL 加载指定会话的完整消息历史。

    Returns:
        [{"role": "user", "content": "...", "created_at": "..."}, ...]
    """
    from ai.core.database import Conversation, Message
    db = _get_session()
    try:
        conv = db.query(Conversation).filter(
            Conversation.service_ticket_id == session_id
        ).first()
        if not conv:
            return []

        rows = db.query(Message).filter(
            Message.conversation_id == conv.id
        ).order_by(Message.sequence.asc()).all()

        return [
            {
                "role": r.role,
                "content": r.content,
                "message_type": r.message_type,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        db.close()


def list_sessions(user_id: str = "", limit: int = 50) -> List[dict]:
    """列出会话列表（侧边栏用）。

    Returns:
        [{"id": 1, "session_id": "xxx", "title": "...", "created_at": "...", "msg_count": 3}, ...]
    """
    from ai.core.database import Conversation, Message
    from sqlalchemy import func as sa_func
    db = _get_session()
    try:
        q = db.query(
            Conversation.id,
            Conversation.service_ticket_id,
            Conversation.title,
            Conversation.scene_type,
            Conversation.created_at,
            sa_func.count(Message.id).label("msg_count"),
        ).outerjoin(Message, Message.conversation_id == Conversation.id)

        if user_id:
            q = q.filter(Conversation.user_id == user_id)

        q = q.group_by(Conversation.id).order_by(
            Conversation.updated_at.desc()
        ).limit(limit)

        return [
            {
                "id": r.id,
                "session_id": r.service_ticket_id,
                "title": r.title,
                "scene_type": r.scene_type,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "msg_count": r.msg_count,
            }
            for r in q.all()
        ]
    finally:
        db.close()
