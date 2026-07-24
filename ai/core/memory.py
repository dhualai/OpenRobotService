"""
对话上下文管理

- Redis 可用 → 持久化存储（服务重启保留）
- Redis 不可用 → 内存 dict（服务重启丢失，但无需任何外部服务）
"""
import asyncio
import json
import re
import time
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

from ai.config import get_ai_config
from ai.exceptions import ServiceUnavailableError
from ai.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SessionMemory:
    """会话记忆"""
    session_id: str
    turns: List[Dict[str, str]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MemoryManager:
    """
    会话记忆管理

    优先 Redis，连接失败自动降到内存模式。
    """

    PRONOUN_PATTERNS = [
        (r"然后呢", "继续上一步"),
        (r"接着", "继续上一步"),
        (r"下一步", "下一步"),
        (r"还有呢", "继续"),
        (r"刚才说的", "上一条"),
        (r"那个", "上一条"),
        # 数字选择（分流/列表选择场景）："2"、"选1"、"第一个" 等
        (r"^\d+$", "选择列表项"),
        (r"选\d+", "选择列表项"),
        (r"第\d+个", "选择列表项"),
        (r"第[一二三四五六七八九十]个", "选择列表项"),
        (r"是\d+", "确认列表项"),
        (r"我选\d+", "选择列表项"),
    ]

    def __init__(self, redis_url: str, max_turns: int = 10, ttl: int = 0):
        self.redis_url = redis_url
        self.max_turns = max_turns
        self.ttl = ttl  # 0 = 永久
        self._redis = None
        self._fallback: Dict[str, Dict[str, Any]] = {}
        self._fallback_pending: set = set()
        self._redis_ok: Optional[bool] = None
        self._redis_last_check: float = 0       # 上次尝试 Redis 的时间戳
        self._redis_retry_interval: float = 60.0  # Redis 恢复后重试间隔（秒）
        self._lock = asyncio.Lock()

    async def _ensure_redis(self, for_write: bool = False):
        """懒连接 Redis，失败则自动用内存；每隔 retry_interval 秒重试一次"""
        now = time.time()
        if self._redis_ok is False:
            # 距上次失败不足 retry_interval 秒，不再尝试
            if now - self._redis_last_check < self._redis_retry_interval:
                return None
            # 超过间隔，重置标记允许重试
            self._redis_ok = None
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is not None:
                return self._redis
            if self._redis_ok is False:
                return None
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(
                    self.redis_url, encoding="utf-8", decode_responses=True,
                    socket_connect_timeout=1, socket_timeout=1,
                )
                await self._redis.ping()
                self._redis_ok = True
                self._redis_last_check = now
            except Exception as e:
                self._redis = None
                self._redis_ok = False
                self._redis_last_check = now
                logger.warning(f"Redis 连接失败（将使用内存/MySQL降级）: url={self.redis_url}, error={e}")
        return self._redis

    def _get_key(self, session_id: str) -> str:
        return f"ai:memory:{session_id}"

    async def get_memory(self, session_id: str) -> SessionMemory:
        # 1. 内存兜底优先——save_memory 总是双写，内存数据最新，且绝不会挂起
        if session_id in self._fallback:
            data = self._fallback[session_id]
            return SessionMemory(session_id=session_id, turns=data.get("turns", []), metadata=data.get("metadata", {}))
        # 2. Redis（带超时保护，避免僵死连接挂起整个请求）
        client = await self._ensure_redis()
        if client:
            try:
                data = await asyncio.wait_for(
                    client.get(self._get_key(session_id)), timeout=2.0,
                )
                if data:
                    parsed = json.loads(data)
                    return SessionMemory(session_id=session_id, turns=parsed.get("turns", []), metadata=parsed.get("metadata", {}))
            except (asyncio.TimeoutError, Exception):
                pass
        # 3. MySQL 降级：Redis 和内存都没有时，尝试从 MySQL 加载
        try:
            from ai.core.conversation_store import get_history
            rows = await asyncio.to_thread(get_history, session_id)
            if rows:
                turns = [{"role": r["role"], "content": r["content"]} for r in rows]
                logger.info(f"MySQL 降级加载成功: session={session_id}, turns={len(turns)}")
                return SessionMemory(session_id=session_id, turns=turns)
        except Exception as e:
            logger.error(f"MySQL 降级加载失败: session={session_id}, error={e}", exc_info=True)
        return SessionMemory(session_id=session_id)

    async def save_memory(self, memory: SessionMemory) -> None:
        data = {"turns": memory.turns[-self.max_turns:], "metadata": memory.metadata}
        # 始终写入内存兜底（确保服务生命周期内数据不丢失）
        self._fallback[memory.session_id] = data
        # 写 Redis（失败不影响内存数据）
        client = await self._ensure_redis(for_write=True)
        if client:
            try:
                value = json.dumps(data, ensure_ascii=False)
                key = self._get_key(memory.session_id)
                if self.ttl > 0:
                    await client.setex(key, self.ttl, value)
                else:
                    await client.set(key, value)
            except Exception as e:
                logger.warning(f"Redis 写入失败: session={memory.session_id}, error={e}")

    async def add_turn(
        self, session_id: str, role: str, content: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: str = "",
    ) -> SessionMemory:
        memory = await self.get_memory(session_id)
        turn = {"role": role, "content": content}
        if metadata:
            turn["metadata"] = metadata
        memory.turns.append(turn)
        if len(memory.turns) > self.max_turns:
            memory.turns = memory.turns[-self.max_turns:]
        await self.save_memory(memory)

        # MySQL 双写已禁用：会话管理统一走前端 → backend /api/call/conversations
        # （原 conversation_store.save_message 会创建 title="新会话" 的冗余会话记录）
        # try:
        #     from ai.core.conversation_store import save_message
        #     await asyncio.to_thread(
        #         save_message, session_id=session_id, role=role,
        #         content=content, user_id=user_id,
        #     )
        # except Exception as e:
        #     logger.error(f"MySQL 双写失败: session={session_id}, role={role}, error={e}", exc_info=True)

        return memory

    async def get_context(self, session_id: str, max_turns: Optional[int] = None) -> List[Dict[str, str]]:
        memory = await self.get_memory(session_id)
        turns = memory.turns[-self.max_turns:] if max_turns is None else memory.turns[-max_turns:]
        return [{"role": t["role"], "content": t["content"]} for t in turns]

    async def resolve_pronoun(self, query: str, session_id: str) -> Tuple[str, bool]:
        """
        指代消解：检测"然后呢""还有呢"等省略表达，用上文补全为完整查询。

        用于提升检索精度——"然后呢"本身无法命中知识库，
        结合上文用户问题和助手回答后可以构造出有意义的检索词。
        """
        # 检测是否匹配指代模式
        matched_pattern = None
        for pattern, _ in self.PRONOUN_PATTERNS:
            if re.search(pattern, query):
                matched_pattern = pattern
                break
        if not matched_pattern:
            return query, False

        memory = await self.get_memory(session_id)
        turns = memory.turns

        # 找到最近一轮用户消息和助手回复（跳过当前 query 本身）
        prev_user = None
        prev_assistant = None
        for t in reversed(turns):
            if t["role"] == "user" and prev_user is None:
                prev_user = t["content"]
            elif t["role"] == "assistant" and prev_assistant is None:
                prev_assistant = t["content"]
            if prev_user and prev_assistant:
                break

        if not prev_user:
            return query, False

        # 用上文补全：把用户省略表达和对话上下文拼接成可检索的完整查询
        parts = [f"用户追问：{query}"]
        parts.append(f"用户原问题：{prev_user[:300]}")
        if prev_assistant:
            # 截取助手回答的要点（去掉过长的细节）
            assistant_brief = prev_assistant[:400]
            parts.append(f"助手此前回答：{assistant_brief}")
        resolved = "；".join(parts)
        return resolved, True

    async def clear(self, session_id: str) -> None:
        client = await self._ensure_redis()
        if client:
            try:
                await client.delete(self._get_key(session_id))
            except Exception:
                pass
        self._fallback.pop(session_id, None)

    async def clear_all(self) -> int:
        """清空所有会话，返回清除数量"""
        count = 0
        client = await self._ensure_redis()
        if client:
            try:
                keys = await client.keys(self._get_key("*"))
                if keys:
                    count = await client.delete(*keys)
            except Exception:
                pass
        count += len(self._fallback)
        self._fallback.clear()
        return count

    async def add_pending_ticket(self, session_id: str) -> None:
        """将 session 加入待派单列表"""
        client = await self._ensure_redis()
        if client:
            await client.sadd("usp:pending_tickets", session_id)
        self._fallback_pending.add(session_id)

    async def remove_pending_ticket(self, session_id: str) -> None:
        client = await self._ensure_redis()
        if client:
            await client.srem("usp:pending_tickets", session_id)
        self._fallback_pending.discard(session_id)

    async def list_pending_tickets(self) -> list:
        client = await self._ensure_redis()
        if client:
            return list(await client.smembers("usp:pending_tickets"))
        return list(self._fallback_pending)

    async def health_check(self) -> bool:
        client = await self._ensure_redis()
        return client is not None


# 全局单例
_memory_manager: Optional[MemoryManager] = None
_manager_lock = asyncio.Lock()

async def get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        async with _manager_lock:
            if _memory_manager is None:
                config = get_ai_config()
                _memory_manager = MemoryManager(
                    redis_url=config.redis_url,
                    max_turns=config.redis_max_context_turns,
                    ttl=config.redis_ttl,
                )
    return _memory_manager
