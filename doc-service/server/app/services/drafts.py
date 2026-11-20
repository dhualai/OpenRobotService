from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import delete as sql_delete, exists, func, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Draft, DraftComment, DraftViewer, User
from app.policy import Principal
from app.services import doc_views as views_svc
from app.services.markdown_html import md_to_html
from app.services.names import map_db

PAGE_DEFAULT = 20
PAGE_MAX = 50
TITLE_MAX = 200
TAG_DRAFT = "draft"
TAG_PUBLIC = "public"
TAGS = frozenset({TAG_DRAFT, TAG_PUBLIC})
AUDIENCE_ALL = "all"
AUDIENCE_LISTED = "listed"


def _draft_id() -> str:
    return "draft_" + secrets.token_hex(4)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _title(raw: str | None) -> str | None:
    t = (raw or "").strip()
    if not t:
        return None
    return t[:TITLE_MAX]


def _canon_tag(raw: str | None) -> str:
    t = (raw or TAG_PUBLIC).strip().lower()
    return t if t in TAGS else TAG_PUBLIC


def _owned(db: Session, principal: Principal, did: str) -> Draft | None:
    row = db.get(Draft, did)
    if not row or row.owner != principal.username:
        return None
    return row


def _can_view(db: Session, principal: Principal, row: Draft) -> bool:
    if row.owner == principal.username:
        return True
    if row.status != TAG_PUBLIC:
        return False
    if row.audience == AUDIENCE_ALL:
        return True
    return (
        db.scalar(
            select(DraftViewer.id).where(
                DraftViewer.draft_id == row.id, DraftViewer.username == principal.username
            )
        )
        is not None
    )


def _item(r: Draft, labels: dict[str, str]) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "status": r.status,
        "owner": labels.get(r.owner, r.owner),
        "updated_at": _iso(r.updated_at),
        "created_at": _iso(r.created_at),
    }


def list_people(db: Session) -> dict:
    rows = list(db.scalars(select(User)))
    labels = map_db(db)
    rows.sort(key=lambda u: ((labels.get(u.username) or u.username), u.username))
    return {
        "ok": True,
        "items": [{"username": u.username, "name": labels.get(u.username, u.username)} for u in rows],
    }


def list_drafts(
    db: Session, principal: Principal, *, tag: str | None = None, page: int = 1, size: int = PAGE_DEFAULT
) -> dict:
    size = min(max(int(size or PAGE_DEFAULT), 1), PAGE_MAX)
    page = max(int(page or 1), 1)
    tag = _canon_tag(tag)
    if tag == TAG_DRAFT:
        filt = (Draft.owner == principal.username) & (Draft.status == TAG_DRAFT)
    else:
        mine = DraftViewer.draft_id == Draft.id
        vis = DraftViewer.username == principal.username
        filt = (Draft.status == TAG_PUBLIC) & (
            (Draft.owner == principal.username)
            | (Draft.audience == AUDIENCE_ALL)
            | exists().where(mine, vis)
        )
    total = int(db.scalar(select(func.count()).select_from(Draft).where(filt)) or 0)
    pages = max(1, (total + size - 1) // size) if total else 1
    page = min(page, pages)
    rows = list(
        db.scalars(
            select(Draft)
            .where(filt)
            .order_by(Draft.updated_at.desc(), Draft.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    )
    labels = map_db(db)
    return {
        "ok": True,
        "tag": tag,
        "items": [_item(r, labels) for r in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "size": size,
    }


def create_draft(
    db: Session, crypto: ContentCrypto, principal: Principal, title: str, markdown: str = ""
) -> tuple[int, dict]:
    if markdown and getattr(principal, "via", "session") == "agent":
        from app.services.packs import use_pack_denied

        return use_pack_denied()
    t = _title(title)
    if not t:
        return 400, {"ok": False, "error": "need_title"}
    did = _draft_id()
    body = markdown or ""
    db.add(
        Draft(
            id=did,
            owner=principal.username,
            title=t,
            body_blob=crypto.encrypt(body, aad=f"draft:{did}"),
            content_hash=crypto.content_hash(body),
            status=TAG_DRAFT,
            audience=AUDIENCE_LISTED,
            updated_at=_now(),
        )
    )
    db.flush()
    return 201, {"ok": True, "id": did}


def _comments(db: Session, did: str) -> list[dict]:
    labels = map_db(db)
    rows = list(
        db.scalars(
            select(DraftComment)
            .where(DraftComment.draft_id == did)
            .order_by(DraftComment.created_at.asc(), DraftComment.id.asc())
        )
    )
    return [
        {
            "id": c.id,
            "author": labels.get(c.author, c.author),
            "body": c.body,
            "quote": c.quote,
        }
        for c in rows
    ]


def get_draft(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    did: str,
    *,
    include_markdown: bool = True,
) -> tuple[int, dict]:
    row = db.get(Draft, did)
    if not row or not _can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    owner = row.owner == principal.username
    body = crypto.decrypt_text(row.body_blob, aad=f"draft:{did}")
    labels = map_db(db)
    from app.services.packs import compose_edit_token

    return 200, {
        "ok": True,
        "id": row.id,
        "title": row.title,
        "status": row.status,
        "owner": labels.get(row.owner, row.owner),
        "updated_at": _iso(row.updated_at),
        "created_at": _iso(row.created_at),
        "markdown": body if include_markdown else "",
        "html": md_to_html(body),
        "content_hash": row.content_hash,
        "edit_token": compose_edit_token(row.content_hash),
        "can_edit": True,
        "can_delete": owner,
        "can_publish": owner and row.status == TAG_DRAFT,
        "can_comment": True,
        "comments": _comments(db, did),
    }


def update_draft(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    did: str,
    *,
    title: str | None = None,
    markdown: str | None = None,
    from_pack: bool = False,
) -> tuple[int, dict]:
    row = db.get(Draft, did)
    if not row or not _can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    if markdown is not None and getattr(principal, "via", "session") == "agent" and not from_pack:
        from app.services.packs import use_pack_denied

        return use_pack_denied()
    if title is not None:
        t = _title(title)
        if not t:
            return 400, {"ok": False, "error": "need_title"}
        row.title = t
    if markdown is not None:
        row.body_blob = crypto.encrypt(markdown, aad=f"draft:{did}")
        row.content_hash = crypto.content_hash(markdown)
    row.updated_at = _now()
    db.flush()
    return get_draft(db, crypto, principal, did)


def publish_draft(
    db: Session, principal: Principal, did: str, usernames: list[str] | None
) -> tuple[int, dict]:
    row = _owned(db, principal, did)
    if not row:
        return 404, {"ok": False, "error": "not_found"}
    if row.status != TAG_DRAFT:
        return 400, {"ok": False, "error": "already_public"}
    known = set(db.scalars(select(User.username)))
    picked: set[str] = set()
    for raw in usernames or []:
        u = (raw or "").strip()
        if u in known:
            picked.add(u)
    db.execute(sql_delete(DraftViewer).where(DraftViewer.draft_id == did))
    if known and picked >= known:
        row.audience = AUDIENCE_ALL
    else:
        row.audience = AUDIENCE_LISTED
        for u in sorted(picked):
            if u == principal.username:
                continue
            db.add(DraftViewer(draft_id=did, username=u))
    row.status = TAG_PUBLIC
    row.updated_at = _now()
    db.flush()
    return 200, {"ok": True, "id": did, "status": TAG_PUBLIC, "audience": row.audience}


def add_comment(
    db: Session, principal: Principal, did: str, body: str, quote: str | None = None
) -> tuple[int, dict]:
    row = db.get(Draft, did)
    if not row or not _can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    text = (body or "").strip()
    if not text:
        return 400, {"ok": False, "error": "need_body"}
    q = (quote or "").strip() or None
    c = DraftComment(draft_id=did, author=principal.username, body=text, quote=q)
    db.add(c)
    db.flush()
    return 201, {"ok": True, "id": c.id}


def record_view(db: Session, principal: Principal, did: str) -> tuple[int, dict]:
    row = db.get(Draft, did)
    if not row or not _can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    return views_svc.record_view(
        db,
        principal,
        kind=views_svc.KIND_DRAFT,
        product="",
        resource_id=did,
        version="",
        title=row.title,
    )


def list_views(db: Session, principal: Principal, did: str) -> tuple[int, dict]:
    row = db.get(Draft, did)
    if not row or not _can_view(db, principal, row):
        return 404, {"ok": False, "error": "not_found"}
    return views_svc.list_views(
        db, principal, kind=views_svc.KIND_DRAFT, product="", resource_id=did, version=""
    )


def delete_draft(db: Session, principal: Principal, did: str) -> tuple[int, dict]:
    row = _owned(db, principal, did)
    if not row:
        return 404, {"ok": False, "error": "not_found"}
    db.execute(sql_delete(DraftComment).where(DraftComment.draft_id == did))
    db.execute(sql_delete(DraftViewer).where(DraftViewer.draft_id == did))
    db.delete(row)
    db.flush()
    return 200, {"ok": True}
