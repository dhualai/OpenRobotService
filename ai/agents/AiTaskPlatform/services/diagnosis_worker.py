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


# ============================================================
# 知识沉淀 Service：工单解决后自动写入 Qdrant
# ============================================================

def _scan_resolved_unindexed_tasks() -> list[dict]:
    """扫描已解决/已关闭但尚未沉淀入 Qdrant 知识库的工单。

    "是否已沉淀"的标记：metadata_info.solution_indexed == True
    """
    from app.models.task import Task, TaskStatus
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        candidates = db.query(Task).filter(
            Task.status.in_([TaskStatus.RESOLVED, TaskStatus.CLOSED]),
        ).order_by(Task.updated_at.desc()).limit(20).all()

        unindexed = []
        for task in candidates:
            meta = task.metadata_info or {}
            if meta.get("solution_indexed"):
                continue
            unindexed.append({
                "id": task.id,
                "title": task.title or "",
                "description": task.description or "",
                "metadata_info": meta,
            })
        return unindexed
    finally:
        db.close()


def _extract_solution_text(task_id: int) -> tuple[str, str]:
    """从工单评论中提取解决方案文本。

    Returns:
        (root_cause, solution_steps) — 分别对应根因和解决步骤
    """
    from app.models.task import TaskComment
    from app.core.db import SessionLocal
    db = SessionLocal()
    try:
        comments = db.query(TaskComment).filter(
            TaskComment.task_id == task_id
        ).order_by(TaskComment.created_at.desc()).limit(20).all()

        root_cause = ""
        solution_steps = ""
        for c in comments:
            content = c.content or ""
            # 取U老师诊断评论作为根因
            if c.created_by == "U老师" and "根因分析" in content and not root_cause:
                m = re.search(r"\*\*根因分析[：:]\*\*\s*(.+?)(?=\n\*\*|\n##|$)", content, re.DOTALL)
                if m:
                    root_cause = m.group(1).strip()[:500]
            # 取最后的人类评论（可能的解决描述）
            if c.created_by != "U老师" and not solution_steps:
                if len(content) > 10:
                    solution_steps = content[:500]
        return root_cause or "无", solution_steps or "无"
    finally:
        db.close()


async def _index_resolved_task(task_id: int) -> dict:
    """将已解决工单沉淀到 Qdrant 知识库"""
    import re
    from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent
    from ai.core.task_adapter import load_task_context_dict

    d = load_task_context_dict(task_id)
    title = d.get("title", f"工单 #{task_id}") or f"工单 #{task_id}"
    root_cause, solution_steps = _extract_solution_text(task_id)

    agent = AiTaskAgent()
    try:
        await agent._ensure_clients()
        # 直接走 retriever 索引（不需要 SolutionDraft）
        await agent._retriever.index_task_resolution(
            task_id=str(task_id),
            title=title,
            root_cause=root_cause,
            solution_steps=solution_steps,
            engineer_note="",
            fault_code=d.get("fault_code", ""),
            robot_type=d.get("robot_type", ""),
            problem_summary=d.get("problem_summary", ""),
        )

        # 标记已沉淀
        from app.models.task import Task
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task:
                meta = dict(task.metadata_info or {})
                meta["solution_indexed"] = True
                meta["solution_indexed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                task.metadata_info = meta
                db.commit()
        finally:
            db.close()

        return {"task_id": task_id, "status": "ok"}
    except Exception as e:
        logger.error(f"Knowledge index failed for task {task_id}: {e}")
        return {"task_id": task_id, "status": "failed", "error": str(e)[:100]}


async def run_knowledge_worker(stop_event: asyncio.Event):
    """后台知识沉淀 worker：扫描已解决工单 → 写入 Qdrant"""
    config = get_ai_config()
    interval = config.diagnosis_scan_interval
    logger.info(f"Knowledge worker started (scan interval={interval}s)")

    while not stop_event.is_set():
        try:
            unindexed = _scan_resolved_unindexed_tasks()
            if unindexed:
                logger.info(f"Found {len(unindexed)} unindexed resolved task(s)")
                for task in unindexed:
                    if stop_event.is_set():
                        break
                    result = await _index_resolved_task(task["id"])
                    logger.info(f"  Knowledge indexed #{result['task_id']}: {result['status']}")
            else:
                logger.debug("No unindexed resolved tasks")
        except Exception as e:
            logger.warning(f"Knowledge worker scan failed: {e}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Knowledge worker stopped")


def diagnosis_worker_start() -> tuple[asyncio.Task, asyncio.Event]:
    """启动诊断 worker，返回 (task, stop_event) 供 lifespan shutdown 使用。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_diagnosis_worker(stop_event))
    return task, stop_event
