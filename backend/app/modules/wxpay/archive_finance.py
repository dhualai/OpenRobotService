from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.models.knowledge import KnowledgePublication
from app.models.task import ArchiveReport, OperationType, Task, TaskOperationLog, TaskStatus
from app.models.wxpay import RewardRecord, RewardStatus
from app.modules.admin.services import knowledge_service
from app.modules.wxpay.schemas import GrantRewardIn
from app.modules.wxpay.service import AuditDecisionEngine, RewardService


ARCHIVE_FINANCE_SOURCE = "task_archive"
ARCHIVE_REWARD_ROLES = {"reporter", "assignee", "reviewer"}


async def prepare_archive_rewards(db, task, report, reviewer_id) -> bool:
    """生成三方闭环奖励，返回 True 表示全部自动过审（无 pending_audit）。"""
    config = await RewardService.resolve_config(db, "task_archive")
    policy = await RewardService.resolve_config(db, "audit_policy")
    if not config:
        raise HTTPException(422, "请先在奖励管理中启用并配置三方闭环奖励")
    users = await knowledge_service.users_by_identity(db)
    participants = [("reporter", task.created_by, "提问人"),
                    ("assignee", report.created_by, "解答人"),
                    ("reviewer", reviewer_id, "审核人")]
    payloads = []
    for role, identity, label in participants:
        user = users.get(identity)
        amount = config.get(f"{role}_fen")
        if not user or not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise HTTPException(422, f"{label}身份或奖励金额未正确配置")
        payloads.append((role, user, amount, GrantRewardIn(user_id=user.id, amount=amount,
            scene_name="工单闭环奖励", reward_desc=f"工单{label}奖励",
            source_type=ARCHIVE_FINANCE_SOURCE, source_id=str(task.id))))

    now = knowledge_service.utcnow()
    any_pending_audit = False
    for role, user, amount, payload in payloads:
        reward, duplicated = await RewardService.grant(db, payload, role=role,
            status=RewardStatus.PENDING_AUDIT, auto_commit=False)
        if duplicated:
            raise HTTPException(409, "该工单已有闭环奖励，请先处理原财务审核记录")
        # 审核决策引擎分流：小额/低风险自动过审，强制审核条件触发则进 pending_audit
        if policy:
            auto_pass = await AuditDecisionEngine.evaluate(db, user.id, amount, policy)
            if auto_pass:
                reward.status = RewardStatus.PENDING
                reward.audit_by = "AUTO"
                reward.audit_at = now
                reward.expire_at = now + timedelta(days=int(settings.WXPAY_REWARD_EXPIRE_DAYS or 30))
            else:
                reward.audit_remark = await AuditDecisionEngine.reason(db, user.id, amount, policy)
                any_pending_audit = True
        else:
            # 没有 audit_policy 配置 → 全部走 pending_audit（兜底行为）
            any_pending_audit = True
    publication = await db.get(KnowledgePublication, task.id)
    if publication:
        publication.active = False
    await db.flush()
    return not any_pending_audit


async def archive_finance_detail(db, task_id):
    task = await db.get(Task, task_id)
    rewards = (await db.execute(select(RewardRecord).where(
        RewardRecord.source_type == ARCHIVE_FINANCE_SOURCE,
        RewardRecord.source_id == str(task_id)
    ))).scalars().all()
    report = (await db.execute(select(ArchiveReport).where(
        ArchiveReport.task_id == task_id, ArchiveReport.submit_status == "approved"
    ).order_by(ArchiveReport.version.desc(), ArchiveReport.id.desc()).limit(1))).scalar_one_or_none()
    if not task or not report or not rewards:
        raise HTTPException(404, "未找到该工单的财务审核记录")
    return {"task_id": task.id, "report_id": report.id,
            "title": await knowledge_service.anonymous_text(db, task, task.title, False),
            "problem": await knowledge_service.anonymous_text(db, task, task.description, False),
            "content": await knowledge_service.anonymous_text(db, task, report.content, False),
            "review_comment": await knowledge_service.anonymous_text(db, task, report.review_comment, False),
            "value_point": await knowledge_service.anonymous_text(db, task, report.value_point, False),
            "total_amount": sum(reward.amount for reward in rewards)}


async def audit_archive_rewards(db, ids, operator_id, approved, remark=""):
    if not approved and not remark.strip():
        raise HTTPException(422, "财务驳回必须填写审核意见")
    selected = (await db.execute(select(RewardRecord).where(
        RewardRecord.id.in_(ids), RewardRecord.source_type == ARCHIVE_FINANCE_SOURCE
    ))).scalars().all()
    task_ids = sorted({int(reward.source_id) for reward in selected})
    processed = []
    for task_id in task_ids:
        task = (await db.execute(select(Task).where(Task.id == task_id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        rewards = (await db.execute(select(RewardRecord).where(
            RewardRecord.source_type == ARCHIVE_FINANCE_SOURCE,
            RewardRecord.source_id == str(task_id)
        ).with_for_update().execution_options(populate_existing=True))).scalars().all()
        if not any(reward.status == RewardStatus.PENDING_AUDIT for reward in rewards):
            continue
        # 有 pending 的（自动过审）可以和 pending_audit 混合——只处理 pending_audit 那些
        audit_rewards = [r for r in rewards if r.status == RewardStatus.PENDING_AUDIT]
        if not audit_rewards:
            continue
        report = (await db.execute(select(ArchiveReport).where(
            ArchiveReport.task_id == task_id
        ).order_by(ArchiveReport.version.desc(), ArchiveReport.id.desc()).limit(1)
            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if not task or task.status != TaskStatus.ARCHIVING or not report or report.submit_status != "approved":
            raise HTTPException(409, "工单或归档报告状态已变更，请刷新后处理")
        now = knowledge_service.utcnow()
        for reward in audit_rewards:
            reward.status = RewardStatus.PENDING if approved else RewardStatus.REJECTED
            reward.audit_by, reward.audit_at = operator_id, now
            reward.audit_remark = remark.strip() or ("工单财务审核通过" if approved else "财务驳回")
            if approved:
                reward.expire_at = now + timedelta(days=int(settings.WXPAY_REWARD_EXPIRE_DAYS or 30))
        # 工单归档 + 知识库发布：只要存在至少一条 pending_audit 且 approved，就推进
        # （自动过审的 pending 奖励不需要财务干预，工单状态仍由财务审核最终决定）
        if approved:
            task.status, task.archived_at, task.archived_by = TaskStatus.ARCHIVED, now, report.reviewer
            publication = await db.get(KnowledgePublication, task.id)
            if publication and not publication.active:
                publication.first_approved_at = now
            await knowledge_service.publish_report(db, task, report, now)
        processed.extend(audit_rewards)
    await db.flush()
    return processed
