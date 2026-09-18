"""诊断后台服务：自动扫描新工单 → 触发三路分析 → 写 task_comments

设计：
  - FastAPI lifespan 启动时作为后台 asyncio 协程运行
  - 每隔 diagnosis_scan_interval 秒扫描一次 tasks 表
  - 只处理 source='ai' 且 status IN (in_progress, pending) 且未被诊断过的工单
  - 逐工单异步诊断（不并行——避免 LLM 并发过载）

"是否被诊断过"的判断依据：
  SELECT 1 FROM task_comments WHERE task_id = X AND created_by = 'U老师'
"""

import asyncio
import re
import time
import logging

from ai.config import get_ai_config
from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


from ai.core.logging import get_logger
logger = get_logger("TASK_AGENT")


def _is_diagnosed(task_id: int) -> bool:
    """查询 task_comments 表：此工单是否已有U老师诊断评论。"""
    from app.models.task import TaskComment
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        return db.query(TaskComment).filter(
            TaskComment.task_id == task_id,
            TaskComment.created_by == "U老师",
        ).first() is not None
    finally:
        db.close()


def _scan_undiagnosed_tasks() -> list[dict]:
    """扫描 tasks 表中待诊断的工单。

    Returns:
        工单列表，每项包含 {"id": int, "title": str, "status": str, "priority": str}
    """
    from app.models.task import Task, TaskStatus
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        # 待诊断状态：in_progress / pending
        candidates = db.query(Task).filter(
            Task.source == "ai",
            Task.status.in_([TaskStatus.IN_PROGRESS, TaskStatus.PENDING]),
        ).order_by(Task.priority.desc(), Task.created_at.desc()).limit(50).all()

        undiagnosed = []
        for task in candidates:
            if not _is_diagnosed(task.id):
                undiagnosed.append({
                    "id": task.id,
                    "title": task.title or "",
                    "status": task.status.value,
                    "priority": task.priority.value if task.priority else "medium",
                })
        return undiagnosed
    finally:
        db.close()


async def _diagnose_one(task_id: int) -> dict:
    """对单个工单执行诊断：三路分析 → 写入 task_comments.

    Returns:
        {"task_id": int, "status": "ok"|"failed", "confidence": float|None}
    """
    from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent
    from ai.agents.AiTaskPlatform.schemas import TaskAnalyzeRequest

    agent = AiTaskAgent()
    try:
        await agent._ensure_clients()
        request = TaskAnalyzeRequest(task_id=str(task_id), session_id=f"diag_{task_id}_{int(time.time())}")
        draft = await agent.analyze(request)
        # analyze() 内部已调用 _add_diagnosis_comment()
        return {"task_id": task_id, "status": "ok", "confidence": draft.confidence}
    except Exception as e:
        logger.error(f"Diagnosis failed for task {task_id}: {e}")
        return {"task_id": task_id, "status": "failed", "confidence": None}


async def run_diagnosis_worker(stop_event: asyncio.Event):
    """后台诊断 worker：轮询扫描 → 逐工单诊断。

    在 FastAPI lifespan 中以 asyncio.create_task() 启动，
    stop_event 用于优雅关闭。
    """
    config = get_ai_config()
    interval = config.diagnosis_scan_interval
    logger.info(f"Diagnosis worker started (scan interval={interval}s)")

    while not stop_event.is_set():
        try:
            # 1. 扫描待诊断工单
            undiagnosed = _scan_undiagnosed_tasks()

            if undiagnosed:
                task_ids = [f'#{t["id"]}' for t in undiagnosed]
                logger.info(f"Found {len(undiagnosed)} undiagnosed task(s): {', '.join(task_ids)}")

                # 2. 逐工单诊断
                for task in undiagnosed:
                    if stop_event.is_set():
                        break
                    result = await _diagnose_one(task["id"])
                    status = result["status"]
                    conf = f" (confidence={result['confidence']:.0%})" if result.get("confidence") else ""
                    logger.info(f"  #{result['task_id']}: {status}{conf}")
            else:
                logger.debug(f"No undiagnosed tasks found")

        except Exception as e:
            logger.warning(f"Diagnosis worker scan failed: {e}")

        # 3. 等待下一次扫描
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass  # timeout 表示该下一次扫描了

    logger.info("Diagnosis worker stopped")


def diagnosis_worker_start() -> tuple[asyncio.Task, asyncio.Event]:
    """启动诊断 worker，返回 (task, stop_event) 供 lifespan shutdown 使用。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_diagnosis_worker(stop_event))
    return task, stop_event
