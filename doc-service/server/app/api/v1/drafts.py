from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.auth import require_user
from app.infra.crypto import ContentCrypto
from app.infra.db import get_db
from app.policy import Principal
from app.services import drafts as draft_svc
from app.services import packs as packs_svc
from app.settings import get_settings

router = APIRouter()


def _raise(code: int, out: dict) -> None:
    err = out.get("error") or "error"
    extra = {k: out[k] for k in ("hint", "message") if out.get(k)}
    if extra.get("hint") and "message" not in extra:
        extra["message"] = extra["hint"]
    if extra:
        raise HTTPException(code, detail={"error": err, **extra})
    raise HTTPException(code, detail=err)


def crypto() -> ContentCrypto:
    return ContentCrypto(get_settings().topo_content_key)


class DraftIn(BaseModel):
    title: str
    markdown: str | None = None


class DraftPatch(BaseModel):
    title: str | None = None
    markdown: str | None = None


class PublishIn(BaseModel):
    usernames: list[str] = []


class CommentIn(BaseModel):
    body: str
    quote: str | None = None


@router.get("/drafts/people")
def draft_people(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return draft_svc.list_people(db)


@router.get("/drafts")
def list_drafts(
    tag: str = Query(draft_svc.TAG_PUBLIC),
    page: int = Query(1, ge=1),
    size: int = Query(draft_svc.PAGE_DEFAULT, ge=1, le=draft_svc.PAGE_MAX),
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    return draft_svc.list_drafts(db, p, tag=tag, page=page, size=size)


@router.post("/drafts", status_code=201)
def create_draft(body: DraftIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    code, out = draft_svc.create_draft(db, crypto(), p, body.title, body.markdown or "")
    if code != 201:
        _raise(code, out)
    return out


@router.get("/drafts/{draft_id}")
def get_draft(
    draft_id: str,
    include_markdown: bool = Query(True),
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    code, out = draft_svc.get_draft(db, crypto(), p, draft_id, include_markdown=include_markdown)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.patch("/drafts/{draft_id}")
def patch_draft(
    draft_id: str, body: DraftPatch, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    code, out = draft_svc.update_draft(
        db, crypto(), p, draft_id, title=body.title, markdown=body.markdown
    )
    if code != 200:
        _raise(code, out)
    return out


@router.post("/drafts/{draft_id}/publish")
def publish_draft(
    draft_id: str, body: PublishIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    code, out = draft_svc.publish_draft(db, p, draft_id, body.usernames)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/drafts/{draft_id}/comments", status_code=201)
def comment_draft(
    draft_id: str, body: CommentIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    code, out = draft_svc.add_comment(db, p, draft_id, body.body, body.quote)
    if code != 201:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/drafts/{draft_id}/views")
def post_draft_views(draft_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    code, out = draft_svc.record_view(db, p, draft_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/drafts/{draft_id}/views")
def get_draft_views(draft_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    code, out = draft_svc.list_views(db, p, draft_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/drafts/{draft_id}/edit-token")
def draft_edit_token(
    draft_id: str,
    since: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    code, out = packs_svc.draft_edit_token_status(db, p, draft_id, since or "")
    if code != 200:
        _raise(code, out)
    return out


@router.get("/drafts/{draft_id}/pack")
def draft_pack_export(draft_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    code, out = packs_svc.export_draft_pack(db, crypto(), p, draft_id)
    if code != 200:
        _raise(code, out if isinstance(out, dict) else {"error": "error"})
    return Response(
        content=out,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{draft_id}.zip"'},
    )


@router.post("/drafts/{draft_id}/pack")
async def draft_pack_import(
    draft_id: str, request: Request, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    raw = await request.body()
    code, out = packs_svc.import_draft_pack(db, crypto(), p, draft_id, raw)
    if code != 200:
        _raise(code, out)
    return out


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    code, out = draft_svc.delete_draft(db, p, draft_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out
