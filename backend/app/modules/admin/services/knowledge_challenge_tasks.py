import asyncio
import logging

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.knowledge_challenge import KnowledgeChallenge
from app.modules.admin.services.knowledge_challenge import enabled, settle, utcnow


logger = logging.getLogger(__name__)
_scheduler_task = None


async def run_once():
    if not enabled():
        return
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(KnowledgeChallenge.id).where(
            KnowledgeChallenge.state.in_(("active", "paused")),
            KnowledgeChallenge.ends_at <= utcnow()).order_by(KnowledgeChallenge.ends_at).limit(100))).scalars().all()
    for challenge_id in ids:
        async with AsyncSessionLocal() as db:
            try:
                await settle(db, challenge_id)
            except Exception:
                await db.rollback()
                logger.exception("高价值问题 %s 自动结算失败，将重试", challenge_id)
                challenge = await db.get(KnowledgeChallenge, challenge_id)
                if challenge and challenge.state in ("active", "paused"):
                    challenge.last_error = "自动结算失败，后台将重试，请检查服务日志"
                    await db.commit()


async def scheduler_loop():
    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("高价值问题到期扫描失败，将重试")
        await asyncio.sleep(60)


def start_scheduler():
    global _scheduler_task
    if not enabled():
        return
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(scheduler_loop())


def stop_scheduler():
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
    _scheduler_task = None
