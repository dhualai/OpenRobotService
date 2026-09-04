"""工单讨论评论的读取与落库（从 pipeline.py 拆分，独立成模块）

职责：
  - _load_discussion: 读取讨论评论（含工程师 + U老师/AI 历史分析，标注来源）
  - add_diagnosis_comment / add_diagnosis_comment_short: 把 AI 结果写入 task_comments
  - notify_backend_comment_broadcast: 写库后回调后端 WS 广播

依赖：TaskComment 模型 + SessionLocal（后端 DB）。
不依赖 AiTaskAgent 实例状态，可独立使用。
"""

from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


def load_discussion(task_id: str, limit: int = 20) -> str:
    """读取工单讨论评论（含工程师与 U老师/AI 的历史分析），返回格式化文本。

    - 工程师评论 → 作第一手事实线索
    - U老师(AI) 评论 → 保留但标注"AI历史分析，仅供参考"，避免回声放大、又不错失已有排查进展
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            comments = db.query(TaskComment).filter(
                TaskComment.task_id == int(task_id)
            ).order_by(TaskComment.created_at.asc()).all()
        finally:
            db.close()
    except Exception:
        return ""

    lines = []
    for c in comments:
        author = getattr(c, 'created_by_name', None) or getattr(c, 'created_by', None) or "?"
        content = str(getattr(c, 'content', "") or "")[:300]
        if not content.strip():
            continue
        if author == "U老师":
            lines.append(f"[U老师(AI历史分析，仅供参考)] {content}")
        else:
            lines.append(f"[{author}] {content}")
    return "\n".join(lines[-limit:])


def add_diagnosis_comment(task_id: int, draft, created_by: str = "U老师") -> bool:
    """将 AI 诊断结果写入 task_comments 表。

    Args:
        draft: 支持 .root_cause_analysis 和 .suggested_actions 的对象
    """
    from app.models.task import TaskComment
    from app.core.db import SessionLocal

    content_parts = []
    if getattr(draft, 'root_cause_analysis', None):
        content_parts.append(draft.root_cause_analysis)
    if getattr(draft, 'suggested_actions', None):
        content_parts.append("")
        if len(draft.suggested_actions) == 1:
            content_parts.append(f"> {draft.suggested_actions[0]}")
        else:
            for action in draft.suggested_actions:
                content_parts.append(f"- {action}")

    db = SessionLocal()
    try:
        comment = TaskComment(
            task_id=task_id,
            content="\n".join(content_parts),
            created_by=created_by,
            is_public=True,
        )
        db.add(comment)
        db.commit()
        return True
    except Exception as e:
        logger.warning(f"Diagnosis comment failed: {e}")
        return False
    finally:
        db.close()


def add_diagnosis_comment_short(task_id: int, content: str) -> Optional[int]:
    """简短回复写入 task_comments（用于 @AI 讨论/摘要/诊断），返回 comment_id。

    写库后回调后端 WS 广播（实时上屏，best-effort）。
    """
    from app.models.task import TaskComment
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        comment = TaskComment(task_id=task_id, content=content,
                              created_by="U老师", is_public=True)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        cid = comment.id
    except Exception:
        db.rollback()
        return None
    finally:
        db.close()
    # 实时推送：写库后回调后端 WS 广播（跨进程 pub-sub，best-effort 不阻塞主流程）
    try:
        notify_backend_comment_broadcast(task_id, cid)
    except Exception:
        pass
    return cid


def notify_backend_comment_broadcast(task_id: int, comment_id: int) -> None:
    """写库后回调后端 WS 广播端点（跨进程 pub-sub，best-effort 不阻塞主流程）。"""
    import asyncio
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-comment"
        headers = {"X-API-Key": cfg.internal_api_key}

        async def _post() -> None:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(url, json={"comment_id": comment_id}, headers=headers)

        async def _run() -> None:
            await _post()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.run_in_executor(None, lambda: asyncio.run(_run()))
        else:
            import threading
            threading.Thread(target=lambda: asyncio.run(_run()), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 评论广播回调失败 task_id={task_id} comment_id={comment_id}: {e}")


def notify_backend_ai_progress(task_id, run_id, todos, phase: str = "running") -> None:
    """跨进程把 AI 执行过程广播进该工单 WS 房间（ai.progress 事件，best-effort）。

    用途：在前端动态展示"正在做哪一步/已完成哪步"，最终回复不含过程块。
    """
    import asyncio
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-ai-progress"
        headers = {"X-API-Key": cfg.internal_api_key}
        payload = {"run_id": run_id, "phase": phase, "todos": todos or []}

        async def _post() -> None:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.post(url, json=payload, headers=headers)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            loop.run_in_executor(None, lambda: asyncio.run(_post()))
        else:
            import threading
            threading.Thread(target=lambda: asyncio.run(_post()), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 进度广播回调失败 task_id={task_id}: {e}")


async def notify_backend_ai_progress_await(task_id, run_id, todos, phase: str) -> None:
    """跨进程广播 AI 进度（await 版本）：确保 done 信号一定送达后端。

    供收尾阶段使用（如讨论完成时发 phase=done 让前端收起执行过程），
    避免 best-effort 线程模式偶发未送达导致前端"一直转不停"。
    """
    import httpx
    from ai.config import get_ai_config

    try:
        cfg = get_ai_config()
        if not cfg.backend_base_url or not cfg.internal_api_key:
            return
        url = f"{cfg.backend_base_url.rstrip('/')}/api/tasks/{task_id}/internal/broadcast-ai-progress"
        headers = {"X-API-Key": cfg.internal_api_key}
        payload = {"run_id": run_id, "phase": phase, "todos": todos or []}
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(url, json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"AI 进度收尾广播失败 task_id={task_id}: {e}")
