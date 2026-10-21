from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.knowledge import KnowledgeLikeEvent
from app.models.knowledge_challenge import KnowledgeChallengeLikeEvent


async def check_question_quota(db, user_id, target_key, now):
    day_start = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=8))).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).replace(tzinfo=None)
    task_ids = (await db.execute(select(KnowledgeLikeEvent.task_id).where(
        KnowledgeLikeEvent.user_id == user_id, KnowledgeLikeEvent.active.is_(True),
        KnowledgeLikeEvent.created_at >= day_start,
        KnowledgeLikeEvent.created_at < day_start + timedelta(days=1),
    ).distinct())).scalars().all()
    challenge_ids = (await db.execute(select(KnowledgeChallengeLikeEvent.target_id).where(
        KnowledgeChallengeLikeEvent.user_id == user_id,
        KnowledgeChallengeLikeEvent.target_kind == "question",
        KnowledgeChallengeLikeEvent.active.is_(True),
        KnowledgeChallengeLikeEvent.created_at >= day_start,
        KnowledgeChallengeLikeEvent.created_at < day_start + timedelta(days=1),
    ).distinct())).scalars().all()
    keys = {f"task:{task_id}" for task_id in task_ids} | {f"challenge:{value}" for value in challenge_ids}
    if target_key not in keys and len(keys) >= 5:
        raise HTTPException(429, "今日已点赞5个不同问题，不能再点赞新问题（北京时间每日0点重置）")
