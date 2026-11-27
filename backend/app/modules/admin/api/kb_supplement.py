"""知识补充（ORS-1159）：待补充知识点协作 + 审核 + 入库。

- 工作台「知识补充」审核台 → POST /push 推送（admin 登录）
- 服务号「知识库 → 待补充」：列表/详情/多人草稿/AI 汇总/提交审核
- 审核人通过后写 kb/team/chat_qa/{标题}.md（H1 + 正文），下次周入库生效

权限：
  补充（草稿/汇总/提交审核）= 智能规划研究院部门成员（KB_SUPPLEMENT_DEPT_ID 可覆盖）
  审核（通过/驳回）          = KB_SUPPLEMENT_REVIEWERS 名单（缺省 8 人）+ admin
"""  # noqa: D205
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_routes import get_current_active_user_from_token
from app.core.config import settings
from app.core.database import get_async_db as get_db
from app.models.kb_supplement import KbSupplementDraft, KbSupplementQuestion

kb_supplement_router = APIRouter(prefix="/kb/supplement-questions",
                                 tags=["admin-kb-supplement"])

KB_ROOT = Path(os.getenv("KB_DIR") or "/data/apps/OpenRobotService-Data/kb").resolve()

# 智能规划研究院（生产/测试库同 ID，见 departments 表）
DEPT_ID = os.getenv("KB_SUPPLEMENT_DEPT_ID", "34555bff-3321-4628-9a21-31d3beb5a0d0")
REVIEWERS = {
    x.strip() for x in os.getenv(
        "KB_SUPPLEMENT_REVIEWERS",
        "董华来,罗昊,胡健楠,贾爽,张文星,耿洪秀,张俊磊,白永奇",
    ).split(",") if x.strip()
}

QTYPES = ("故障处置", "操作指引", "功能咨询", "状态查询", "资料查询", "其他")

DRAFT_AI = "__ai__"  # AI 汇总稿的 author_id


def _uid(user: Dict[str, Any]) -> str:
    return str(user.get("id") or user.get("user_id") or "")


def _uname(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "")


# ---------------- 权限（auth 依赖返回的 dict 已带 department_id/name/permissions） ----------------


def _is_admin(user: Dict[str, Any]) -> bool:
    return "admin" in (user.get("permissions") or [])


def _is_dept_member(user: Dict[str, Any]) -> bool:
    return str(user.get("department_id") or "") == DEPT_ID


def _is_reviewer(user: Dict[str, Any]) -> bool:
    names = {str(user.get("name") or ""), _uname(user)}
    return bool(names & REVIEWERS)


def _require_member(user: Dict[str, Any]) -> Dict[str, Any]:
    if not (_is_dept_member(user) or _is_admin(user)):
        raise HTTPException(403, "仅智能规划研究院成员可补充知识")
    return user


def _require_reviewer(user: Dict[str, Any]) -> Dict[str, Any]:
    if not (_is_reviewer(user) or _is_admin(user)):
        raise HTTPException(403, "仅知识审核人可审核")
    return user


# ---------------- 序列化 ----------------


def _q_out(q: KbSupplementQuestion) -> Dict[str, Any]:
    return {
        "id": q.id, "week_date": q.week_date, "env": q.env,
        "title": q.title, "description": q.description or "",
        "qtype": q.qtype, "hit_count": q.hit_count, "status": q.status,
        "pushed_at": q.pushed_at.isoformat() if q.pushed_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


def _d_out(d: KbSupplementDraft) -> Dict[str, Any]:
    return {
        "id": d.id, "question_id": d.question_id,
        "author_id": d.author_id, "author_name": d.author_name or "",
        "content_md": d.content_md or "", "media": d.media or "",
        "is_merged": bool(d.is_merged),
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


# ---------------- 推送（工作台 → 服务号） ----------------


class PushItem(BaseModel):
    id: str
    title: str
    description: str = ""
    qtype: str = "其他"
    count: int = 0


class PushReq(BaseModel):
    week_date: str = ""
    env: str = "prod"
    items: List[PushItem] = []


@kb_supplement_router.post("/push")
async def push_questions(
    req: PushReq,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    """工作台推送知识点（admin 登录）。

    幂等：同 (week_date, id) 不重复累加热度；已有记录只更新描述/类型与最近周次，
    状态保留（已入库的不因重推回退）。
    """
    if not _is_admin(user):
        raise HTTPException(403, "仅管理员可推送知识点")
    if not req.items:
        raise HTTPException(400, "items 为空")

    created = updated = 0
    for it in req.items:
        q = await db.get(KbSupplementQuestion, it.id)
        qtype = it.qtype if it.qtype in QTYPES else "其他"
        if q is None:
            db.add(KbSupplementQuestion(
                id=it.id, week_date=req.week_date or datetime.now().strftime("%Y%m%d"),
                env=req.env, title=it.title[:255], description=it.description[:2000],
                qtype=qtype, hit_count=max(0, it.count), status="open",
                pushed_at=datetime.now(),
            ))
            created += 1
        else:
            q.week_date = req.week_date or q.week_date
            q.hit_count = max(q.hit_count, max(0, it.count))
            if q.status in ("open", "drafting", "merged", "rejected"):
                q.title = it.title[:255]
                q.description = it.description[:2000]
                q.qtype = qtype
            updated += 1
    await db.commit()
    return {"code": 0, "created": created, "updated": updated}


# ---------------- 列表 / 详情 ----------------


@kb_supplement_router.get("")
async def list_questions(
    status: str = "",
    qtype: str = "",
    q: str = "",
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    stmt = select(KbSupplementQuestion)
    if status:
        stmt = stmt.where(KbSupplementQuestion.status == status)
    if qtype:
        stmt = stmt.where(KbSupplementQuestion.qtype == qtype)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (KbSupplementQuestion.title.like(like))
            | (KbSupplementQuestion.description.like(like)))
    stmt = stmt.order_by(KbSupplementQuestion.hit_count.desc(),
                         KbSupplementQuestion.updated_at.desc()).limit(500)
    rows = (await db.execute(stmt)).scalars().all()

    counts = dict((await db.execute(
        select(KbSupplementDraft.question_id, func.count(KbSupplementDraft.id))
        .where(KbSupplementDraft.is_merged.is_(False))  # 只数人工草稿，AI 汇总稿单列
        .group_by(KbSupplementDraft.question_id))).all())
    out = []
    for r in rows:
        d = _q_out(r)
        d["draft_count"] = int(counts.get(r.id, 0))
        out.append(d)
    # 状态计数用全量（不随当前 status 筛选变化）——否则切到「待补充」时
    # 其他状态全显示 0，误导用户以为没有数据
    summ_rows = (await db.execute(
        select(KbSupplementQuestion.status, func.count(KbSupplementQuestion.id))
        .group_by(KbSupplementQuestion.status))).all()
    summary = {s: 0 for s in ("open", "drafting", "merged", "reviewing",
                              "approved", "rejected")}
    for st, cnt in summ_rows:
        summary[str(st)] = int(cnt)
    return {"code": 0, "data": out, "summary": summary}


@kb_supplement_router.get("/{qid}")
async def question_detail(
    qid: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    drafts = (await db.execute(
        select(KbSupplementDraft).where(KbSupplementDraft.question_id == qid)
        .order_by(KbSupplementDraft.is_merged.desc(), KbSupplementDraft.updated_at.desc())
    )).scalars().all()
    return {
        "code": 0,
        "data": _q_out(q),
        "drafts": [_d_out(d) for d in drafts],
        "me": {
            "can_write": bool(_is_dept_member(user) or _is_admin(user)),
            "can_review": bool(_is_reviewer(user) or _is_admin(user)),
            "user_id": _uid(user), "name": str(user.get("name") or "") or _uname(user),
        },
    }


# ---------------- 草稿 ----------------


class DraftReq(BaseModel):
    content_md: str = ""
    media: str = ""


@kb_supplement_router.post("/{qid}/drafts")
async def save_draft(
    qid: str,
    req: DraftReq,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    """提交/更新自己的答案草稿（每人一条，重复提交覆盖）。"""
    user = _require_member(user)
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    if q.status in ("approved",):
        raise HTTPException(400, "该知识点已入库，不能再改草稿")
    if q.status == "reviewing":
        raise HTTPException(400, "审核中，内容已冻结；由审核人驳回后再修改")
    if not req.content_md.strip():
        raise HTTPException(400, "答案内容为空")
    uid, uname = _uid(user), str(user.get("name") or "") or _uname(user)

    d = (await db.execute(select(KbSupplementDraft).where(
        KbSupplementDraft.question_id == qid,
        KbSupplementDraft.author_id == uid))).scalar_one_or_none()
    if d is None:
        db.add(KbSupplementDraft(question_id=qid, author_id=uid,
                                 author_name=uname,
                                 content_md=req.content_md, media=req.media,
                                 is_merged=False))
    else:
        d.content_md = req.content_md
        d.media = req.media
        d.author_name = uname
    if q.status in ("open", "rejected"):
        q.status = "drafting"
    await db.commit()
    return {"code": 0}


# ---------------- AI 汇总 ----------------


@kb_supplement_router.post("/{qid}/merge")
async def merge_drafts(
    qid: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    """把本知识点的全部人工草稿交给 AI 汇总成一份标准答案（覆盖旧汇总稿）。"""
    _require_member(user)
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    if q.status in ("approved", "reviewing"):
        raise HTTPException(400, "审核中/已入库，不能重新汇总")
    drafts = (await db.execute(
        select(KbSupplementDraft).where(
            KbSupplementDraft.question_id == qid,
            KbSupplementDraft.is_merged.is_(False))
        .order_by(KbSupplementDraft.updated_at.asc()))).scalars().all()
    if not drafts:
        raise HTTPException(400, "还没有任何草稿，先让成员补充答案")

    body = {
        "title": q.title,
        "description": q.description or "",
        "drafts": [{"author": d.author_name or "", "content": d.content_md or ""}
                   for d in drafts],
    }
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            r = await c.post(f"{settings.AI_SERVICE_URL.rstrip('/')}/api/ai/kb/supplement/merge",
                             json=body)
            r.raise_for_status()
            merged = (r.json() or {}).get("content_md") or ""
    except Exception as e:
        raise HTTPException(502, f"AI 汇总失败: {type(e).__name__}: {e}")
    if not merged.strip():
        raise HTTPException(502, "AI 汇总返回为空")

    d = (await db.execute(select(KbSupplementDraft).where(
        KbSupplementDraft.question_id == qid,
        KbSupplementDraft.author_id == DRAFT_AI))).scalar_one_or_none()
    if d is None:
        db.add(KbSupplementDraft(question_id=qid, author_id=DRAFT_AI,
                                 author_name="AI 汇总", content_md=merged,
                                 is_merged=True))
    else:
        d.content_md = merged
        d.is_merged = True
    q.status = "merged"
    await db.commit()
    return {"code": 0, "content_md": merged}


# ---------------- 提交审核 / 通过 / 驳回 ----------------


@kb_supplement_router.post("/{qid}/submit-review")
async def submit_review(
    qid: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    _require_member(user)
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    if q.status == "approved":
        raise HTTPException(400, "已入库")
    has = (await db.execute(select(KbSupplementDraft.id).where(
        KbSupplementDraft.question_id == qid))).first()
    if not has:
        raise HTTPException(400, "没有任何草稿，不能提交审核")
    q.status = "reviewing"
    await db.commit()
    return {"code": 0}


def _kb_file_path(title: str) -> Path:
    safe = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title).strip(" ._")[:80] or "未命名知识点"
    return KB_ROOT / "team" / "chat_qa" / f"{safe}.md"


@kb_supplement_router.post("/{qid}/approve")
async def approve_question(
    qid: str,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    """审核通过 → 写 kb/team/chat_qa/{标题}.md（H1=标题 + 汇总稿正文）。"""
    user = _require_reviewer(user)
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    if q.status == "approved":
        raise HTTPException(400, "已入库")
    drafts = (await db.execute(
        select(KbSupplementDraft).where(KbSupplementDraft.question_id == qid)
        .order_by(KbSupplementDraft.is_merged.desc(),
                  KbSupplementDraft.updated_at.desc()))).scalars().all()
    if not drafts:
        raise HTTPException(400, "没有任何草稿，不能通过")
    content = drafts[0].content_md  # 优先汇总稿，其次最新草稿

    path = _kb_file_path(q.title)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {q.title}\n\n{content.strip()}\n", encoding="utf-8")
    q.status = "approved"
    await db.commit()
    return {"code": 0, "file": str(path),
            "reviewer": str(user.get("name") or "") or _uname(user)}


class RejectReq(BaseModel):
    reason: str = ""


@kb_supplement_router.post("/{qid}/reject")
async def reject_question(
    qid: str,
    req: RejectReq,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_active_user_from_token),
):
    user = _require_reviewer(user)
    q = await db.get(KbSupplementQuestion, qid)
    if not q:
        raise HTTPException(404, "知识点不存在")
    if q.status == "approved":
        raise HTTPException(400, "已入库，不能驳回")
    q.status = "rejected"
    await db.commit()
    return {"code": 0, "reason": req.reason,
            "reviewer": str(user.get("name") or "") or _uname(user)}
