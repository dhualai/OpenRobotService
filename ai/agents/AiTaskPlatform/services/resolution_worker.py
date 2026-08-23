"""解决方式总结 Worker — 结束工单 AI 确认弹窗的后台总结服务

设计（模式 B：即时 + 异步回写 + 并行）：
  - 前端点「处理完成」→ 后端把任务 LPUSH 到 Redis List（ors:resolution）
  - Worker 起 N（resolution_worker_concurrency，默认 10）个并行消费者协程
  - 每个消费者用 BRPOP 阻塞取一个任务，取到后独立并行处理，互不影响
  - 每个任务只做一轮 LLM 对话（快速简短），取材：ai_summary + 其后新评论 + description/diagnosis.solution
  - 结果写 metadata_info.resolution_summary（草案），前端轮询读取回填
  - 无内容 → 不调 LLM，写空串（占位提示由前端 placeholder 控制）

进程模型：
  - 与 backend 为独立进程；不直接推 WS（ConnectionManager 在 backend 进程内存），
    前端通过轮询 GET /api/tasks/{id} 读取 resolution_summary 回填。
"""

import asyncio
import time
import logging
from typing import Optional

from ai.config import get_ai_config
from ai.core.logging import get_logger
from ai.core.llm import get_llm_client
from ai.agents.AiTaskPlatform.prompts import (
    RESOLUTION_FULL_TEMPLATE,
    RESOLUTION_INCREMENTAL_TEMPLATE,
    RESOLUTION_SYSTEM_PROMPT,
)

logger = get_logger("TASK_AGENT")


async def _summarize_resolution(task_id: int, llm) -> tuple[str, bool]:
    """对单个工单生成"问题解决方式"总结（只一轮 LLM，快速简短）。

    Returns:
        (summary_text, has_ai)
          summary_text: 总结文本；无内容时为 ""（前端 placeholder 兜底）
          has_ai:       是否有可用内容/是否成功生成
    """
    from app.models.task import Task, TaskComment
    from app.core.database import SessionLocal
    from datetime import datetime as dt

    db = SessionLocal()
    task = None
    try:
        comments = db.query(TaskComment).filter(
            TaskComment.task_id == task_id
        ).order_by(TaskComment.created_at.asc()).all()

        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            logger.warning(f"Resolution #{task_id}: 工单不存在，跳过")
            return "", False

        task_title = task.title or ""
        task_desc = task.description or ""
        meta = dict(task.metadata_info or {})
        diag = meta.get("diagnosis", {}) or {}
        diag_summary = diag.get("problem_summary", "") or ""
        diag_solution = diag.get("solution", "") or ""

        # ai_summary 是历史讨论的浓缩结论；解决方式在其基础上 + 摘要之后的新评论提炼（含 AI/U老师）
        last_summary_text = meta.get("ai_summary", "") or ""
        last_summary_at = None
        _at_str = meta.get("ai_summary_at", "")
        if _at_str:
            try:
                last_summary_at = dt.strptime(_at_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        if last_summary_text:
            # 有 ai_summary：只取摘要之后的新评论（含 U老师）作为增量素材
            new_comments = []
            for c in comments:
                if last_summary_at and c.created_at and c.created_at <= last_summary_at:
                    continue
                if (c.content or "").strip():
                    new_comments.append(c)
        else:
            # 无 ai_summary：读全部有效评论（含 U老师）兜底
            new_comments = []
            for c in comments:
                if (c.content or "").strip():
                    new_comments.append(c)
    finally:
        db.close()

    # 组装历史对话行
    history_lines = []
    for c in new_comments[-20:]:
        author = getattr(c, 'created_by_name', None) or c.created_by or "?"
        content = (c.content or "")[:200]
        history_lines.append(f"[{author}] {content}")
    history_text = "\n".join(history_lines) if history_lines else ""

    # 判断是否有可总结内容：ai_summary / 讨论评论 / diagnosis 方案 / 问题描述 任一存在即尝试总结
    has_material = bool(last_summary_text.strip()) or bool(history_text.strip()) or bool(diag_solution)

    if not has_material:
        # 无任何内容 → 不调 LLM，返回空串（前端 placeholder【请补充解决方法】）
        logger.info(f"Resolution #{task_id}: 无可用材料(无摘要/无讨论/无诊断方案)，不调 LLM")
        return "", False

    try:
        if last_summary_text:
            prompt = RESOLUTION_INCREMENTAL_TEMPLATE.format(
                previous_summary=last_summary_text,
                discussion_history=history_text or "（无新增讨论）",
            )
        else:
            prompt = RESOLUTION_FULL_TEMPLATE.format(
                title=task_title or f"工单 #{task_id}",
                description=task_desc or "",
                diagnosis_summary=(diag_summary or diag_solution or "无"),
                discussion_history=history_text or "（无）",
            )
        # 任务级超时：避免单个卡住拖慢（只在个人消费者内超时，不影响其他）
        summary = await asyncio.wait_for(
            llm.complete(
                prompt=prompt,
                system_prompt=RESOLUTION_SYSTEM_PROMPT,
                max_tokens=300,
                temperature=0.3,
            ),
            timeout=get_ai_config().resolution_worker_wait_timeout,
        )
        summary_text = summary.strip()
        # LLM 判定"无真正解决方案"（只输出 NO_SOLUTION）→ 视为无方案，返回空
        if summary_text.upper() in ("NO_SOLUTION", "__NO_SOLUTION__"):
            logger.info(f"Resolution #{task_id}: LLM 判定无真正解决方案 (NO_SOLUTION)")
            return "", False
        return summary_text, True
    except asyncio.TimeoutError:
        logger.warning(f"Resolution #{task_id}: LLM 总结超时")
        return "", False
    except Exception as e:
        logger.error(f"Resolution #{task_id}: LLM 总结失败: {e}")
        return "", False


def _save_resolution_draft(task_id: int, summary_text: str) -> None:
    """把 worker 生成的解决方式草案写入 metadata_info.resolution_summary。

    - summary_text 非空 → 写解决方式 + resolution_gen_state=done（有内容）。
    - summary_text 为空（无材料/失败）→ 不写解决方式，resolution_gen_state=empty（表示尝试过但无材料，允许稍后重试）。
    """
    from app.models.task import Task
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return
        meta = dict(task.metadata_info or {})
        if summary_text.strip():
            meta["resolution_summary"] = summary_text
            meta["resolution_summary_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            meta["resolution_gen_state"] = "done"
            logger.info(f"Resolution #{task_id}: 草案已写入 metadata_info.resolution_summary")
        else:
            meta.pop("resolution_summary", None)
            meta["resolution_gen_state"] = "empty"
            meta["resolution_empty_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"Resolution #{task_id}: 无材料，置 generation state=empty（允许稍后重试）")
        task.metadata_info = meta
        db.commit()
    finally:
        db.close()


async def _process_one(task_id: int, llm) -> None:
    """处理单个解决方式总结任务（独立并行，互不影响）。"""
    t0 = time.perf_counter()
    try:
        summary_text, has_ai = await _summarize_resolution(task_id, llm)
        _save_resolution_draft(task_id, summary_text)
        ms = round((time.perf_counter() - t0) * 1000)
        logger.info(f"Resolution #{task_id}: {'生成' if has_ai else '无内容/失败'}完成, {ms}ms")
    except Exception as e:
        logger.error(f"Resolution #{task_id}: 处理失败: {e}")


async def _consumer(queue_key: str, llm, stop_event: asyncio.Event) -> None:
    """单个消费者：BROP 阻塞取任务 → 独立并行处理。"""
    import redis.asyncio as aioredis
    from ai.config import get_ai_config as _cfg

    r = None
    try:
        r = aioredis.from_url(_cfg().redis_url or "redis://localhost:6379/0")
        logger.debug(f"Resolution consumer started, queue={queue_key}")
        while not stop_event.is_set():
            try:
                result = await asyncio.wait_for(
                    r.brpop(queue_key, timeout=1),
                    timeout=2,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                continue
            if not result:
                continue
            _, raw = result
            try:
                task_id = int(raw)
            except (TypeError, ValueError):
                logger.warning(f"Resolution: 非法任务载荷 {raw!r}，忽略")
                continue
            logger.info(f"Resolution[consume] 消费到任务 #{task_id} (队列={queue_key})")
            await _process_one(task_id, llm)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Resolution consumer 异常: {e}")
    finally:
        if r:
            try:
                await r.aclose()
            except Exception:
                pass


class ResolutionSummaryWorker:
    """解决方式总结 Worker — Redis 队列 + N 个并行消费者。

    使用方式:
        worker = ResolutionSummaryWorker()
        task = asyncio.create_task(worker.run())
        # ... 运行中 ...
        await worker.stop()

    对外入队接口（后端调）：
        await worker.enqueue(task_id)   # LPUSH 到队列
    """

    def __init__(self, concurrency: Optional[int] = None):
        cfg = get_ai_config()
        self.concurrency = concurrency or cfg.resolution_worker_concurrency
        self.queue_key = cfg.resolution_worker_queue
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._consumers: list[asyncio.Task] = []
        self._llm = None

    async def enqueue(self, task_id: int) -> None:
        """把任务推入解决方式总结队列（即时 + 异步处理）。"""
        import redis.asyncio as aioredis
        from ai.config import get_ai_config as _cfg
        try:
            r = aioredis.from_url(_cfg().redis_url or "redis://localhost:6379/0")
            try:
                await r.lpush(self.queue_key, str(int(task_id)))
            finally:
                await r.aclose()
        except Exception as e:
            logger.warning(f"Resolution enqueue #{task_id} 失败: {e}")

    async def run(self) -> None:
        """启动 N 个并行消费者。"""
        logger.info(f"解决方式总结 Worker 启动，并行数={self.concurrency}, 队列={self.queue_key}")
        try:
            self._llm = await get_llm_client()
        except Exception as e:
            logger.warning(f"解决方式总结 Worker 获取 LLM 客户端失败: {e}")
            self._llm = None

        for i in range(self.concurrency):
            c = asyncio.create_task(_consumer(self.queue_key, self._llm, self._stop))
            self._consumers.append(c)
        # 等待任一消费者退出（或全部）
        if self._consumers:
            await asyncio.gather(*self._consumers, return_exceptions=True)

    async def stop(self) -> None:
        """优雅关闭：置停止事件，等待消费者退出。"""
        if not self._stop.is_set():
            self._stop.set()
        for c in self._consumers:
            c.cancel()
        if self._consumers:
            try:
                await asyncio.wait_for(asyncio.gather(*self._consumers, return_exceptions=True), timeout=5)
            except asyncio.TimeoutError:
                pass
        logger.info("解决方式总结 Worker 已停止")


# 模块级单例（供 lifespan 使用）
_resolution_worker: Optional[ResolutionSummaryWorker] = None


def resolution_worker_start() -> tuple[ResolutionSummaryWorker, asyncio.Task]:
    """启动 resolver worker（供 ai/run.py lifespan 调用）。

    Returns:
        (worker, task)
    """
    global _resolution_worker
    worker = ResolutionSummaryWorker()
    _resolution_worker = worker
    task = asyncio.create_task(worker.run())
    worker._task = task
    return worker, task


def get_resolution_worker() -> Optional[ResolutionSummaryWorker]:
    """获取当前 resolution worker 单例（供后端接口入队）。"""
    return _resolution_worker
