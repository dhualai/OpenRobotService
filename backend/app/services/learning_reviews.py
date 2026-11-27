import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select

from app.models.identity import UserDB
from app.models.learning import LearningArticle, LearningArticleVersion, LearningAudit, LearningQuestion
from app.models.organization import Department
from app.models.task import OperationType, Task, TaskOperationLog, TaskPriority, TaskStatus, TaskType
from app.modules.admin.services.knowledge_service import actor_id, actor_identities
from app.modules.admin.services.learning_content import reading_article


SOURCE = "learning_review"
ACTIVE_STATES = (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.PENDING)
logger = logging.getLogger(__name__)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def reviewers(db):
    rows = (await db.execute(select(UserDB, Department).outerjoin(
        Department, UserDB.department_id == Department.id
    ).where(UserDB.status == "active").order_by(UserDB.name, UserDB.username, UserDB.id))).all()
    return {"items": [{"id": user.id, "username": user.username, "name": user.name or user.username,
        "department_id": department.id if department else user.department_id,
        "department": department.name if department else user.department or ""} for user, department in rows]}


async def validate_reviewer(db, reviewer_id):
    if not reviewer_id:
        return None
    user = await db.get(UserDB, reviewer_id)
    if not user or user.status != "active":
        raise HTTPException(422, "审核人不存在或已停用，请重新选择")
    return user.id


def log_operation(db, task, actor, operation, description):
    db.add(TaskOperationLog(task_id=task.id, operation_type=operation,
        operator=str(actor.get("username") or actor.get("id")), operator_name=actor.get("name"),
        to_status=task.status.value, description=description, created_at=utcnow()))


async def create_review(db, actor, article, question, revision, reviewer_id):
    now = utcnow()
    task = Task(title=f"题目审核：{question.payload['stem']}"[:255],
        description=f"请核对知识点《{article.title}》的题目、选项、答案、解析与文章依据，并检查脱敏结果。\n"
                    f"题干：{question.payload['stem']}\n请在系统任务 → 学习任务中完成审核。",
        source=SOURCE, external_id=f"{question.id}:{revision}", task_type=TaskType.OTHER,
        status=TaskStatus.IN_PROGRESS if reviewer_id else TaskStatus.NEW, priority=TaskPriority.MEDIUM,
        created_by=await actor_id(db, actor), assigned_to=reviewer_id,
        tags=["学习任务", "题目审核"], deadline_at=now + timedelta(days=7),
        metadata_info={"kind": "learning_question_review", "question_id": question.id,
            "article_id": article.id, "question_revision": revision, "article_version": question.article_version},
        created_at=now, updated_at=now)
    db.add(task)
    await db.flush()
    log_operation(db, task, actor, OperationType.CREATE, "提交题目审核，指定审核人" if reviewer_id else "提交题目审核，等待自动派单")
    return task


async def notify_review(task):
    from app.modules.tasks.services.ticket_service import _publish_new_ticket_to_assigner

    if not task.assigned_to:
        await _publish_new_ticket_to_assigner(task.id)
        return
    try:
        from app.utils.notification_utils import NotificationUtils

        await NotificationUtils.send_ticket_create_notification(ticket_id=task.id, title=task.title,
            project_name="学习任务", operator=task.created_by, deadline_at=task.deadline_at,
            user_names=[task.assigned_to])
    except Exception:
        logger.exception("题目审核通知发送失败 task_id=%s", task.id)


async def finish_reviews(db, actor, *, question_id=None, article_id=None, outcome="canceled", reason=""):
    query = select(Task).where(Task.source == SOURCE, Task.status.in_(ACTIVE_STATES))
    if question_id:
        query = query.where(Task.metadata_info["question_id"].as_string() == question_id)
    elif article_id:
        query = query.where(Task.metadata_info["article_id"].as_string() == article_id)
    else:
        raise ValueError("必须指定题目或文章")
    tasks = (await db.execute(query.with_for_update().execution_options(populate_existing=True))).scalars().all()
    for task in tasks:
        task.status = TaskStatus.CLOSED if outcome in ("approved", "rejected") else TaskStatus.CANCELED
        task.updated_at = utcnow()
        if task.status == TaskStatus.CLOSED:
            task.closed_at = task.updated_at
        else:
            task.canceled_at = task.updated_at
        task.metadata_info = {**task.metadata_info, "outcome": outcome, "reason": reason}
        log_operation(db, task, actor, OperationType.STATUS_CHANGE,
            {"approved": "题目审核通过", "rejected": "题目审核驳回"}.get(outcome, "题目或文章已变更，本次审核失效"))


def summary(task, identities):
    return {"id": str(task.id), "title": task.title, "status": task.status.value,
        "assigned_to": task.assigned_to, "created_at": task.created_at.isoformat(),
        "outcome": (task.metadata_info or {}).get("outcome"),
        "reason": (task.metadata_info or {}).get("reason", ""),
        "can_review": task.assigned_to in identities and task.status in ACTIVE_STATES}


async def listing(db, actor, page=1):
    identities = await actor_identities(db, actor)
    query = select(Task).where(Task.source == SOURCE,
        or_(Task.assigned_to.in_(identities), Task.created_by.in_(identities)))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    tasks = (await db.execute(query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset((page - 1) * 20).limit(20))).scalars().all()
    return {"items": [summary(task, identities) for task in tasks], "total": total}


async def visible_task(db, actor, task_id):
    identities = await actor_identities(db, actor)
    task = (await db.execute(select(Task).where(Task.id == task_id, Task.source == SOURCE,
        or_(Task.assigned_to.in_(identities), Task.created_by.in_(identities))))).scalar_one_or_none()
    if not task:
        raise HTTPException(404, "未找到与你相关的题目审核工单")
    return task, identities


async def detail(db, actor, task_id):
    task, identities = await visible_task(db, actor, task_id)
    metadata = task.metadata_info or {}
    question = await db.get(LearningQuestion, metadata.get("question_id"))
    version = (await db.execute(select(LearningArticleVersion).where(
        LearningArticleVersion.article_id == metadata.get("article_id"),
        LearningArticleVersion.version == metadata.get("article_version")))).scalar_one_or_none()
    article = await db.get(LearningArticle, metadata.get("article_id"))
    current = bool(question and article and article.enabled and
        question.revision == metadata.get("question_revision") and question.review_status == "pending_review" and
        question.article_version == article.active_version)
    submitted = (await db.execute(select(LearningAudit).where(LearningAudit.question_id == metadata.get("question_id"),
        LearningAudit.action == "question.submit", LearningAudit.snapshot["review_task_id"].as_string() == str(task.id))
        .order_by(LearningAudit.created_at.desc()).limit(1))).scalar_one_or_none()
    return {**summary(task, identities), "can_review": task.assigned_to in identities and
        task.status in ACTIVE_STATES and current, "stale": task.status in ACTIVE_STATES and not current,
        "revision": metadata.get("question_revision"), "question": submitted.snapshot["payload"] if submitted else None,
        "article": reading_article(version.snapshot["content"]) if version else None}


async def decide(db, actor, task_id, body):
    from app.modules.admin.services import learning_service

    task, identities = await visible_task(db, actor, task_id)
    metadata = task.metadata_info or {}
    article = (await db.execute(select(LearningArticle).where(LearningArticle.id == metadata.get("article_id"))
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    question = (await db.execute(select(LearningQuestion).where(LearningQuestion.id == metadata.get("question_id"))
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    task = (await db.execute(select(Task).where(Task.id == task_id).with_for_update()
        .execution_options(populate_existing=True))).scalar_one()
    if task.assigned_to not in identities:
        raise HTTPException(403, "只有当前工单的审核人可以审核")
    if task.status not in ACTIVE_STATES or not article or not question or not article.enabled or \
            question.article_id != article.id or question.article_version != article.active_version or \
            question.revision != (task.metadata_info or {}).get("question_revision") or \
            question.review_status != "pending_review" or body.revision != question.revision:
        raise HTTPException(409, "审核工单已完成或题目已变更，请刷新后重试")
    await learning_service.apply_question_action(db, actor, article, question, body, assigned_review=True)
    return await detail(db, actor, task_id)
