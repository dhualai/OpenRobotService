"""tasks 模块 WebSocket —— 评论区实时订阅（轻量 IM 模式）。

端点：GET /api/tasks/{task_id}/ws?token=<JWT>
鉴权：token 走 query 参数（浏览器原生 WebSocket 不支持自定义 Header），全程走 wss。
房间模型：按 task_id 分房间，维护连接集合 / 在线成员 / typing 态。
广播事件：comment.created / comment.updated / comment.deleted / presence / typing /
         read_receipt / task.updated / pong。

本模块导出若干 `ws_broadcast_*` 供 REST 评论/状态接口在写库成功后调用，
实现「写走 REST、变更实时推送」的发布-订阅模式。

已读回执相关改造（已读名单更新不及时根因治理）：
- P1 上报逻辑下沉到 `app/modules/tasks/read_receipt.py`（批量幂等 upsert + 分块失败隔离
  + 评论归属校验），本文件只负责收帧与广播；
- P2 同步 ORM 一律经 `run_in_threadpool` 移出事件循环，避免大批量上报阻塞整个进程；
- P2 跨进程广播由 `RoomHub`（Redis pub/sub）承载，Redis 不可用时自动降级为单进程。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_user_with_roles
from app.core.db import SessionLocal
from app.core.security import decode_token
from app.modules.tasks.read_receipt import (
    READ_RECORDS_SNAPSHOT_LIMIT,
    read_cursor_map,
    read_records_map,
    report_read_sync,
)
from app.modules.tasks.schemas.ticket import TicketCommentResponse
from app.modules.tasks.ws_hub import RoomHub

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CONN_PER_USER = 5


def _build_redis_url() -> Optional[str]:
    """跨进程广播用的 Redis 地址；可用环境变量关闭（关闭后纯本地广播）。"""
    flag = os.getenv("WS_HUB_REDIS_ENABLED", "true").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None
    return f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"


hub = RoomHub(redis_url=_build_redis_url())


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
    """房间连接集合委托给 RoomHub（支持跨进程），typing 态仍留在进程内。"""

    def __init__(self, room_hub: RoomHub) -> None:
        self.hub = room_hub
        self.typing: Dict[int, Set[str]] = {}

    async def connect(self, task_id: int, conn: WsConnection) -> None:
        self.hub.add(task_id, conn)
        await self._broadcast_presence(task_id)

    async def disconnect(self, task_id: int, conn: WsConnection) -> None:
        self.hub.discard(task_id, conn)
        if not self.hub.members(task_id):
            self.typing.pop(task_id, None)
        await self._broadcast_presence(task_id)

    def online_members(self, task_id: int) -> list[dict]:
        """按 username 去重（多客户端同用户算一人），返回 [{username, name, avatar_resource_id}]。"""
        seen: set[str] = set()
        result: list[dict] = []
        for c in self.hub.members(task_id):
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
        await self.hub.broadcast(task_id, payload)

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


manager = ConnectionManager(hub)


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
        #    同步 ORM 走线程池，避免大工单快照查询阻塞事件循环
        try:
            read_map = await run_in_threadpool(read_cursor_map, db, task_id)
        except Exception:  # noqa: BLE001
            read_map = {}
        try:
            read_records = await run_in_threadpool(
                read_records_map, db, task_id, None, READ_RECORDS_SNAPSHOT_LIMIT
            )
        except Exception:  # noqa: BLE001
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
                # 已读上报：明细（飞书式名单）+ 游标（兼容旧前端人数统计）都维护。
                # 写入逻辑在 read_receipt 中：批量幂等 upsert + 分块失败隔离 + 评论归属校验。
                try:
                    result = await run_in_threadpool(
                        report_read_sync,
                        db,
                        task_id,
                        msg.get("comment_ids"),
                        username,
                        name,
                        avatar_resource_id,
                        msg.get("last_read_comment_id"),
                    )
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    logger.error(f"已读回执写入失败 task_id={task_id}: {exc}")
                    result = None
                if result is not None:
                    # 广播：游标回执（兼容旧前端）+ 名单增量（新前端名单弹层）
                    await manager.broadcast(task_id, {
                        "type": "read_receipt",
                        "username": username,
                        "last_read_comment_id": result["last_read_comment_id"],
                        "comment_ids": result["comment_ids"],
                        "records": result["records"],
                    })
            # 其他类型忽略
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"WS 连接异常 task_id={task_id} user={username}: {e}")
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
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


async def ws_broadcast_read_receipt(
    task_id: int,
    username: str,
    records: list[dict],
    comment_ids: Optional[list[int]] = None,
    last_read_comment_id: Optional[int] = None,
) -> None:
    """广播已读回执（REST 兜底通道上报成功时调用，让在线成员实时看到名单变化）。"""
    await manager.broadcast(task_id, {
        "type": "read_receipt",
        "username": username,
        "last_read_comment_id": last_read_comment_id,
        "comment_ids": comment_ids or [],
        "records": records,
    })


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
