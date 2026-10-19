import hashlib

from fastapi import HTTPException
from sqlalchemy import select

from app.models.knowledge import KnowledgeLike, KnowledgePublication, KnowledgeSettlement
from app.models.task import Task
from app.modules.admin.services.knowledge_service import actor_id, iso, month_bounds, rankings, utcnow
from app.modules.wxpay.schemas import GrantRewardIn
from app.modules.wxpay.service import RewardService


def settlement_view(snapshot):
    if not snapshot:
        return None
    return {"month": snapshot.month, "status": snapshot.status, "revision": snapshot.revision,
            "rankings": snapshot.rankings, "audit": snapshot.audit, "frozen_at": iso(snapshot.frozen_at),
            "reviewed_at": iso(snapshot.reviewed_at), "granted_at": iso(snapshot.granted_at)}


async def risk_items(db, month, page=1, size=30):
    start, end = month_bounds(month)
    rows = (await db.execute(select(KnowledgeLike).join(KnowledgePublication,
        KnowledgePublication.task_id == KnowledgeLike.task_id).join(Task, Task.id == KnowledgePublication.task_id).where(
        Task.created_at >= start, Task.created_at < end
    ).order_by(KnowledgeLike.updated_at.desc(), KnowledgeLike.task_id, KnowledgeLike.user_id))).scalars().all()
    counts = {}
    for row in rows:
        counts[row.user_id] = counts.get(row.user_id, 0) + int(row.active)
    return {"total": len(rows), "items": [{"task_id": row.task_id, "user_id": row.user_id,
        "active": row.active, "risk_status": row.risk_status, "risk_note": row.risk_note,
        "updated_at": iso(row.updated_at), "user_like_count": counts[row.user_id]} for row in rows[(page - 1) * size:page * size]],
        "settlement": settlement_view(await db.get(KnowledgeSettlement, month))}


async def mark_like_risk(db, actor, task_id, user_id, status, note):
    row = (await db.execute(select(KnowledgeLike).where(KnowledgeLike.task_id == task_id,
        KnowledgeLike.user_id == user_id).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "点赞记录不存在")
    task = await db.get(Task, task_id)
    from app.modules.admin.services.knowledge_service import BEIJING
    from datetime import timezone
    month = task.created_at.replace(tzinfo=timezone.utc).astimezone(BEIJING).strftime("%Y-%m")
    snapshot = (await db.execute(select(KnowledgeSettlement).where(
        KnowledgeSettlement.month == month).with_for_update())).scalar_one_or_none()
    if snapshot and snapshot.status == "granting":
        raise HTTPException(409, "奖金正在发放，不能同时修改计奖点赞")
    row.risk_status, row.risk_note = status, note
    row.risk_operator, row.updated_at = await actor_id(db, actor), utcnow()
    if snapshot:
        if snapshot.status != "granted":
            snapshot.status = "stale"
        snapshot.audit = [*snapshot.audit, {"action": "risk_changed", "operator": row.risk_operator,
            "note": note, "task_id": task_id, "at": iso(utcnow())}]
    await db.commit()
    return {"ok": True}


async def freeze_month(db, actor, month, note):
    _, end = month_bounds(month)
    if utcnow() < end:
        raise HTTPException(409, "月份尚未结束，不能提前冻结最终榜单")
    snapshot = (await db.execute(select(KnowledgeSettlement).where(
        KnowledgeSettlement.month == month).with_for_update())).scalar_one_or_none()
    if snapshot and snapshot.status in ("granting", "granted"):
        raise HTTPException(409, "已开始发奖，不能重建快照")
    entries = await rankings(db, month, frozen=False)
    action = {"action": "freeze", "operator": await actor_id(db, actor), "note": note, "at": iso(utcnow())}
    if not snapshot:
        snapshot = KnowledgeSettlement(month=month, revision=1, rankings=entries,
            status="reviewing", audit=[action], frozen_at=utcnow())
        db.add(snapshot)
    else:
        action["previous_rankings"] = snapshot.rankings
        snapshot.audit = [*snapshot.audit, action]
        snapshot.rankings = entries
        snapshot.revision += 1
        snapshot.status = "reviewing"
        snapshot.frozen_at = utcnow()
        snapshot.reviewed_by = snapshot.reviewed_at = None
    await db.commit()
    return settlement_view(snapshot)


def unresolved_boundary(entries):
    if any(entry.get("has_dual_role") for entry in entries):
        return "存在同工单双角色贡献，请先确认计分规则再发奖"
    for boundary in (3, 10):
        if len(entries) > boundary:
            before, after = entries[boundary - 1], entries[boundary]
            if (before["contribution_count"], before["valid_like_count"]) == (after["contribution_count"], after["valid_like_count"]):
                return "奖励边界存在同数量且同获赞，请先确认最终排序规则"
    return None


async def review_snapshot(db, actor, month, approved, note):
    snapshot = (await db.execute(select(KnowledgeSettlement).where(
        KnowledgeSettlement.month == month).with_for_update())).scalar_one_or_none()
    if not snapshot or snapshot.status not in ("reviewing", "held", "approved"):
        raise HTTPException(409, "请先冻结榜单，发奖后不可重新核验")
    if approved and (reason := unresolved_boundary(snapshot.rankings)):
        raise HTTPException(409, reason)
    start, end = month_bounds(month)
    pending = (await db.execute(select(KnowledgeLike.task_id).join(KnowledgePublication,
        KnowledgePublication.task_id == KnowledgeLike.task_id).join(Task, Task.id == KnowledgePublication.task_id).where(
        KnowledgeLike.risk_status == "pending", Task.created_at >= start, Task.created_at < end).limit(1))).first()
    if approved and pending:
        raise HTTPException(409, "仍有待核验点赞，请处理后重建快照")
    snapshot.status = "approved" if approved else "held"
    snapshot.reviewed_by, snapshot.reviewed_at = await actor_id(db, actor), utcnow()
    snapshot.audit = [*snapshot.audit, {"action": snapshot.status, "operator": snapshot.reviewed_by,
        "note": note, "at": iso(snapshot.reviewed_at)}]
    await db.commit()
    return settlement_view(snapshot)


async def grant_month(db, actor, month):
    snapshot = (await db.execute(select(KnowledgeSettlement).where(
        KnowledgeSettlement.month == month).with_for_update())).scalar_one_or_none()
    if not snapshot or snapshot.status not in ("approved", "granting", "granted"):
        raise HTTPException(409, "须完成异常核验后才能发奖")
    if snapshot.status == "granted":
        return settlement_view(snapshot)
    snapshot.status = "granting"
    await db.commit()
    for entry in snapshot.rankings[:10]:
        source_id = hashlib.sha256(f"phase1:{month}:{entry['user_id']}".encode()).hexdigest()[:48]
        await RewardService.grant(db, GrantRewardIn(user_id=entry["user_id"],
            amount=entry["estimated_reward_fen"], scene_name="机器人知识贡献月榜",
            reward_desc=f"{month}综合贡献第{entry['rank']}名", source_type="knowledge_monthly", source_id=source_id))
    snapshot.status, snapshot.granted_at = "granted", utcnow()
    snapshot.audit = [*snapshot.audit, {"action": "granted", "operator": await actor_id(db, actor), "at": iso(utcnow())}]
    await db.commit()
    return settlement_view(snapshot)
