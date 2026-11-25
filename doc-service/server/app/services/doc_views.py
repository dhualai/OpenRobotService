from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import DocumentView, Draft, Module, Requirement, User, VisitLog
from app.policy import Principal, can_browse_module, can_protect, can_view_product
from app.services.names import map_db
from app.services.topology import module_view

KIND_MODULE = "module"
KIND_REQUIREMENT = "requirement"
KIND_LOGIN = "login"
KIND_HOME = "home"
KIND_DRAFT = "draft"
COOLDOWN = timedelta(seconds=120)
KEEP_DAYS = 7
KIND_LABEL = {
    KIND_LOGIN: "登录",
    KIND_MODULE: "浏览",
    KIND_REQUIREMENT: "浏览",
    KIND_HOME: "浏览",
    KIND_DRAFT: "浏览",
}
PRODUCT_LABEL = {"usp": "USP", "ors": "ORS"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def purge_visits(db: Session, *, days: int = KEEP_DAYS) -> None:
    keep = max(1, int(days or KEEP_DAYS))
    cutoff = _now() - timedelta(days=keep)
    db.execute(sql_delete(VisitLog).where(VisitLog.at < cutoff))


def _clip_title(title: str) -> str:
    return (title or "").strip()[:200]


def _admin_usernames(db: Session) -> set[str]:
    return set(db.scalars(select(User.username).where(User.role == "admin")))


def _skip_actor(db: Session, username: str | None = None, principal: Principal | None = None) -> bool:
    if principal is not None and can_protect(principal):
        return True
    u = (username or "").strip()
    return bool(u and u in _admin_usernames(db))


def _log_visit(
    db: Session,
    *,
    username: str,
    kind: str,
    product: str | None = None,
    resource_id: str = "",
    version: str = "",
    title: str = "",
    at: datetime | None = None,
) -> None:
    db.add(
        VisitLog(
            at=at or _now(),
            username=username,
            kind=kind,
            product=product,
            resource_id=resource_id or "",
            version=version or "",
            title=_clip_title(title),
        )
    )


def record_view(
    db: Session,
    principal: Principal,
    *,
    kind: str,
    product: str,
    resource_id: str,
    version: str = "",
    title: str = "",
) -> tuple[int, dict]:
    if _skip_actor(db, principal=principal):
        return 200, {"ok": True}
    ver = (version or "").strip()
    row = db.scalar(
        select(DocumentView).where(
            DocumentView.kind == kind,
            DocumentView.product == product,
            DocumentView.resource_id == resource_id,
            DocumentView.version == ver,
            DocumentView.username == principal.username,
        )
    )
    now = _now()
    bumped = False
    if row is None:
        db.add(
            DocumentView(
                kind=kind,
                product=product,
                resource_id=resource_id,
                version=ver,
                username=principal.username,
                view_count=1,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        bumped = True
    else:
        last = _aware(row.last_seen_at)
        if last is None or now - last >= COOLDOWN:
            row.view_count = int(row.view_count or 0) + 1
            bumped = True
        row.last_seen_at = now
    if bumped:
        _log_visit(
            db,
            username=principal.username,
            kind=kind,
            product=product,
            resource_id=resource_id,
            version=ver,
            title=title,
            at=now,
        )
    return 200, {"ok": True}


def list_views(
    db: Session,
    principal: Principal,
    *,
    kind: str,
    product: str,
    resource_id: str,
    version: str = "",
) -> tuple[int, dict]:
    if not can_protect(principal):
        return 403, {"ok": False, "error": "forbidden"}
    ver = (version or "").strip()
    rows = list(
        db.scalars(
            select(DocumentView)
            .where(
                DocumentView.kind == kind,
                DocumentView.product == product,
                DocumentView.resource_id == resource_id,
                DocumentView.version == ver,
            )
            .order_by(
                DocumentView.view_count.desc(),
                DocumentView.last_seen_at.desc(),
                DocumentView.username.asc(),
            )
        )
    )
    unames = {r.username for r in rows}
    admins = _admin_usernames(db)
    rows = [r for r in rows if r.username not in admins]
    names: dict[str, str] = {}
    if unames:
        names = {
            u.username: (u.display_name or "").strip() or u.username
            for u in db.scalars(select(User).where(User.username.in_(unames)))
        }
    items = [
        {
            "username": r.username,
            "name": names.get(r.username) or r.username,
            "count": int(r.view_count or 0),
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
        }
        for r in rows
    ]
    return 200, {
        "ok": True,
        "total_views": sum(x["count"] for x in items),
        "items": items,
    }


def record_module_view(
    db: Session,
    principal: Principal,
    product: str,
    module_id: str,
    version: str | None,
    title: str = "",
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if not can_browse_module(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    ver = (version or "").strip() or (m.current_version or "")
    return record_view(
        db,
        principal,
        kind=KIND_MODULE,
        product=product,
        resource_id=module_id,
        version=ver,
        title=title or module_id,
    )


def list_module_views(
    db: Session, principal: Principal, product: str, module_id: str, version: str | None
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if not can_browse_module(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    ver = (version or "").strip() or (m.current_version or "")
    return list_views(
        db, principal, kind=KIND_MODULE, product=product, resource_id=module_id, version=ver
    )


def record_requirement_view(
    db: Session, principal: Principal, product: str, req_id: str, title: str = ""
) -> tuple[int, dict]:
    row = db.get(Requirement, req_id)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    return record_view(
        db,
        principal,
        kind=KIND_REQUIREMENT,
        product=product,
        resource_id=req_id,
        version="",
        title=title or row.title or req_id,
    )


def list_requirement_views(
    db: Session, principal: Principal, product: str, req_id: str
) -> tuple[int, dict]:
    row = db.get(Requirement, req_id)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    return list_views(
        db, principal, kind=KIND_REQUIREMENT, product=product, resource_id=req_id, version=""
    )


def record_login(db: Session, username: str) -> None:
    if not (username or "").strip():
        return
    if _skip_actor(db, username=username):
        return
    _log_visit(db, username=username.strip(), kind=KIND_LOGIN)


def record_home_view(db: Session, principal: Principal, product: str) -> tuple[int, dict]:
    if not can_view_product(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    return record_view(
        db,
        principal,
        kind=KIND_HOME,
        product=product,
        resource_id="",
        version="",
        title=PRODUCT_LABEL.get(product, product.upper()),
    )


def _live_title(db: Session, crypto: ContentCrypto | None, row: VisitLog) -> str:
    stored = (row.title or "").strip()
    if row.kind == KIND_LOGIN:
        return stored or "登录"
    if row.kind == KIND_HOME:
        return stored or PRODUCT_LABEL.get(row.product or "", (row.product or "").upper())
    if row.kind == KIND_REQUIREMENT and row.resource_id:
        req = db.get(Requirement, row.resource_id)
        if req and (req.title or "").strip():
            return req.title.strip()
        return stored or row.resource_id
    if row.kind == KIND_DRAFT and row.resource_id:
        d = db.get(Draft, row.resource_id)
        if d and (d.title or "").strip():
            return d.title.strip()
        return stored or row.resource_id
    if row.kind == KIND_MODULE and row.product and row.resource_id:
        m = db.scalar(select(Module).where(Module.product == row.product, Module.id == row.resource_id))
        if m and m.title_blob and crypto is not None:
            try:
                t = crypto.decrypt_text(m.title_blob, aad=f"module.title:{row.product}:{m.id}")
                if t.strip():
                    return t.strip()
            except Exception:
                pass
        return stored or row.resource_id
    return stored or KIND_LABEL.get(row.kind, row.kind)


def _href(row: VisitLog) -> str | None:
    prod = (row.product or "").strip()
    rid = (row.resource_id or "").strip()
    if row.kind == KIND_MODULE and prod and rid:
        q = f"?v={row.version}" if (row.version or "").strip() else ""
        return f"/{prod}/m/{rid}{q}"
    if row.kind == KIND_REQUIREMENT and prod and rid:
        return f"/{prod}/requirements/{rid}"
    if row.kind == KIND_HOME and prod:
        return f"/{prod}"
    if row.kind == KIND_DRAFT and rid:
        return f"/{prod or 'usp'}/drafts/{rid}"
    return None


def list_visits(
    db: Session,
    principal: Principal,
    crypto: ContentCrypto | None = None,
    *,
    days: int = KEEP_DAYS,
    limit: int = 500,
) -> tuple[int, dict]:
    if not can_protect(principal):
        return 403, {"ok": False, "error": "forbidden"}
    days = max(1, min(int(days or KEEP_DAYS), KEEP_DAYS))
    limit = max(1, min(int(limit or 500), 500))
    purge_visits(db, days=days)
    cutoff = _now() - timedelta(days=days)
    admins = _admin_usernames(db)
    q = select(VisitLog).where(VisitLog.at >= cutoff)
    if admins:
        q = q.where(VisitLog.username.notin_(admins))
    rows = list(db.scalars(q.order_by(VisitLog.at.desc(), VisitLog.id.desc()).limit(limit)))
    labels = map_db(db)
    items = [
        {
            "id": r.id,
            "at": r.at.isoformat() if r.at else None,
            "username": r.username,
            "name": labels.get(r.username, r.username),
            "kind": r.kind,
            "kind_label": KIND_LABEL.get(r.kind, r.kind),
            "product": r.product,
            "title": _live_title(db, crypto, r),
            "href": _href(r),
        }
        for r in rows
    ]
    return 200, {"ok": True, "days": days, "items": items}
