from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.core.user_identity import is_admin_user
from app.core.config import settings
from app.models.identity import UserDB
from app.models.knowledge import KnowledgeLike, KnowledgeLikeEvent
from app.models.knowledge_challenge import (
    KnowledgeChallenge, KnowledgeChallengeAnswer, KnowledgeChallengeAudit,
    KnowledgeChallengeLike, KnowledgeChallengeLikeEvent,
)
from app.models.task import Task
from app.models.wxpay import RewardRecord
from app.modules.admin.services import knowledge_service as knowledge
from app.modules.admin.services.knowledge_like_quota import check_question_quota
from app.modules.wxpay.schemas import GrantRewardIn
from app.modules.wxpay.service import RewardService


def utcnow():
    return knowledge.utcnow()


def enabled():
    return settings.KNOWLEDGE_CHALLENGES_ENABLED


def ensure_enabled():
    if not enabled():
        raise HTTPException(409, "高价值问题专区暂未开放新参与")


def can_manage(actor):
    return is_admin_user(actor) or actor.get("can_manage_knowledge_challenges") is True


def require_admin(actor):
    if not can_manage(actor):
        raise HTTPException(403, "仅超级管理员或管理员可管理高价值问题")


def utc(value):
    if value.tzinfo is None:
        raise HTTPException(422, "截止时间必须包含时区")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


async def get_challenge(db, challenge_id, actor=None, lock=False):
    query = select(KnowledgeChallenge).where(KnowledgeChallenge.id == challenge_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    challenge = (await db.execute(query)).scalar_one_or_none()
    if not challenge or ((challenge.hidden or challenge.state == "draft") and not can_manage(actor or {})):
        raise HTTPException(404, "问题不存在或暂不可见")
    return challenge


def accepting(challenge):
    return enabled() and challenge.state == "active" and not challenge.hidden and utcnow() < challenge.ends_at


def ensure_open(challenge):
    if not accepting(challenge):
        raise HTTPException(409, "问题已截止、暂停或下架，不能继续答题或点赞")


def expect_version(challenge, version):
    if version != challenge.version:
        raise HTTPException(409, "问题状态已变化，请刷新后重试")


async def public_text(db, challenge, text, author_id=None):
    identities = {challenge.publisher_id, challenge.questioner_id, author_id}
    identities.update((await db.execute(select(KnowledgeChallengeAnswer.author_id).where(
        KnowledgeChallengeAnswer.challenge_id == challenge.id))).scalars().all())
    if challenge.task_id is not None:
        task = await db.get(Task, challenge.task_id)
        if task:
            text = await knowledge.anonymous_text(db, task, text)
    identities.discard(None)
    users = (await db.execute(select(UserDB).where(UserDB.id.in_(identities)))).scalars().all()
    for user in users:
        for term in (user.name, user.username, user.id, user.wechat_openid):
            if term and len(term) >= 2:
                text = text.replace(term, "[匿名]")
    text = re.sub(r"https?://\S+", "[链接已隐藏]", text)
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", "[邮箱已隐藏]", text)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[电话已隐藏]", text)


def audit(db, challenge, operator_id, action, reason, before):
    db.add(KnowledgeChallengeAudit(challenge_id=challenge.id, operator_id=operator_id,
        action=action, reason=reason, before=before,
        after={"state": challenge.state, "best_answer_id": challenge.best_answer_id,
               "hidden": challenge.hidden, "version": challenge.version,
               "ends_at": knowledge.iso(challenge.ends_at)}, created_at=utcnow()))


async def source_task(db, actor, task_id):
    require_admin(actor)
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "来源工单不存在")
    return {"title": await knowledge.anonymous_text(db, task, task.title or ""),
            "content": await knowledge.anonymous_text(db, task, task.description or "")}


async def create(db, actor, body):
    require_admin(actor)
    ensure_enabled()
    user_id = await knowledge.actor_id(db, actor, lock=True)
    ends_at = utc(body.ends_at)
    fingerprint = hashlib.sha256(json.dumps({"task_id": body.task_id, "title": body.title,
        "content": body.content, "bounty_fen": body.bounty_fen, "ends_at": knowledge.iso(ends_at)},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = (await db.execute(select(KnowledgeChallenge).where(
        KnowledgeChallenge.request_key == body.request_key))).scalar_one_or_none()
    if existing:
        if existing.publisher_id != user_id:
            raise HTTPException(409, "发布请求标识已被使用")
        if existing.creation_fingerprint != fingerprint:
            raise HTTPException(409, "发布内容已变化，请在管理问题中查看原草稿，或关闭表单后重新发布")
        return await detail(db, actor, existing.id)
    now = utcnow()
    if ends_at <= now:
        raise HTTPException(422, "截止时间必须晚于当前时间")
    task = None
    if body.task_id is not None:
        task = (await db.execute(select(Task).where(Task.id == body.task_id).with_for_update())).scalar_one_or_none()
        if not task:
            raise HTTPException(404, "来源工单不存在")
        duplicate = (await db.execute(select(KnowledgeChallenge.id).where(
            KnowledgeChallenge.task_id == task.id))).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(409, "该工单已有高价值问题，请管理原问题，不能重复发布")
    identities = await knowledge.users_by_identity(db) if task else {}
    questioner = identities.get(task.created_by) if task else None
    challenge = KnowledgeChallenge(task_id=task.id if task else None,
        questioner_id=questioner.id if questioner else None, publisher_id=user_id,
        request_key=body.request_key, creation_fingerprint=fingerprint, title=body.title, content=body.content,
        bounty_fen=body.bounty_fen, ends_at=ends_at, state="draft", version=1,
        hidden=False, reward_attention=False, rule_version="likes-first-submit-v1", created_at=now)
    db.add(challenge)
    await db.flush()
    audit(db, challenge, user_id, "create", "创建问题草稿", {})
    await db.commit()
    return await detail(db, actor, challenge.id)


async def publish(db, actor, challenge_id, version):
    require_admin(actor)
    ensure_enabled()
    user_id = await knowledge.actor_id(db, actor)
    challenge = await get_challenge(db, challenge_id, actor, lock=True)
    if challenge.state != "draft":
        return await detail(db, actor, challenge.id)
    expect_version(challenge, version)
    if challenge.ends_at <= utcnow():
        raise HTTPException(422, "截止时间已过，请重新创建有效期限的问题")
    challenge.title = await public_text(db, challenge, challenge.title)
    challenge.content = await public_text(db, challenge, challenge.content)
    task = await db.get(Task, challenge.task_id) if challenge.task_id is not None else None
    if challenge.task_id is not None and not task:
        raise HTTPException(409, "关联工单已不存在，不能发布")
    challenge.first_published_at = utcnow()
    challenge.asked_at = task.created_at if task else challenge.first_published_at
    challenge.state = "active"
    challenge.version += 1
    audit(db, challenge, user_id, "publish", "发布问题及悬赏", {"state": "draft"})
    await db.commit()
    return await detail(db, actor, challenge.id)


def like_total_expression():
    task_votes = select(func.count()).select_from(KnowledgeLike).where(
        KnowledgeLike.task_id == KnowledgeChallenge.task_id, KnowledgeLike.active.is_(True),
        KnowledgeLike.risk_status == "valid").correlate(KnowledgeChallenge).scalar_subquery()
    independent_votes = select(func.count()).select_from(KnowledgeChallengeLike).where(
        KnowledgeChallengeLike.target_id == KnowledgeChallenge.id,
        KnowledgeChallengeLike.target_kind == "question", KnowledgeChallengeLike.active.is_(True)
    ).correlate(KnowledgeChallenge).scalar_subquery()
    return task_votes + independent_votes


async def question_liked(db, challenge, user_id):
    if challenge.task_id is not None:
        row = await db.get(KnowledgeLike, (challenge.task_id, user_id))
        return bool(row and row.active and row.risk_status == "valid")
    row = await db.get(KnowledgeChallengeLike, ("question", challenge.id, user_id))
    return bool(row and row.active)


async def can_like_question(db, challenge, user_id):
    if not accepting(challenge) or user_id == challenge.questioner_id:
        return False
    if challenge.task_id is not None:
        task = await db.get(Task, challenge.task_id)
        user = await db.get(UserDB, user_id)
        identities = {user.id, user.username, user.wechat_openid} if user else {user_id}
        identities.discard(None)
        if task and identities.intersection({task.created_by, task.assigned_to, task.archived_by}):
            return False
    return True


async def card(db, actor, challenge, user_id, votes=None):
    if challenge.snapshot is not None:
        votes = challenge.snapshot.get("question_likes", 0)
    if votes is None:
        votes = (await db.execute(select(like_total_expression()).where(
            KnowledgeChallenge.id == challenge.id))).scalar_one()
    state = "settling" if challenge.state in ("active", "paused") and challenge.ends_at <= utcnow() else challenge.state
    return {"id": challenge.id, "title": challenge.title, "bounty_fen": challenge.bounty_fen,
        "ends_at": knowledge.iso(challenge.ends_at), "asked_at": knowledge.iso(challenge.asked_at),
        "state": state, "hidden": challenge.hidden, "version": challenge.version,
        "source": "task" if challenge.task_id is not None else "standalone",
        "questioner": "匿名提问人" if challenge.task_id is not None else "平台发布",
        "like_count": votes, "liked_by_me": await question_liked(db, challenge, user_id),
        "can_like": await can_like_question(db, challenge, user_id),
        "can_answer": accepting(challenge), "can_manage_state": can_manage(actor)}


async def listing(db, actor, view="active", page=1, size=20):
    user_id = await knowledge.actor_id(db, actor)
    query = select(KnowledgeChallenge, like_total_expression().label("votes"))
    if view == "manage":
        require_admin(actor)
    else:
        query = query.where(KnowledgeChallenge.hidden.is_(False), KnowledgeChallenge.state != "draft")
        if view == "active":
            query = query.where(KnowledgeChallenge.state == "active", KnowledgeChallenge.ends_at > utcnow())
        elif view == "mine":
            query = query.where(KnowledgeChallenge.id.in_(select(KnowledgeChallengeAnswer.challenge_id).where(
                KnowledgeChallengeAnswer.author_id == user_id)))
        else:
            query = query.where(or_(KnowledgeChallenge.ends_at <= utcnow(),
                KnowledgeChallenge.state.in_(("completed", "unanswered", "rejected", "cancelled"))))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (await db.execute(query.order_by(like_total_expression().desc(),
        KnowledgeChallenge.first_published_at.desc(), KnowledgeChallenge.id.asc())
        .offset((page - 1) * size).limit(size))).all()
    return {"items": [await card(db, actor, row, user_id, votes) for row, votes in rows],
            "total": total, "can_manage": can_manage(actor)}


async def answer_votes(db, answer_id):
    return (await db.execute(select(func.count()).select_from(KnowledgeChallengeLike).where(
        KnowledgeChallengeLike.target_kind == "answer", KnowledgeChallengeLike.target_id == answer_id,
        KnowledgeChallengeLike.active.is_(True)))).scalar_one()


async def detail(db, actor, challenge_id):
    user_id = await knowledge.actor_id(db, actor)
    challenge = await get_challenge(db, challenge_id, actor)
    result = await card(db, actor, challenge, user_id)
    answers = (await db.execute(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge.id,
        KnowledgeChallengeAnswer.submitted_at.is_not(None), KnowledgeChallengeAnswer.hidden.is_(False)
    ).order_by(KnowledgeChallengeAnswer.id))).scalars().all()
    frozen = {entry["id"]: entry for entry in (challenge.snapshot or {}).get("answers", [])}
    items = []
    for position, answer in enumerate(answers):
        entry = frozen.get(answer.id)
        liked = await db.get(KnowledgeChallengeLike, ("answer", answer.id, user_id))
        items.append({"id": answer.id, "author": f"匿名解答者{position + 1}",
            "content": entry["content"] if entry else answer.content,
            "like_count": entry["likes"] if entry else await answer_votes(db, answer.id),
            "submitted_at": knowledge.iso(answer.submitted_at), "is_mine": answer.author_id == user_id,
            "is_best": answer.id == challenge.best_answer_id,
            "liked_by_me": bool(liked and liked.active),
            "can_like": accepting(challenge) and answer.author_id != user_id})
    items.sort(key=lambda entry: (-entry["like_count"], entry["submitted_at"], entry["id"]))
    reward = (await db.execute(select(RewardRecord).where(RewardRecord.source_type == "knowledge_challenge",
        RewardRecord.source_id == str(challenge.id)))).scalar_one_or_none()
    result.update(content=challenge.content, answers=items,
        best_answer=next((entry for entry in items if entry["is_best"]), None),
        knowledge_source="community", professionally_verified=False,
        my_reward_status=reward.status if reward and reward.user_id == user_id else None)
    if can_manage(actor):
        result.update(task_id=challenge.task_id, reward_attention=challenge.reward_attention,
            reward_status=reward.status if reward else None, last_error=challenge.last_error)
    return result


async def my_answer(db, actor, challenge_id):
    user_id = await knowledge.actor_id(db, actor)
    await get_challenge(db, challenge_id, actor)
    answer = (await db.execute(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge_id,
        KnowledgeChallengeAnswer.author_id == user_id))).scalar_one_or_none()
    return {"content": (answer.draft_content if answer.draft_content is not None else answer.content) if answer else "",
        "version": answer.version if answer else 0,
        "submitted_at": knowledge.iso(answer.submitted_at) if answer else None}


async def community_result(db, actor, challenge_id):
    result = await detail(db, actor, challenge_id)
    if result["state"] != "completed" or not result["best_answer"]:
        raise HTTPException(404, "该问题暂无有效社区优选结果")
    return result


async def write_answer(db, actor, challenge_id, content, version, submit=False):
    user_id = await knowledge.actor_id(db, actor)
    challenge = await get_challenge(db, challenge_id, actor, lock=True)
    ensure_open(challenge)
    answer = (await db.execute(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge.id,
        KnowledgeChallengeAnswer.author_id == user_id).with_for_update())).scalar_one_or_none()
    if version != (answer.version if answer else 0):
        raise HTTPException(409, "答案已在其他页面更新，请刷新后重试")
    if submit and not content.strip():
        raise HTTPException(422, "请填写答案内容")
    if not answer:
        answer = KnowledgeChallengeAnswer(challenge_id=challenge.id, author_id=user_id,
            content="", version=0, history=[], updated_at=utcnow())
        db.add(answer)
    if answer.hidden:
        raise HTTPException(409, "该答案已隐藏，请联系管理员")
    if submit and answer.submitted_at is not None:
        answer.history = [*answer.history, {"version": answer.version,
            "content": answer.content, "at": knowledge.iso(utcnow())}]
    answer.version += 1
    answer.updated_at = utcnow()
    answer.draft_content = content
    if submit:
        answer.content = await public_text(db, challenge, content.strip(), user_id)
        answer.draft_content = None
        answer.submitted_at = answer.submitted_at or utcnow()
    ensure_open(challenge)
    await db.commit()
    return await my_answer(db, actor, challenge.id)


async def set_like(db, actor, challenge_id, active, answer_id=None):
    user_id = await knowledge.actor_id(db, actor, lock=True)
    challenge = await get_challenge(db, challenge_id, actor, lock=True)
    ensure_open(challenge)
    now = utcnow()
    kind, target_id = ("answer", answer_id) if answer_id is not None else ("question", challenge.id)
    if answer_id is not None:
        answer = await db.get(KnowledgeChallengeAnswer, answer_id)
        if not answer or answer.challenge_id != challenge.id or not answer.submitted_at or answer.hidden:
            raise HTTPException(404, "答案不存在")
        if answer.author_id == user_id:
            raise HTTPException(403, "不能给自己的答案点赞")
    elif not await can_like_question(db, challenge, user_id):
        raise HTTPException(403, "不能给本人参与的工单问题点赞计奖")
    if kind == "question" and challenge.task_id is not None:
        row = await db.get(KnowledgeLike, (challenge.task_id, user_id))
        if row and row.risk_status != "valid":
            raise HTTPException(409, "该点赞正在核验或已判无效")
    else:
        row = await db.get(KnowledgeChallengeLike, (kind, target_id, user_id))
    if active and (not row or not row.active):
        if kind == "question":
            key = f"task:{challenge.task_id}" if challenge.task_id is not None else f"challenge:{challenge.id}"
            await check_question_quota(db, user_id, key, now)
        else:
            start = now.replace(tzinfo=timezone.utc).astimezone(knowledge.BEIJING).replace(
                hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).replace(tzinfo=None)
            used = set((await db.execute(select(KnowledgeChallengeLikeEvent.target_id).where(
                KnowledgeChallengeLikeEvent.user_id == user_id, KnowledgeChallengeLikeEvent.target_kind == "answer",
                KnowledgeChallengeLikeEvent.active.is_(True), KnowledgeChallengeLikeEvent.created_at >= start,
                KnowledgeChallengeLikeEvent.created_at < start + timedelta(days=1)).distinct())).scalars().all())
            if target_id not in used and len(used) >= 5:
                raise HTTPException(429, "今日已点赞5条不同答案，明日再来")
    if not row:
        if kind == "question" and challenge.task_id is not None:
            row = KnowledgeLike(task_id=challenge.task_id, user_id=user_id, active=False,
                risk_status="valid", updated_at=now)
        else:
            row = KnowledgeChallengeLike(target_kind=kind, target_id=target_id, user_id=user_id, active=False)
        db.add(row)
    if row.active != active:
        row.active = active
        if kind == "question" and challenge.task_id is not None:
            row.updated_at = now
            db.add(KnowledgeLikeEvent(task_id=challenge.task_id, user_id=user_id, active=active, created_at=now))
        else:
            db.add(KnowledgeChallengeLikeEvent(target_kind=kind, target_id=target_id,
                user_id=user_id, active=active, created_at=now))
    ensure_open(challenge)
    await db.commit()
    return await detail(db, actor, challenge.id)


async def settle(db, challenge_id):
    if not enabled():
        return False
    challenge = await get_challenge(db, challenge_id, {"is_admin": True}, lock=True)
    if challenge.state not in ("active", "paused") or challenge.ends_at > utcnow():
        return False
    answers = (await db.execute(select(KnowledgeChallengeAnswer).where(
        KnowledgeChallengeAnswer.challenge_id == challenge.id,
        KnowledgeChallengeAnswer.submitted_at < challenge.ends_at,
        KnowledgeChallengeAnswer.hidden.is_(False)))).scalars().all()
    entries = [{"id": answer.id, "content": answer.content, "submitted_at": knowledge.iso(answer.submitted_at),
        "likes": await answer_votes(db, answer.id)} for answer in answers]
    entries.sort(key=lambda entry: (-entry["likes"], entry["submitted_at"], entry["id"]))
    challenge.snapshot = {"cutoff": knowledge.iso(challenge.ends_at), "rule_version": challenge.rule_version,
        "answers": entries, "question_likes": (await card(db, {}, challenge, ""))["like_count"]}
    challenge.default_answer_id = entries[0]["id"] if entries else None
    challenge.best_answer_id = challenge.default_answer_id
    challenge.state = "completed" if entries else "unanswered"
    challenge.settled_at, challenge.last_error = utcnow(), None
    challenge.version += 1
    if entries:
        winner = next(answer for answer in answers if answer.id == challenge.best_answer_id)
        user = await db.get(UserDB, winner.author_id)
        await RewardService.grant(db, GrantRewardIn(user_id=winner.author_id,
            openid=user.wechat_openid or user.id, amount=challenge.bounty_fen,
            scene_name="高价值问题", reward_desc=f"高价值问题#{challenge.id}：{challenge.title[:64]}",
            source_type="knowledge_challenge", source_id=str(challenge.id)), role="answerer", auto_commit=False)
    audit(db, challenge, "SYSTEM", "settle", "到期按有效点赞及首次提交时间自动结算", {"state": "active_or_paused"})
    await db.commit()
    return True


async def change_state(db, actor, challenge_id, body):
    require_admin(actor)
    user_id = await knowledge.actor_id(db, actor)
    challenge = await get_challenge(db, challenge_id, actor, lock=True)
    expect_version(challenge, body.version)
    before = {"state": challenge.state, "hidden": challenge.hidden, "ends_at": knowledge.iso(challenge.ends_at)}
    if body.action in ("hide", "unhide"):
        challenge.hidden = body.action == "hide"
    elif challenge.state in ("active", "paused") and challenge.ends_at > utcnow():
        if body.action == "pause" and challenge.state == "active":
            challenge.state = "paused"
        elif body.action == "resume" and challenge.state == "paused":
            challenge.state = "active"
        elif body.action == "cancel":
            challenge.state = "cancelled"
        elif body.action == "extend" and body.ends_at and utc(body.ends_at) > challenge.ends_at:
            challenge.ends_at = utc(body.ends_at)
        else:
            raise HTTPException(409, "当前状态不能执行此操作，延期必须晚于原截止时间")
    else:
        raise HTTPException(409, "已截止或当前状态不允许修改征集状态")
    challenge.version += 1
    audit(db, challenge, user_id, body.action, body.reason, before)
    await db.commit()
    return await detail(db, actor, challenge.id)


async def override(db, actor, challenge_id, body):
    require_admin(actor)
    user_id = await knowledge.actor_id(db, actor)
    challenge = await get_challenge(db, challenge_id, actor, lock=True)
    expect_version(challenge, body.version)
    if challenge.state not in ("completed", "rejected") or not challenge.snapshot:
        raise HTTPException(409, "请等待自动结算完成后再调整结果")
    participant = (await db.execute(select(KnowledgeChallengeAnswer.id).where(
        KnowledgeChallengeAnswer.challenge_id == challenge.id,
        KnowledgeChallengeAnswer.author_id == user_id,
        KnowledgeChallengeAnswer.submitted_at.is_not(None)))).scalar_one_or_none()
    if participant or challenge.questioner_id == user_id:
        raise HTTPException(403, "参与者不能调整本题结果，请由其他管理员处理")
    before = {"state": challenge.state, "best_answer_id": challenge.best_answer_id}
    winner = None
    if body.action != "reject":
        candidate_id = body.answer_id or challenge.default_answer_id
        if candidate_id not in {entry["id"] for entry in challenge.snapshot["answers"]}:
            raise HTTPException(422, "只能选择截止快照中的合规答案")
        winner = await db.get(KnowledgeChallengeAnswer, candidate_id)
        if not winner or winner.hidden:
            raise HTTPException(422, "该答案已不可用")
    challenge.best_answer_id = winner.id if winner else None
    challenge.state = "completed" if winner else "rejected"
    reward = (await db.execute(select(RewardRecord).where(RewardRecord.source_type == "knowledge_challenge",
        RewardRecord.source_id == str(challenge.id)).with_for_update())).scalar_one_or_none()
    challenge.reward_attention = bool(reward and (not winner or reward.user_id != winner.author_id))
    challenge.version += 1
    audit(db, challenge, user_id, body.action, body.reason, before)
    await db.commit()
    return await detail(db, actor, challenge.id)
