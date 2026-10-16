from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.user_identity import is_admin_user
from app.models.identity import UserDB
from app.models.knowledge import (
    ArchiveReviewDecision, KnowledgeLike, KnowledgeLikeEvent,
    KnowledgePublication, KnowledgeSettlement, KnowledgeDiscussion,
)
from app.models.task import ArchiveReport, Task, TaskStatus


BEIJING = timezone(timedelta(hours=8))
CAMPAIGN_START = datetime(2026, 9, 30, 16)
CAMPAIGN_END = datetime(2026, 12, 31, 16)
CAMPAIGN_MONTHS = ("2026-10", "2026-11", "2026-12")
DAILY_QUESTION_LIKE_LIMIT = 5


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def month_bounds(month):
    if month not in CAMPAIGN_MONTHS:
        raise HTTPException(422, f"请选择活动月份：{'、'.join(CAMPAIGN_MONTHS)}")
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=BEIJING)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start.astimezone(timezone.utc).replace(tzinfo=None), end.astimezone(timezone.utc).replace(tzinfo=None)


def selected_month(month=None, now=None):
    current = (now or utcnow()).replace(tzinfo=timezone.utc).astimezone(BEIJING).strftime("%Y-%m")
    result = month or max(CAMPAIGN_MONTHS[0], min(current, CAMPAIGN_MONTHS[-1]))
    month_bounds(result)
    return result


async def actor_id(db, actor, lock=False):
    raw = str(actor.get("id") or actor.get("user_id") or actor.get("username") or "")
    query = select(UserDB).where(
        (UserDB.id == raw) | (UserDB.username == raw)
    )
    if lock:
        query = query.with_for_update()
    user = (await db.execute(query)).scalar_one_or_none()
    if not user:
        raise HTTPException(403, "用户身份未关联，请联系管理员")
    return user.id


async def users_by_identity(db):
    users = (await db.execute(select(UserDB))).scalars().all()
    return {key: user for user in users for key in (user.id, user.username, user.wechat_openid) if key}


async def actor_identities(db, actor):
    user = await db.get(UserDB, await actor_id(db, actor))
    return {identity for identity in (user.id, user.username, user.wechat_openid) if identity}


def display_user(user):
    return {"user_id": user.id, "name": user.name or user.username,
            "avatar_resource_id": user.avatar_resource_id}


async def anonymous_text(db, task, text, hide_participants=True):
    identities = [task.archived_by]
    reports = (await db.execute(select(ArchiveReport).where(ArchiveReport.task_id == task.id))).scalars().all()
    identities += [report.reviewer for report in reports]
    identities += list((await db.execute(select(ArchiveReviewDecision.reviewer_id).where(
        ArchiveReviewDecision.task_id == task.id
    ))).scalars().all())
    if hide_participants:
        identities += [task.created_by, task.assigned_to]
        identities += [report.created_by for report in reports]
    users = await users_by_identity(db)
    terms = [task.project_name, task.customer, task.team]
    for identity in identities:
        user = users.get(identity)
        terms += [identity, user.id if user else None, user.name if user else None,
                  user.username if user else None, user.wechat_openid if user else None]
    value = text or ""
    for term in sorted({str(term) for term in terms if term and len(str(term)) > 1}, key=len, reverse=True):
        value = re.sub(re.escape(term), "（已匿名）", value, flags=re.IGNORECASE)
    value = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "[附件链接已隐藏]", value)
    value = re.sub(r"https?://\S+", "[链接已隐藏]", value)
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱已隐藏]", value)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[联系方式已隐藏]", value)


async def publish_report(db, task, report, now=None):
    now = now or utcnow()
    if not (CAMPAIGN_START <= task.created_at < CAMPAIGN_END and CAMPAIGN_START <= now < CAMPAIGN_END):
        return
    users = await users_by_identity(db)
    questioner, answerer = users.get(task.created_by), users.get(report.created_by)
    if not questioner or not answerer:
        raise HTTPException(422, "提单人或解答人身份未关联，不能发布活动问题")
    publication = await db.get(KnowledgePublication, task.id)
    values = {
        "title": await anonymous_text(db, task, task.title, False),
        "problem": await anonymous_text(db, task, task.description, False),
        "content": await anonymous_text(db, task, report.content, False),
        "review_comment": await anonymous_text(db, task, report.review_comment, False),
        "value_point": await anonymous_text(db, task, report.value_point, False),
    }
    if not publication:
        publication = KnowledgePublication(task_id=task.id, first_approved_at=now,
            questioner_id=questioner.id, answerer_id=answerer.id)
        db.add(publication)
    publication.report_id = report.id
    for field, value in values.items():
        setattr(publication, field, value)
    publication.active = True
    await db.flush()


async def eligible_publications(db, month=None):
    query = select(KnowledgePublication).join(Task, Task.id == KnowledgePublication.task_id).join(
        ArchiveReport, ArchiveReport.id == KnowledgePublication.report_id
    ).where(KnowledgePublication.active.is_(True), Task.status == TaskStatus.ARCHIVED,
            ArchiveReport.submit_status == "approved", Task.created_at >= CAMPAIGN_START,
            Task.created_at < CAMPAIGN_END, KnowledgePublication.first_approved_at >= CAMPAIGN_START,
            KnowledgePublication.first_approved_at < CAMPAIGN_END)
    if month:
        start, end = month_bounds(month)
        query = query.where(KnowledgePublication.first_approved_at >= start,
                            KnowledgePublication.first_approved_at < end)
    return (await db.execute(query)).scalars().all()


async def like_state(db, cutoff=None):
    likes = (await db.execute(select(KnowledgeLike))).scalars().all()
    if cutoff is None:
        return {(row.task_id, row.user_id): row for row in likes if row.active and row.risk_status == "valid"}
    events = (await db.execute(select(KnowledgeLikeEvent).where(
        KnowledgeLikeEvent.created_at < cutoff
    ).order_by(KnowledgeLikeEvent.created_at, KnowledgeLikeEvent.id))).scalars().all()
    latest = {(event.task_id, event.user_id): event.active for event in events}
    return {(row.task_id, row.user_id): row for row in likes
            if latest.get((row.task_id, row.user_id), False) and row.risk_status == "valid"}


async def rankings(db, month, frozen=True):
    if frozen:
        snapshot = await db.get(KnowledgeSettlement, month)
        if snapshot:
            return snapshot.rankings
    publications = await eligible_publications(db, month)
    users = await users_by_identity(db)
    _, end = month_bounds(month)
    likes = await like_state(db, end if utcnow() >= end else None)
    counts = defaultdict(int)
    for task_id, _ in likes:
        counts[task_id] += 1
    entries = {}
    for publication in publications:
        for user_id in {publication.questioner_id, publication.answerer_id}:
            if user_id not in users:
                continue
            entry = entries.setdefault(user_id, {**display_user(users[user_id]), "question_count": 0,
                "answer_count": 0, "contribution_count": 0, "valid_like_count": 0,
                "last_contribution_at": iso(publication.first_approved_at), "has_dual_role": False})
            entry["question_count"] += int(user_id == publication.questioner_id)
            entry["answer_count"] += int(user_id == publication.answerer_id)
            entry["contribution_count"] += 1
            entry["has_dual_role"] |= publication.questioner_id == publication.answerer_id
            entry["valid_like_count"] += counts[publication.task_id]
            entry["last_contribution_at"] = max(entry["last_contribution_at"], iso(publication.first_approved_at))
    result = sorted(entries.values(), key=lambda entry: (-entry["contribution_count"],
        -entry["valid_like_count"], entry["last_contribution_at"], entry["user_id"]))
    for position, entry in enumerate(result, 1):
        entry["rank"] = position
        entry["estimated_reward_fen"] = 10000 if position <= 3 else 5000 if position <= 10 else 0
        entry["tied"] = sum(other["contribution_count"] == entry["contribution_count"] and
                            other["valid_like_count"] == entry["valid_like_count"] for other in result) > 1
    return result


async def overview(db, actor, month=None):
    from app.modules.tasks.services.archive_review_dispatch import archive_reviewer_ids

    month = selected_month(month)
    identities = await actor_identities(db, actor)
    configured = bool(identities & archive_reviewer_ids())
    now = utcnow()
    snapshot = await db.get(KnowledgeSettlement, month)
    review = (await db.execute(select(ArchiveReport.id).where(
        ArchiveReport.reviewer.in_(identities)
    ).limit(1))).scalar_one_or_none()
    history = (await db.execute(select(ArchiveReviewDecision.id).where(
        ArchiveReviewDecision.reviewer_id.in_(identities)
    ).limit(1))).scalar_one_or_none()
    pending_count = (await db.execute(select(func.count()).select_from(ArchiveReport).where(
        ArchiveReport.reviewer.in_(identities), ArchiveReport.submit_status == "submitted"
    ))).scalar_one()
    return {"month": month, "months": list(CAMPAIGN_MONTHS), "starts_at": iso(CAMPAIGN_START),
            "ends_at": iso(CAMPAIGN_END), "campaign_status": "upcoming" if now < CAMPAIGN_START else
            "ended" if now >= CAMPAIGN_END else "active", "settlement_status": snapshot.status if snapshot else "live",
            "top_three": (await rankings(db, month))[:3],
            "can_review": configured or review is not None or history is not None or is_admin_user(actor),
            "reviewer_configured": configured, "pending_review_count": pending_count,
            "rule_notice": "综合榜统一计奖；同工单双角色暂计1次，同数量按有效获赞排序；未定边界须确认后发奖"}


async def question_items(db, actor, month=None, sort="archived_at", page=1, size=20, task_id=None,
                         contributor_id=None, role="all"):
    publications = await eligible_publications(db, month)
    if contributor_id:
        publications = [item for item in publications if
            (role in ("all", "questioner") and item.questioner_id == contributor_id) or
            (role in ("all", "answerer") and item.answerer_id == contributor_id)]
    if task_id is not None:
        publications = [item for item in publications if item.task_id == task_id]
        if not publications:
            raise HTTPException(404, "问题尚未公开或不属于本期活动")
    users = await users_by_identity(db)
    user_id = await actor_id(db, actor)
    identities = await actor_identities(db, actor)
    likes = await like_state(db)
    counts = defaultdict(int)
    for liked_task, _ in likes:
        counts[liked_task] += 1
    publications.sort(key=lambda item: (-counts[item.task_id] if sort == "likes" else 0,
                                        -item.first_approved_at.timestamp(), -item.task_id))
    items = []
    for publication in publications[(page - 1) * size:page * size]:
        report = await db.get(ArchiveReport, publication.report_id)
        items.append({"task_id": publication.task_id, "title": publication.title,
            "problem": publication.problem if task_id else publication.problem[:160],
            "content": publication.content if task_id else publication.content[:160],
            "review_comment": publication.review_comment, "value_point": publication.value_point,
            "archived_at": iso(publication.first_approved_at), "valid_like_count": counts[publication.task_id],
            "liked_by_me": (publication.task_id, user_id) in likes,
            "can_like": user_id not in (publication.questioner_id, publication.answerer_id) and
            report.reviewer not in identities and CAMPAIGN_START <= utcnow() < CAMPAIGN_END,
            "questioner": display_user(users[publication.questioner_id]),
            "answerer": display_user(users[publication.answerer_id])})
    return {"total": len(publications), "page": page, "size": size, "items": items}


async def contributor_questions(db, actor, user_id, month=None, role="all", page=1, size=20):
    month = selected_month(month)
    await actor_id(db, actor)
    user = await db.get(UserDB, user_id)
    publications = [item for item in await eligible_publications(db, month)
                    if user_id in (item.questioner_id, item.answerer_id)]
    if not user or not publications:
        raise HTTPException(404, "未找到本月公开知识贡献")
    result = await question_items(db, actor, month, page=page, size=size, contributor_id=user_id, role=role)
    return {**result, "month": month, "user": display_user(user),
            "question_count": sum(item.questioner_id == user_id for item in publications),
            "answer_count": sum(item.answerer_id == user_id for item in publications)}


async def discussion_item(db, discussion):
    task = await db.get(Task, discussion.task_id)
    reports = (await db.execute(select(ArchiveReport).where(ArchiveReport.task_id == task.id))).scalars().all()
    reviewers = {task.archived_by, *(report.reviewer for report in reports)}
    reviewers.update((await db.execute(select(ArchiveReviewDecision.reviewer_id).where(
        ArchiveReviewDecision.task_id == task.id))).scalars().all())
    users = await users_by_identity(db)
    author = users.get(discussion.author_id)
    hidden = author and {identity for identity in (author.id, author.username, author.wechat_openid) if identity} & reviewers
    return {"id": discussion.id, "task_id": discussion.task_id,
            "author": display_user(author) if author and not hidden else None,
            "content": await anonymous_text(db, task, discussion.content, False),
            "created_at": iso(discussion.created_at)}


async def discussion_items(db, actor, task_id, page=1, size=20):
    await question_items(db, actor, task_id=task_id)
    total = (await db.execute(select(func.count()).select_from(KnowledgeDiscussion).where(
        KnowledgeDiscussion.task_id == task_id))).scalar_one()
    rows = (await db.execute(select(KnowledgeDiscussion).where(KnowledgeDiscussion.task_id == task_id)
        .order_by(KnowledgeDiscussion.created_at.desc(), KnowledgeDiscussion.id.desc())
        .offset((page - 1) * size).limit(size))).scalars().all()
    return {"items": [await discussion_item(db, row) for row in rows], "total": total, "page": page, "size": size}


async def add_discussion(db, actor, task_id, content):
    await question_items(db, actor, task_id=task_id)
    value = content.strip()
    if not value or len(value) > 2000:
        raise HTTPException(422, "讨论内容须为1至2000字")
    row = KnowledgeDiscussion(task_id=task_id, author_id=await actor_id(db, actor), content=value, created_at=utcnow())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await discussion_item(db, row)


async def set_like(db, actor, task_id, active):
    user_id = await actor_id(db, actor, lock=True)
    now = utcnow()
    if not CAMPAIGN_START <= now < CAMPAIGN_END:
        raise HTTPException(409, "活动未开始或已截止，不能更改计奖点赞")
    await question_items(db, actor, task_id=task_id)
    publication = await db.get(KnowledgePublication, task_id)
    report = await db.get(ArchiveReport, publication.report_id)
    identities = await actor_identities(db, actor)
    if user_id in (publication.questioner_id, publication.answerer_id) or report.reviewer in identities:
        raise HTTPException(403, "不能为本人参与的问题点赞计奖")
    row = (await db.execute(select(KnowledgeLike).where(KnowledgeLike.task_id == task_id,
        KnowledgeLike.user_id == user_id).with_for_update()
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if row and row.risk_status != "valid":
        raise HTTPException(409, "该点赞正在核验或已判无效")
    if active and (not row or not row.active):
        day_start = now.replace(tzinfo=timezone.utc).astimezone(BEIJING).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).replace(tzinfo=None)
        liked_today = set((await db.execute(select(KnowledgeLikeEvent.task_id).where(
            KnowledgeLikeEvent.user_id == user_id,
            KnowledgeLikeEvent.active.is_(True),
            KnowledgeLikeEvent.created_at >= day_start,
            KnowledgeLikeEvent.created_at < day_start + timedelta(days=1)
        ).distinct())).scalars().all())
        if task_id not in liked_today and len(liked_today) >= DAILY_QUESTION_LIKE_LIMIT:
            raise HTTPException(429, "今日已点赞5个不同问题，不能再点赞新问题（北京时间每日0点重置）")
    if not row:
        row = KnowledgeLike(task_id=task_id, user_id=user_id, active=False, risk_status="valid", updated_at=now)
        db.add(row)
    if row.active != active:
        row.active, row.updated_at = active, now
        db.add(KnowledgeLikeEvent(task_id=task_id, user_id=user_id, active=active, created_at=row.updated_at))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "点赞状态已变化，请刷新后重试")
    return (await question_items(db, actor, task_id=task_id))["items"][0]
