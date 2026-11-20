"""工单 Agent 状态刷新 Worker — 每次 @U老师 之后，把新讨论折叠进结构化状态。

设计（照 `resolution_worker` 的现成形状）：
  - discuss 回复落库后，把 task_id LPUSH 到 Redis List（ors:agent_state）
  - Worker 起 N（agent_state_worker_concurrency，默认 4）个并行消费者协程
  - 每个消费者 BRPOP 取一个任务，独立处理，互不影响
  - 每个任务：读状态 + 水位线 → 读水位线之后的新评论 → **扣掉最近 raw_window 条**
    （那几条留在 prompt 里当原文）→ 没有可折叠的**直接跳过不调 LLM** →
    有则按九栏模板并入 → upsert + 推进水位线

为什么用队列而不是 asyncio.create_task：
  1. 不给用户回复加延迟（回复此时已落库，收尾立即返回）；
  2. **天然幂等**：同一工单被重复入队，第二次跑时水位线已推进、无可折叠 → 自动跳过，
     正好处理「用户连着追问三次」；
  3. 有界并发、失败可重试（水位线不动，下次 @ 自然重试）。

进程模型：与 backend 独立进程；不推 WS。
"""

import asyncio
from typing import Optional

from ai.config import get_ai_config
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.contexts.agent_state import (
    MERGE_SYSTEM_PROMPT,
    build_merge_prompt,
    load_state,
    merge_state,
    save_state,
)
from ai.agents.AiTaskPlatform.contexts.comments import (
    load_comment_rows_after,
    render_comment_lines,
)

logger = get_logger("TASK_AGENT")

# 单次续写上限：状态是结构化 JSON，长工单也不会离谱；撞顶会在日志里显形。
MERGE_MAX_TOKENS = 2000


async def _ensure_llm(llm):
    if llm is not None:
        return llm
    from ai.core import get_llm_client as _get  # 惰性：避免模块导入期拉重依赖
    return await _get()


async def refresh_ticket_state(task_id, llm=None) -> dict:
    """把某工单「水位线之后、又不在原文窗口内」的评论折叠进状态。

    Returns:
        {"skipped": bool, "reason": str, ...} 或 {"ok": True, "watermark": int, "rounds": int}
    """
    cfg = get_ai_config()
    raw_window = max(0, int(cfg.agent_state_raw_window or 0))

    cur = load_state(task_id)
    watermark = int(cur.get("covered_comment_id") or 0)
    rows = load_comment_rows_after(task_id, after_id=watermark)
    if not rows:
        return {"skipped": True, "reason": "水位线之后没有新评论"}

    # 最近 raw_window 条留在 prompt 里当原文，不折叠进状态。
    foldable = rows if raw_window <= 0 else rows[:-raw_window]
    if not foldable:
        return {"skipped": True, "reason": f"新评论未超出原文窗口({raw_window})"}

    lines = render_comment_lines(foldable, per_comment=None)
    if not lines:
        return {"skipped": True, "reason": "可折叠评论渲染为空"}

    llm = await _ensure_llm(llm)
    if llm is None:
        return {"skipped": True, "reason": "LLM 客户端不可用"}

    # 首次构建（老工单、水位线为 0）可能一次折叠很多条 → 分块并入，
    # 避免单次调用自己把窗口顶满。
    from ai.agents.AiTaskPlatform.contexts.history_compact import _CHUNK_TOKENS, _chunks

    state = cur.get("state")
    for chunk in _chunks(lines, _CHUNK_TOKENS):
        try:
            raw = await llm.complete(
                prompt=build_merge_prompt(state, chunk),
                system_prompt=MERGE_SYSTEM_PROMPT,
                max_tokens=MERGE_MAX_TOKENS,
                temperature=0.1,
            )
        except Exception as e:
            logger.warning(f"[agent_state] 折叠调用失败 task={task_id}: {e}")
            return {"skipped": True, "reason": f"LLM 调用失败: {e}"}
        merged = merge_state(raw, state)
        if merged is None:
            # 解析失败或模型把状态清空了 —— 不采纳、不推进水位线，下次重试。
            logger.warning(
                f"[agent_state] 折叠输出不可用 task={task_id} "
                f"len={len((raw or '').strip())}"
            )
            return {"skipped": True, "reason": "折叠输出不可用"}
        state = merged

    new_watermark = foldable[-1].get("id") or watermark
    rounds = int(cur.get("rounds") or 0) + 1
    if not save_state(task_id, state, new_watermark, rounds):
        return {"skipped": True, "reason": "保存失败"}
    logger.info(
        f"[agent_state] 已刷新 task={task_id} 折叠={len(foldable)}条 "
        f"水位线={new_watermark} 轮次={rounds}"
    )
    return {"ok": True, "watermark": new_watermark, "rounds": rounds, "folded": len(foldable)}


async def _consumer(queue_key: str, llm, stop: asyncio.Event) -> None:
    """单个消费者：BRPOP 阻塞取任务，逐个处理。

    ⚠️ **超时不是异常**：BRPOP 到点没数据必然超时，那是空轮询的正常结果。
    照 `resolution_worker._consumer` 的已验写法——brpop 用短超时，外面再套一层
    wait_for，超时直接 `continue`。

    这里踩过坑：一开始把超时当异常记 WARNING（`brpop(timeout=30)` 直接抛
    `Timeout reading from localhost:6379`），于是 N 个消费者每隔几秒各刷一条，
    把日志淹了（2026-10-14 生产实测）。
    """
    r = None
    try:
        import redis.asyncio as aioredis
        from redis.exceptions import TimeoutError as RedisTimeout

        r = aioredis.from_url(get_ai_config().redis_url or "redis://localhost:6379/0")
        while not stop.is_set():
            try:
                item = await asyncio.wait_for(r.brpop(queue_key, timeout=1), timeout=3)
            except (asyncio.TimeoutError, RedisTimeout):
                # 空轮询：队列里没有任务，正常。不记日志。
                continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # 真异常（连不上 Redis 等）才告警，退避 1 秒再试
                logger.warning(f"[agent_state] BRPOP 异常，1s 后重试: {e}")
                await asyncio.sleep(1)
                continue
            if not item:
                continue
            _, raw = item
            try:
                task_id = int(raw)
            except (TypeError, ValueError):
                logger.warning(f"[agent_state] 非法任务载荷 {raw!r}，忽略")
                continue
            try:
                await refresh_ticket_state(task_id, llm)
            except Exception as e:
                logger.error(f"[agent_state] 处理 #{task_id} 异常: {e}")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[agent_state] consumer 异常退出: {e}")
    finally:
        if r is not None:
            try:
                await r.aclose()
            except Exception:
                pass


class AgentStateWorker:
    """工单状态刷新 Worker — Redis 队列 + N 个并行消费者。"""

    def __init__(self, concurrency: Optional[int] = None):
        cfg = get_ai_config()
        self.concurrency = int(concurrency or cfg.agent_state_worker_concurrency)
        self.queue_key = cfg.agent_state_worker_queue
        self._stop = asyncio.Event()
        self._consumers: list[asyncio.Task] = []
        self._llm = None

    async def enqueue(self, task_id) -> None:
        """把工单推入状态刷新队列（即时 + 异步）。失败只告警，绝不阻断回复。"""
        try:
            import redis.asyncio as aioredis

            r = aioredis.from_url(get_ai_config().redis_url or "redis://localhost:6379/0")
            try:
                await r.lpush(self.queue_key, str(int(task_id)))
            finally:
                await r.aclose()
        except Exception as e:
            logger.warning(f"[agent_state] enqueue #{task_id} 失败: {e}")

    async def run(self) -> None:
        logger.info(
            f"工单状态刷新 Worker 启动，并行数={self.concurrency}, 队列={self.queue_key}"
        )
        try:
            self._llm = await _ensure_llm(None)
        except Exception as e:
            logger.warning(f"工单状态刷新 Worker 获取 LLM 客户端失败: {e}")
            self._llm = None
        for _ in range(self.concurrency):
            self._consumers.append(
                asyncio.create_task(_consumer(self.queue_key, self._llm, self._stop))
            )
        if self._consumers:
            await asyncio.gather(*self._consumers, return_exceptions=True)

    async def stop(self) -> None:
        if not self._stop.is_set():
            self._stop.set()
        for c in self._consumers:
            c.cancel()
        if self._consumers:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._consumers, return_exceptions=True), timeout=5
                )
            except asyncio.TimeoutError:
                pass
        logger.info("工单状态刷新 Worker 已停止")


_worker_singleton: Optional[AgentStateWorker] = None


def agent_state_worker_start() -> tuple[AgentStateWorker, asyncio.Task]:
    """启动 worker（供 ai/run.py lifespan 调用），并登记为进程级单例。"""
    global _worker_singleton
    worker = AgentStateWorker()
    _worker_singleton = worker
    task = asyncio.create_task(worker.run())
    return worker, task


async def enqueue_ticket_state(task_id) -> None:
    """供 discuss 流程调用：入队刷新该工单状态（Worker 未启动时静默跳过）。"""
    if _worker_singleton is None:
        return
    await _worker_singleton.enqueue(task_id)
