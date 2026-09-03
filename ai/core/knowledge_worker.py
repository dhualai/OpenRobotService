"""知识沉淀 Worker（core 共享层）—— 工单解决后自动写入 Qdrant 知识库

从任务 Agent 平台仓库 `ai/agents/AiTaskPlatform/services/diagnosis_worker.py` 的"知识沉淀 Service"
下沉合并到 `ai/core`，作为所有 Agent（任务 Agent / 智能派单等）共用的沉淀基建。

职责（与旧版一致，仅解耦平台依赖）：
  - `run_knowledge_worker`：后台定时扫描"已解决/已关闭但未沉淀"的工单 → 写入 Qdrant（project domain）。
  - `_scan_resolved_unindexed_tasks`：扫 tasks 表，挑 `metadata_info.solution_indexed != True` 的已结案工单。
  - `_extract_solution_text`：从工单评论提取根因(U老师诊断) + 解决步骤(最后人类评论)。
  - `_index_resolved_task`：单工单沉淀入 `task_resolutions`，成功后标记 `solution_indexed=True`。

解耦说明：
  - 旧版依赖 `AiTaskAgent`（平台类）取 retriever；本版直接 `ai.core.retrieval.get_retrieval_service()`，
    不 import 任何平台类型，故可被所有 Agent 复用。
  - DB：沿用 `ai/core/task_adapter.py` 同款后端模型/会话（`app.models.task` + `app.core.db`）。

启动位置：`ai/run.py` lifespan 已引用 `from ai.core.knowledge_worker import run_knowledge_worker`。
"""

import asyncio
import re
import time

from ai.config import get_ai_config
from ai.core.logging import get_logger

logger = get_logger("KNOWLEDGE_WORKER")


# ============================================================
# 扫描：找出已解决/已关闭但尚未沉淀的工单
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


# ============================================================
# 提取：从评论里抽出根因 + 解决步骤
# ============================================================

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


# ============================================================
# 沉淀：单工单写入 task_resolutions（project domain）
# ============================================================

async def _index_resolved_task(task_id: int) -> dict:
    """将已解决工单沉淀到 Qdrant（project domain / task_resolutions）。

    解耦：不再依赖 AiTaskAgent，统一走 core 的 get_retrieval_service()。
    """
    from ai.core.retrieval import get_retrieval_service
    from ai.core.task_adapter import load_task_context_dict

    d = load_task_context_dict(task_id)
    title = d.get("title", f"工单 #{task_id}") or f"工单 #{task_id}"
    root_cause, solution_steps = _extract_solution_text(task_id)

    try:
        retriever = await get_retrieval_service()
        await retriever.index_task_resolution(
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


# ============================================================
# Worker 主循环
# ============================================================

async def run_knowledge_worker(stop_event: asyncio.Event):
    """后台知识沉淀 worker：扫描已解决工单 → 写入 Qdrant（core 统一入口）"""
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


def start_knowledge_worker() -> tuple[asyncio.Task, asyncio.Event]:
    """启动知识沉淀 worker，返回 (task, stop_event) 供 lifespan shutdown 使用。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_knowledge_worker(stop_event))
    return task, stop_event
