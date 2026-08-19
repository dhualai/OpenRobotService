"""tasks 模块 WebSocket —— 评论区实时订阅（轻量 IM 模式）。

端点：GET /api/tasks/{task_id}/ws?token=<JWT>
鉴权：token 走 query 参数（浏览器原生 WebSocket 不支持自定义 Header），全程走 wss。
房间模型：按 task_id 分房间，维护连接集合 / 在线成员 / typing 态。
广播事件：comment.created / comment.updated / comment.deleted / presence / typing /
         read_receipt / task.updated / pong。

本模块导出若干 `ws_broadcast_*` 供 REST 评论/状态接口在写库成功后调用，
实现「写走 REST、变更实时推送」的发布-订阅模式。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Iterable, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.core.database import get_user_with_roles
from app.core.security import decode_token
from app.core.db import SessionLocal
from app.models.task import TaskCommentRead, TaskCommentReadRecord
from app.models.identity import UserDB
from app.modules.tasks.schemas.ticket import TicketCommentResponse

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CONN_PER_USER = 5


class WsConnection:
    __slots__ = ("ws", "username", "name", "avatar_resource_id", "task_id", "last_ping")

    def __init__(self, ws: WebSocket, username: str, name: str, task_id: int, avatar_resource_id: Optional[int] = None) -> None:
        self.ws = ws
        self.username = username
        self.name = name
        self.avatar_resource_id = avatar_resource_id
        self.task_id = task_id
        self.last_ping = time.monotonic()

    async def send(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover - 连接已断
            pass


class ConnectionManager:
    def __init__(self) -> None:
        self.rooms: Dict[int, Set[WsConnection]] = {}
        self.typing: Dict[int, Set[str]] = {}

    async def connect(self, task_id: int, conn: WsConnection) -> None:
        self.rooms.setdefault(task_id, set()).add(conn)
        await self._broadcast_presence(task_id)

    async def disconnect(self, task_id: int, conn: WsConnection) -> None:
        room = self.rooms.get(task_id)
        if room:
            room.discard(conn)
            if not room:
                self.rooms.pop(task_id, None)
                self.typing.pop(task_id, None)
        await self._broadcast_presence(task_id)

    def online_members(self, task_id: int) -> list[dict]:
        """按 username 去重（多客户端同用户算一人），返回 [{username, name, avatar_resource_id}]。"""
        seen: set[str] = set()
        result: list[dict] = []
        for c in self.rooms.get(task_id, set()):
            if c.username in seen:
                continue
            seen.add(c.username)
            result.append({
                "username": c.username,
                "name": c.name,
                "avatar_resource_id": c.avatar_resource_id,
            })
        return result

    async def broadcast(self, task_id: int, payload: dict) -> None:
        for conn in list(self.rooms.get(task_id, set())):
            await conn.send(payload)

    async def _broadcast_presence(self, task_id: int) -> None:
        await self.broadcast(task_id, {"type": "presence", "online": self.online_members(task_id)})

    async def set_typing(self, task_id: int, username: str, value: bool) -> None:
        self.typing.setdefault(task_id, set())
        if value:
            self.typing[task_id].add(username)
        else:
            self.typing[task_id].discard(username)
        # 广播给房间所有人（前端忽略自己的 username）
        await self.broadcast(task_id, {"type": "typing", "username": username, "value": value})


manager = ConnectionManager()


def _upsert_read(db, task_id: int, username: str, comment_id: int) -> None:
    res = db.execute(
        select(TaskCommentRead).where(
            TaskCommentRead.task_id == task_id, TaskCommentRead.username == username
        )
    )
    rec = res.scalar_one_or_none()
    if rec:
        rec.last_read_comment_id = comment_id
        rec.updated_at = func.now()
    else:
        db.add(TaskCommentRead(task_id=task_id, username=username, last_read_comment_id=comment_id))
    db.commit()


def _read_map(db, task_id: int) -> dict:
    res = db.execute(select(TaskCommentRead).where(TaskCommentRead.task_id == task_id))
    return {r.username: r.last_read_comment_id for r in res.scalars().all()}


def _mark_comment_read(db, task_id: int, comment_id: int, username: str) -> bool:
    """幂等写入单条评论的已读明细（飞书式名单）。已存在则跳过，返回是否新增。"""
    res = db.execute(
        select(TaskCommentReadRecord.id).where(
            TaskCommentReadRecord.comment_id == comment_id,
            TaskCommentReadRecord.username == username,
        )
    )
    if res.scalar_one_or_none() is not None:
        return False
    db.add(TaskCommentReadRecord(
        task_id=task_id, comment_id=comment_id, username=username,
    ))
    return True


def _read_records_map(db, task_id: int) -> dict:
    """按 comment_id 分组返回已读名单（含用户名/姓名/头像/阅读时间），阅读时间倒序。

    返回 {str(comment_id): [ {username, name, avatar_resource_id, read_at}, ... ]}。
    """
    rows = db.execute(
        select(TaskCommentReadRecord)
        .where(TaskCommentReadRecord.task_id == task_id)
        .order_by(TaskCommentReadRecord.read_at.desc(), TaskCommentReadRecord.id.desc())
    ).scalars().all()

    usernames = sorted({r.username for r in rows})
    name_map: dict = {}
    avatar_map: dict = {}
    if usernames:
        users = db.execute(
            select(UserDB).where(UserDB.username.in_(usernames))
        ).scalars().all()
        for u in users:
            name_map[u.username] = u.name or u.username
            avatar_map[u.username] = getattr(u, "avatar_resource_id", None)

    result: dict = {}
    for r in rows:
        result.setdefault(str(r.comment_id), []).append({
            "username": r.username,
            "name": name_map.get(r.username, r.username),
            "avatar_resource_id": avatar_map.get(r.username),
            "read_at": r.read_at.isoformat() if r.read_at else None,
        })
    return result


@router.websocket("/{task_id}/ws")
async def ws_task_room(websocket: WebSocket, task_id: int, token: str = Query(None)):
    # 1. 鉴权（query token）
    if not token:
        await websocket.close(code=4401)
        return
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        await websocket.close(code=4401)
        return
    username = payload["sub"]
    user = get_user_with_roles(username)
    if user is None:
        await websocket.close(code=4404)
        return
    name = user.get("name") or username
    avatar_resource_id = user.get("avatar_resource_id")

    # 2. 接受连接 + 加入房间
    await websocket.accept()
    conn = WsConnection(websocket, username, name, task_id, avatar_resource_id)
    await manager.connect(task_id, conn)

    # 单个连接生命周期内复用同一同步 session（与 task.py 既有模式一致）
    db = SessionLocal()
    try:
        # 3. welcome：在线成员 + 各用户已读游标 + 已读名单快照
        try:
            read_map = _read_map(db, task_id)
        except Exception:
            read_map = {}
        try:
            read_records = _read_records_map(db, task_id)
        except Exception:
            read_records = {}
        await conn.send({
            "type": "welcome",
            "you": username,
            "online": manager.online_members(task_id),
            "read_map": read_map,
            "read_records": read_records,
        })
        # 4. 接收客户端帧
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "ping":
                conn.last_ping = time.monotonic()
                await conn.send({"type": "pong"})
            elif mtype == "typing":
                await manager.set_typing(task_id, username, bool(msg.get("value")))
            elif mtype == "read":
                # 兼容两种上报：comment_ids（本次实际读到的评论列表，飞书式名单）
                # 或 last_read_comment_id（游标）。二者都维护，名单与游标互不冲突。
                comment_ids = msg.get("comment_ids")
                if not isinstance(comment_ids, list):
                    comment_ids = []
                cid = msg.get("last_read_comment_id")
                try:
                    new_records: list[dict] = []
                    for _cid in comment_ids:
                        if not isinstance(_cid, int):
                            continue
                        if _mark_comment_read(db, task_id, _cid, username):
                            new_records.append({
                                "comment_id": _cid,
                                "username": username,
                                "name": name,
                                "avatar_resource_id": avatar_resource_id,
                            })
                    if isinstance(cid, int):
                        _upsert_read(db, task_id, username, cid)
                    db.commit()
                    # 广播：游标回执（兼容旧前端人数统计）+ 名单增量（新前端名单弹层）
                    await manager.broadcast(task_id, {
                        "type": "read_receipt",
                        "username": username,
                        "last_read_comment_id": cid if isinstance(cid, int) else None,
                        "comment_ids": comment_ids,
                        "records": new_records,
                    })
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    logger.error(f"已读回执写入失败 task_id={task_id}: {e}")
            # 其他类型忽略
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"WS 连接异常 task_id={task_id} user={username}: {e}")
    finally:
        try:
            db.close()
        except Exception:
            pass
        await manager.disconnect(task_id, conn)


# ─────────────────────────────────────────────────────────────────────────────
# 供 REST 评论/状态接口调用的广播封装（调用方应自行 try/except，失败不影响主流程）
# ─────────────────────────────────────────────────────────────────────────────

def _comment_payload(comment) -> dict:
    """把 ORM 评论序列化为与 TicketCommentResponse 一致的 dict（前端零解析差异）。"""
    return TicketCommentResponse.model_validate(comment).model_dump(mode="json")


async def ws_broadcast_comment(event: str, task_id: int, comment) -> None:
    """event: 'comment.created' | 'comment.updated'"""
    payload = _comment_payload(comment)
    await manager.broadcast(task_id, {"type": event, "comment": payload})


async def ws_broadcast_comment_deleted(task_id: int, comment_id: int) -> None:
    await manager.broadcast(task_id, {"type": "comment.deleted", "id": comment_id})


def _task_updated_payload(obj) -> dict:
    status = getattr(obj, "status", None)
    status = status.value if hasattr(status, "value") else (str(status) if status else None)
    updated_at = getattr(obj, "updated_at", None)
    updated_at = updated_at.isoformat() if hasattr(updated_at, "isoformat") else (str(updated_at) if updated_at else None)
    return {
        "type": "task.updated",
        "task_id": getattr(obj, "id", None),
        "status": status,
        "assigned_to": getattr(obj, "assigned_to", None),
        "assigned_to_name": getattr(obj, "assigned_to_name", None),
        "updated_at": updated_at,
    }


async def ws_broadcast_task_updated(task_id: int, obj) -> None:
    await manager.broadcast(task_id, _task_updated_payload(obj))
