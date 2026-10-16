from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from app.core.auth_routes import get_current_active_user_from_token
from app.core.database import get_async_db
from app.core.user_identity import user_matches
from app.models.knowledge import ArchiveReviewDecision
from app.models.task import ArchiveReport, Task
from app.modules.admin.services.knowledge_service import actor_identities, anonymous_text, iso
from app.modules.tasks.schemas.archive_report import ArchiveReportResponse


async def projected_report(db, report, actor):
    task = await db.get(Task, report.task_id)
    owner = user_matches(actor, report.created_by, task.created_by, task.assigned_to)
    reviewer = report.reviewer in await actor_identities(db, actor)
    response = ArchiveReportResponse.model_validate(report)
    response.reviewer = None
    response.reviewer_name = None
    response.updated_by = None
    response.updated_by_name = None
    response.can_review = reviewer and report.submit_status == "submitted"
    if report.submit_status == "approved":
        response.content = await anonymous_text(db, task, response.content, False)
    if report.submit_status != "approved" and not owner:
        response.created_by = ""
        response.created_by_name = None
        response.content = await anonymous_text(db, task, report.content) if reviewer else ""
    if report.submit_status == "rejected" and not (owner or reviewer):
        response.review_comment = None
    if response.review_comment:
        response.review_comment = await anonymous_text(db, task, response.review_comment, False)
    if response.value_point:
        response.value_point = await anonymous_text(db, task, response.value_point, False)
    return response


async def review_detail(db, actor, report_id):
    report = await db.get(ArchiveReport, report_id)
    if not report or report.reviewer not in await actor_identities(db, actor):
        raise HTTPException(404, "未找到分配给你的审核报告")
    task = await db.get(Task, report.task_id)
    return {"report_id": report.id, "task_id": task.id, "version": report.version,
            "revision": report.revision, "submit_status": report.submit_status,
            "title": await anonymous_text(db, task, task.title),
            "problem": await anonymous_text(db, task, task.description),
            "content": await anonymous_text(db, task, report.content),
            "review_comment": await anonymous_text(db, task, report.review_comment),
            "value_point": await anonymous_text(db, task, report.value_point)}


async def pending_reviews(db, actor):
    identities = await actor_identities(db, actor)
    reports = (await db.execute(select(ArchiveReport).where(
        ArchiveReport.reviewer.in_(identities),
        ArchiveReport.submit_status == "submitted"
    ).order_by(ArchiveReport.updated_at.desc()))).scalars().all()
    return {"items": [await review_detail(db, actor, report.id) for report in reports], "total": len(reports)}


async def own_review_history(db, actor):
    identities = await actor_identities(db, actor)
    rows = (await db.execute(select(ArchiveReviewDecision).where(
        ArchiveReviewDecision.reviewer_id.in_(identities)
    ).order_by(ArchiveReviewDecision.created_at.desc(), ArchiveReviewDecision.id.desc()))).scalars().all()
    items = []
    for row in rows:
        task = await db.get(Task, row.task_id)
        items.append({"id": row.id, "report_id": row.report_id, "task_id": row.task_id,
            "version": row.version, "outcome": row.outcome, "created_at": iso(row.created_at),
            "review_comment": await anonymous_text(db, task, row.review_comment),
            "value_point": await anonymous_text(db, task, row.value_point),
            "content": await anonymous_text(db, task, row.content)})
    return {"items": items, "total": len(items)}


async def blind_review_actor(request: Request):
    if not request.path_params.get("task_id") or request.url.path.endswith("/similar"):
        return None
    return await get_current_active_user_from_token(request)


async def raw_access_guard(request: Request, db=Depends(get_async_db),
                           actor=Depends(blind_review_actor)):
    task_id = request.path_params.get("task_id")
    if not task_id or request.url.path.endswith("/similar"):
        return
    try:
        task_id = int(task_id)
    except ValueError:
        return
    report = (await db.execute(select(ArchiveReport).where(ArchiveReport.task_id == task_id)
        .order_by(ArchiveReport.version.desc()).limit(1))).scalar_one_or_none()
    if report and report.submit_status != "approved" and report.reviewer in await actor_identities(db, actor):
        raise HTTPException(403, {"code": "BLIND_REVIEW_REQUIRED", "report_id": report.id,
                                  "message": "请从匿名审核页面查看报告"})


async def project_task_list(db, result, actor):
    from app.modules.tasks.schemas.ticket import TicketListItemResponse

    items = result.get("items", [])
    ids = [item.get("id") if isinstance(item, dict) else item.id for item in items]
    reports = (await db.execute(select(ArchiveReport).where(ArchiveReport.task_id.in_(ids),
        ArchiveReport.submit_status != "approved"))).scalars().all() if ids else []
    identities = await actor_identities(db, actor) if reports else set()
    hidden = {report.task_id for report in reports if report.reviewer in identities}
    projected = []
    allowed = {"id", "title", "description", "status", "priority", "ticket_type", "task_type", "created_at", "updated_at"}
    for item in items:
        response = TicketListItemResponse.model_validate(item).model_dump()
        response["archived_by"] = None
        if response["id"] in hidden:
            task = await db.get(Task, response["id"])
            response["title"] = await anonymous_text(db, task, response["title"])
            response["description"] = "待归档审核，请进入匿名审核页面"
            for key, value in list(response.items()):
                if key not in allowed:
                    response[key] = "" if isinstance(value, str) else False if isinstance(value, bool) else \
                        0 if isinstance(value, int) else [] if isinstance(value, list) else None
        projected.append(response)
    related = result.get("related_tickets")
    if related:
        result = {**result, "related_tickets": (await project_task_list(db, {"items": related}, actor))["items"]}
    return {**result, "items": projected}
