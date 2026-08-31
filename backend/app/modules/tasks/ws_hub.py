"""工单讨论区 WS 房间广播 Hub（本地 + 可选 Redis 跨进程）。

当前部署是单进程 ``uvicorn.run("app:app")``，纯内存房间广播即可；但
``docs/PRODUCT/SETUP.md`` / ``ARCHITECTURE.md`` 规划了 gunicorn 多进程部署，
届时「进程内广播」会导致 A 进程读者的已读回执送不到 B 进程的作者 —— 已读名单
看起来就是「不更新」。

本模块把房间管理与广播策略收敛到一处：
- Redis 可用 → 本地广播 + 发布到 Redis 频道，其他进程订阅后本地广播；
- Redis 不可用 → 静默降级为本地广播（行为与改造前一致，不引入新的失败模式）。

广播采用 lazy start：第一次 broadcast 时才建 Redis 连接与订阅任务，
无需改动应用 lifespan。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import uuid
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

WS_HUB_CHANNEL = "openrobot:taskroom"
# 订阅异常重连退避上限（秒）
_SUBSCRIBE_MAX_BACKOFF = 30


class RoomHub:
    """按 task_id 分房间的连接集合 + 跨进程广播。"""

    def __init__(self, redis_url: Optional[str] = None, channel: str = WS_HUB_CHANNEL) -> None:
        self._rooms: Dict[int, Set[Any]] = {}
        self._redis_url = redis_url
        self._channel = channel
        # 本进程唯一标识：过滤掉自己发出去的广播，避免重复投递
        self._origin = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._pub: Any = None
        self._sub_task: Optional[asyncio.Task] = None
        self._start_lock = asyncio.Lock()
        self._started = False
        self.degraded = False

    # ── 房间管理（纯内存，与广播策略无关） ────────────────────────────────

    def add(self, task_id: int, conn: Any) -> None:
        self._rooms.setdefault(task_id, set()).add(conn)

    def discard(self, task_id: int, conn: Any) -> None:
        room = self._rooms.get(task_id)
        if not room:
            return
        room.discard(conn)
        if not room:
            self._rooms.pop(task_id, None)

    def members(self, task_id: int) -> Set[Any]:
        return set(self._rooms.get(task_id, ()))

    # ── 生命周期 ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """幂等启动；Redis 不可用则降级为本地广播（不抛异常）。"""
        if self._started or not self._redis_url:
            return
        async with self._start_lock:
            if self._started:
                return
            self._started = True
            try:
                self._pub = await self._create_redis()
                await self._pub.ping()
                self._sub_task = asyncio.create_task(self._subscribe_loop())
                logger.info("WS 房间广播已启用 Redis 跨进程分发 channel=%s", self._channel)
            except Exception as exc:  # noqa: BLE001
                self.degraded = True
                self._pub = None
                logger.warning("WS 房间广播 Redis 不可用，降级为单进程广播: %s", exc)

    async def _create_redis(self) -> Any:
        from redis.asyncio import Redis as AsyncRedis  # 延迟导入：Redis 是可选依赖

        return AsyncRedis.from_url(self._redis_url, decode_responses=True)

    async def stop(self) -> None:
        task, self._sub_task = self._sub_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        pub, self._pub = self._pub, None
        if pub is not None:
            try:
                await pub.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._started = False

    # ── 广播 ────────────────────────────────────────────────────────────

    async def broadcast(self, task_id: int, payload: Dict[str, Any]) -> None:
        """本地直投 + 跨进程分发。

        即使本进程该房间无人也要 publish —— 其他进程可能有人在线。
        """
        await self._local_broadcast(task_id, payload)
        await self._publish(task_id, payload)

    async def _local_broadcast(self, task_id: int, payload: Dict[str, Any]) -> None:
        for conn in list(self._rooms.get(task_id, ())):
            await conn.send(payload)

    async def _publish(self, task_id: int, payload: Dict[str, Any]) -> None:
        await self.start()
        if self._pub is None:
            return
        envelope = json.dumps(
            {"origin": self._origin, "task_id": int(task_id), "payload": payload},
            ensure_ascii=False,
        )
        try:
            await self._pub.publish(self._channel, envelope)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WS 房间广播 Redis publish 失败（本地已投递）: %s", exc)

    async def _subscribe_loop(self) -> None:
        backoff = 1
        while True:
            sub = None
            try:
                sub = await self._create_redis()
                async with sub.pubsub() as ps:
                    await ps.subscribe(self._channel)
                    backoff = 1
                    async for message in ps.listen():
                        if message.get("type") != "message":
                            continue
                        self._handle_remote(message.get("data"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("WS 房间广播 Redis 订阅中断，%ss 后重连: %s", backoff, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _SUBSCRIBE_MAX_BACKOFF)
            finally:
                if sub is not None:
                    try:
                        await sub.aclose()
                    except Exception:  # noqa: BLE001
                        pass

    def _handle_remote(self, data: Any) -> None:
        """处理其他进程发来的广播：跳过自己发出的，其余本地投递。"""
        try:
            envelope = json.loads(data)
            if envelope.get("origin") == self._origin:
                return
            task_id = int(envelope["task_id"])
            payload = envelope["payload"]
        except Exception:  # noqa: BLE001
            logger.warning("WS 房间广播收到非法消息，已丢弃")
            return
        # 订阅回调是同步上下文，投递是协程 → 交给事件循环，不阻塞订阅循环
        asyncio.create_task(self._local_broadcast(task_id, payload))
