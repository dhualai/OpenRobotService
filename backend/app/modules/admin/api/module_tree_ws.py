"""责任模块树 WebSocket —— 多人维护实时广播。

端点：GET /api/admin/module-tree/ws?token=<JWT>（同 REST 前缀，协议 ws/wss）。
鉴权：token 走 query 参数（浏览器原生 WebSocket 不支持自定义 Header，全程 wss）。
房间模型：全局单房间（所有责任树编辑者共享），维护连接集合 / 在线成员。
广播事件：
  - presence             在线人员列表
  - module_tree.updated  某产品树被他人修改：{ by, product }，前端据此重拉/合并

写 REST 接口在保存成功后调用 `ws_broadcast_module_tree_updated(product, by)`，
实现「写走 REST、变更实时推送」的发布-订阅模式。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Dict, Optional, Set

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.database import get_user_with_roles
from app.core.security import decode_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/module-tree")

MAX_CONN_PER_USER = 5


class ModuleTreeConnection:
    __slots__ = ("ws", "username", "name", "last_ping")

    def __init__(self, ws: WebSocket, username: str, name: str) -> None:
        self.ws = ws
        self.username = username
        self.name = name
        self.last_ping = time.monotonic()

    async def send(self, payload: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:  # pragma: no cover - 连接已断
            pass


class ModuleTreeManager:
    def __init__(self) -> None:
        self.connections: Set[ModuleTreeConnection] = set()

    async def connect(self, conn: ModuleTreeConnection) -> None:
        self.connections.add(conn)
        await self._broadcast_presence()

    async def disconnect(self, conn: ModuleTreeConnection) -> None:
        self.connections.discard(conn)
        await self._broadcast_presence()

    def online_members(self) -> list[dict]:
        """按 username 去重（多客户端同用户算一人），返回 [{username, name}]。"""
        seen: set[str] = set()
        result: list[dict] = []
        for c in self.connections:
            if c.username in seen:
                continue
            seen.add(c.username)
            result.append({"username": c.username, "name": c.name})
        return result

    async def broadcast(self, payload: dict) -> None:
        for conn in list(self.connections):
            await conn.send(payload)

    async def _broadcast_presence(self) -> None:
        await self.broadcast({"type": "presence", "online": self.online_members()})


manager = ModuleTreeManager()


@router.websocket("/ws")
async def ws_module_tree(websocket: WebSocket, token: str = Query(None)):
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

    # 2. 接受连接 + 加入全局房间
    await websocket.accept()
    conn = ModuleTreeConnection(websocket, username, name)
    await manager.connect(conn)

    try:
        # 3. welcome
        await conn.send({
            "type": "welcome",
            "you": username,
            "online": manager.online_members(),
        })
        # 4. 接收客户端帧
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "ping":
                conn.last_ping = time.monotonic()
                await conn.send({"type": "pong"})
            # 其它类型忽略（本房间只做广播，不发上行业务）
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"责任树 WS 连接异常 user={username}: {e}")
    finally:
        await manager.disconnect(conn)


async def ws_broadcast_module_tree_updated(product: str, by: str) -> None:
    """REST 保存成功后调用：广播「某产品树被修改」。

    调用方应自行 try/except，失败不影响主流程。
    """
    payload = {"type": "module_tree.updated", "product": product, "by": by}
    try:
        await manager.broadcast(payload)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"责任树 WS 广播失败: {e}")
