from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import (
    Module,
    ModuleVersion,
    Requirement,
    RequirementAssignee,
    RequirementComment,
    RequirementDoc,
    RequirementFeatureLink,
    RequirementReviewRound,
    RequirementReviewer,
    RequirementRevision,
    User,
    UserProduct,
)
from app.policy import (
    Principal,
    can_comment_requirement,
    can_delete_requirement,
    can_edit_requirement,
    can_edit_requirements,
    can_link_requirements,
    can_view_product,
    redact_title,
)
from app.services.hours import hours_progress_for_groups
from app.services.names import map_db
from app.services.topology import module_view

LINKABLE = frozenset({"pending_link", "developing"})
STATUSES = frozenset({"draft", "review", "pending_link", "developing", "done"})
STATUS_ALIAS = {"designing": "developing", "testing": "developing"}
VERDICTS = frozenset({"pass", "clarify", "reject"})
PRIORITY_MIN = 0
PRIORITY_MAX = 4
PRIORITY_DEFAULT = 2
TAG_PRODUCT = "product"
TAG_PROJECT = "project"


def normalize_req_tag(raw: str | None) -> str:
    t = (raw or "").strip().lower()
    if t in (TAG_PROJECT, "项目", "项目需求", "pjm", "pj"):
        return TAG_PROJECT
    return TAG_PRODUCT


def tag_for_creator_role(role: str | None) -> str:
    return TAG_PROJECT if (role or "").strip() == "pjm" else TAG_PRODUCT


def canon_status(raw: str | None) -> str:
    s = (raw or "draft").strip().lower()
    s = STATUS_ALIAS.get(s, s)
    return s if s in STATUSES else "draft"


def _req_id() -> str:
    return "req_" + secrets.token_hex(4)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _priority(row: Requirement) -> int:
    n = getattr(row, "priority", None)
    if n is None:
        return PRIORITY_DEFAULT
    return int(n)


def _touch(row: Requirement, username: str) -> None:
    row.updated_by = username
    row.updated_at = _now()


def _feature_titles(
    db: Session, crypto: ContentCrypto, principal: Principal, product: str, fids: list[str]
) -> dict[str, str]:
    if not fids:
        return {}
    out: dict[str, str] = {}
    for m in db.scalars(select(Module).where(Module.product == product, Module.id.in_(fids))):
        title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}") if m.title_blob else m.id
        shown = redact_title(principal, module_view(m), title)
        if shown:
            out[m.id] = shown
    return out


def _links_by_req(db: Session, product: str, rids: list[str] | None = None) -> dict[str, list[str]]:
    q = select(RequirementFeatureLink).where(RequirementFeatureLink.product == product)
    if rids is not None:
        if not rids:
            return {}
        q = q.where(RequirementFeatureLink.requirement_id.in_(rids))
    by: dict[str, list[str]] = {}
    for l in db.scalars(q):
        by.setdefault(l.requirement_id, []).append(l.feature_id)
    return by


def _feature_done(db: Session, product: str, fid: str) -> bool:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == fid))
    if not m:
        return False
    ver = m.current_version or "v1.0"
    mv = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product, ModuleVersion.module_id == fid, ModuleVersion.semver == ver
        )
    )
    return bool(mv and (mv.lifecycle_band or "") == "done")


def sync_linked_completion(db: Session, product: str, feature_id: str | None = None) -> None:
    if feature_id:
        rids = [
            l.requirement_id
            for l in db.scalars(
                select(RequirementFeatureLink).where(
                    RequirementFeatureLink.product == product, RequirementFeatureLink.feature_id == feature_id
                )
            )
        ]
    else:
        rids = [r.id for r in db.scalars(select(Requirement).where(Requirement.product == product))]
    by = _links_by_req(db, product, rids)
    for rid in rids:
        row = db.get(Requirement, rid)
        if not row or canon_status(row.status) != "developing":
            continue
        fids = by.get(rid) or []
        if len(fids) < 1:
            continue
        if all(_feature_done(db, product, fid) for fid in fids):
            row.status = "done"
            row.updated_at = _now()


def _round_rows(db: Session, rid: str) -> list[RequirementReviewRound]:
    return list(
        db.scalars(
            select(RequirementReviewRound)
            .where(RequirementReviewRound.requirement_id == rid)
            .order_by(RequirementReviewRound.id.asc())
        )
    )


def _open_round(db: Session, rid: str) -> RequirementReviewRound | None:
    return db.scalar(
        select(RequirementReviewRound)
        .where(RequirementReviewRound.requirement_id == rid, RequirementReviewRound.outcome.is_(None))
        .order_by(RequirementReviewRound.id.desc())
    )


def _reviewers_for_round(db: Session, round_id: int) -> list[RequirementReviewer]:
    return list(db.scalars(select(RequirementReviewer).where(RequirementReviewer.round_id == round_id)))


def _reviewer_rows(db: Session, rid: str) -> list[RequirementReviewer]:
    rnd = _open_round(db, rid)
    if rnd:
        return _reviewers_for_round(db, rnd.id)
    rounds = _round_rows(db, rid)
    if not rounds:
        return []
    return _reviewers_for_round(db, rounds[-1].id)


def _reviewers_out(rows: list[RequirementReviewer], labels: dict[str, str]) -> list[dict]:
    return [
        {
            "username": r.username,
            "name": labels.get(r.username, r.username),
            "verdict": r.verdict,
            "reason": r.reason,
            "decided_at": _iso(r.decided_at),
        }
        for r in rows
    ]


def _rounds_out(db: Session, rid: str, labels: dict[str, str]) -> list[dict]:
    out = []
    for rnd in _round_rows(db, rid):
        out.append(
            {
                "id": rnd.id,
                "started_by": labels.get(rnd.started_by, rnd.started_by),
                "started_at": _iso(rnd.started_at),
                "outcome": rnd.outcome,
                "reviewers": _reviewers_out(_reviewers_for_round(db, rnd.id), labels),
            }
        )
    return out


def _close_review_if_done(db: Session, row: Requirement) -> None:
    rnd = _open_round(db, row.id)
    if not rnd:
        return
    revs = _reviewers_for_round(db, rnd.id)
    if not revs or any(r.verdict is None for r in revs):
        return
    if all(r.verdict == "pass" for r in revs):
        rnd.outcome = "pass"
        row.status = "pending_link"
    else:
        rnd.outcome = "fail"
        row.status = "draft"
    rnd.closed_at = _now()
    row.updated_at = _now()


def _list_item(
    row: Requirement,
    principal: Principal,
    labels: dict[str, str],
    *,
    features: list[dict] | None = None,
    hours_ratio: float | None = None,
    hours_week: float | None = None,
) -> dict:
    st = canon_status(row.status)
    return {
        "id": row.id,
        "title": row.title,
        "status": st,
        "created_by": labels.get(row.created_by, row.created_by),
        "created_at": _iso(row.created_at),
        "updated_by": labels.get(row.updated_by, row.updated_by) if row.updated_by else None,
        "updated_at": _iso(row.updated_at),
        "priority": _priority(row),
        "tag": normalize_req_tag(row.tag),
        "can_edit": can_edit_requirement(principal, row.created_by),
        "can_delete": can_delete_requirement(principal, row.created_by),
        "can_start_review": st == "draft" and can_edit_requirement(principal, row.created_by),
        "features": features or [],
        "hours_ratio": hours_ratio if st == "developing" else None,
        "hours_week": hours_week if st == "developing" else None,
    }


def list_requirements(db: Session, crypto: ContentCrypto, product: str, principal: Principal) -> dict:
    rows = list(
        db.scalars(select(Requirement).where(Requirement.product == product).order_by(Requirement.updated_at.desc()))
    )
    labels = map_db(db)
    by = _links_by_req(db, product, [r.id for r in rows])
    all_fids = [fid for fids in by.values() for fid in fids]
    titles = _feature_titles(db, crypto, principal, product, all_fids)
    groups = {r.id: by.get(r.id, []) for r in rows if canon_status(r.status) == "developing"}
    stats = hours_progress_for_groups(db, product, groups) if groups else {}
    items = []
    for r in rows:
        fids = by.get(r.id, [])
        feats = [{"id": fid, "title": titles[fid]} for fid in fids if fid in titles]
        info = stats.get(r.id) or {}
        items.append(
            _list_item(
                r,
                principal,
                labels,
                features=feats,
                hours_ratio=info.get("ratio"),
                hours_week=info.get("week"),
            )
        )
    return {"ok": True, "items": items}


def create_requirement(
    db: Session, crypto: ContentCrypto, principal: Principal, product: str, title: str, markdown: str = ""
) -> tuple[int, dict]:
    if markdown and getattr(principal, "via", "session") == "agent":
        from app.services.packs import use_pack_denied

        return use_pack_denied()
    if not can_edit_requirements(principal):
        return 403, {"ok": False, "error": "forbidden"}
    rid = _req_id()
    db.add(
        Requirement(
            id=rid,
            product=product,
            title=title.strip() or "untitled",
            created_by=principal.username,
            updated_by=principal.username,
            priority=PRIORITY_DEFAULT,
            tag=tag_for_creator_role(principal.role),
        )
    )
    body = markdown or ""
    blob = crypto.encrypt(body, aad=f"requirement:{rid}")
    db.add(
        RequirementDoc(
            requirement_id=rid,
            body_blob=blob,
            content_hash=crypto.content_hash(body),
        )
    )
    return 201, {"ok": True, "id": rid}


def get_requirement(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    rid: str,
    *,
    include_markdown: bool = True,
) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirements(principal) and principal.role == "guest":
        return 403, {"ok": False, "error": "forbidden"}
    doc = db.get(RequirementDoc, rid)
    body = crypto.decrypt_text(doc.body_blob, aad=f"requirement:{rid}") if doc else ""
    from app.services.markdown_html import md_to_html

    ids_by_product = {
        "usp": set(db.scalars(select(Module.id).where(Module.product == "usp"))),
        "ors": set(db.scalars(select(Module.id).where(Module.product == "ors"))),
    }

    labels = map_db(db)
    comments = [
        {"id": c.id, "author": labels.get(c.author, c.author), "body": c.body, "quote": c.quote}
        for c in db.scalars(select(RequirementComment).where(RequirementComment.requirement_id == rid))
    ]
    fids = [
        l.feature_id
        for l in db.scalars(select(RequirementFeatureLink).where(RequirementFeatureLink.requirement_id == rid))
    ]
    titles = _feature_titles(db, crypto, principal, row.product, fids)
    features = [{"id": fid, "title": titles.get(fid, fid)} for fid in fids if fid in titles]
    st = canon_status(row.status)
    revs = _reviewer_rows(db, rid)
    mine = next((r for r in revs if r.username == principal.username), None)
    hours_ratio = None
    hours_week = None
    if st == "developing":
        info = hours_progress_for_groups(db, row.product, {rid: fids}).get(rid) or {}
        hours_ratio = info.get("ratio")
        hours_week = info.get("week")
    revision_count = int(
        db.scalar(
            select(func.count())
            .select_from(RequirementRevision)
            .where(RequirementRevision.requirement_id == rid)
        )
        or 0
    )
    return 200, {
        "ok": True,
        "id": row.id,
        "product": row.product,
        "title": row.title,
        "status": st,
        "created_by": labels.get(row.created_by, row.created_by),
        "updated_by": labels.get(row.updated_by, row.updated_by) if row.updated_by else None,
        "updated_at": _iso(row.updated_at),
        "priority": _priority(row),
        "tag": normalize_req_tag(row.tag),
        "markdown": body if include_markdown else "",
        "html": md_to_html(body, product=row.product, ids_by_product=ids_by_product),
        "comments": comments,
        "linked_feature_ids": fids,
        "features": features,
        "hours_ratio": hours_ratio,
        "hours_week": hours_week,
        "reviewers": _reviewers_out(revs, labels),
        "review_rounds": _rounds_out(db, rid, labels),
        "can_edit": can_edit_requirement(principal, row.created_by),
        "can_delete": can_delete_requirement(principal, row.created_by),
        "can_start_review": st == "draft" and can_edit_requirement(principal, row.created_by),
        "can_verdict": st == "review" and mine is not None,
        "can_skip_link": st == "pending_link" and can_edit_requirement(principal, row.created_by),
        "my_verdict": mine.verdict if mine else None,
        "open_comments": len(comments),
        "revision_count": revision_count,
        "content_hash": doc.content_hash if doc else "",
        "edit_token": _req_edit_token(doc.content_hash if doc else ""),
    }


def _req_edit_token(content_hash: str | None) -> str:
    from app.services.packs import compose_edit_token

    return compose_edit_token(content_hash)


def update_requirement(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    rid: str,
    *,
    title: str | None = None,
    markdown: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    from_pack: bool = False,
) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    if markdown is not None and getattr(principal, "via", "session") == "agent" and not from_pack:
        from app.services.packs import use_pack_denied

        return use_pack_denied()
    comments = list(db.scalars(select(RequirementComment).where(RequirementComment.requirement_id == rid)))
    if comments and markdown is not None and principal.role != "admin":
        return 409, {"ok": False, "error": "unresolved_comments"}
    if title is not None:
        row.title = title.strip() or row.title
    if status is not None and canon_status(status) != canon_status(row.status):
        return 400, {"ok": False, "error": "invalid_status"}
    if priority is not None:
        if not isinstance(priority, int) or isinstance(priority, bool) or not (PRIORITY_MIN <= priority <= PRIORITY_MAX):
            return 400, {"ok": False, "error": "invalid_priority"}
        row.priority = priority
    if markdown is not None:
        doc = db.get(RequirementDoc, rid)
        blob = crypto.encrypt(markdown, aad=f"requirement:{rid}")
        h = crypto.content_hash(markdown)
        if doc:
            if doc.content_hash != h:
                db.add(
                    RequirementRevision(
                        requirement_id=rid,
                        body_blob=doc.body_blob,
                        content_hash=doc.content_hash,
                        author=principal.username,
                    )
                )
            doc.body_blob = blob
            doc.content_hash = h
        else:
            db.add(RequirementDoc(requirement_id=rid, body_blob=blob, content_hash=h))
    _touch(row, principal.username)
    doc = db.get(RequirementDoc, rid)
    h = doc.content_hash if doc else ""
    return 200, {
        "ok": True,
        "id": rid,
        "status": canon_status(row.status),
        "priority": _priority(row),
        "content_hash": h,
        "edit_token": _req_edit_token(h),
    }


def delete_requirement(db: Session, principal: Principal, product: str, rid: str) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_delete_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    db.execute(sql_delete(RequirementFeatureLink).where(RequirementFeatureLink.requirement_id == rid))
    db.execute(sql_delete(RequirementComment).where(RequirementComment.requirement_id == rid))
    db.execute(sql_delete(RequirementAssignee).where(RequirementAssignee.requirement_id == rid))
    db.execute(sql_delete(RequirementReviewer).where(RequirementReviewer.requirement_id == rid))
    db.execute(sql_delete(RequirementReviewRound).where(RequirementReviewRound.requirement_id == rid))
    db.execute(sql_delete(RequirementRevision).where(RequirementRevision.requirement_id == rid))
    db.execute(sql_delete(RequirementDoc).where(RequirementDoc.requirement_id == rid))
    title = row.title
    db.flush()
    db.delete(row)
    return 200, {"ok": True, "id": rid, "title": title}


def list_revisions(db: Session, principal: Principal, product: str, rid: str) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirements(principal) and principal.role == "guest":
        return 403, {"ok": False, "error": "forbidden"}
    rows = list(
        db.scalars(
            select(RequirementRevision)
            .where(RequirementRevision.requirement_id == rid)
            .order_by(RequirementRevision.id.desc())
            .limit(20)
        )
    )
    labels = map_db(db)
    return 200, {
        "ok": True,
        "can_restore": can_edit_requirement(principal, row.created_by),
        "items": [
            {
                "id": r.id,
                "author": labels.get(r.author, r.author),
                "created_at": _iso(r.created_at),
                "content_hash": r.content_hash,
            }
            for r in rows
        ],
    }


def restore_revision(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    rid: str,
    revision_id: int,
) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    rev = db.get(RequirementRevision, revision_id)
    if not rev or rev.requirement_id != rid:
        return 404, {"ok": False, "error": "not_found"}
    body = crypto.decrypt_text(rev.body_blob, aad=f"requirement:{rid}")
    return update_requirement(db, crypto, principal, rid, markdown=body)


def add_comment(db: Session, principal: Principal, rid: str, body: str, quote: str | None = None) -> tuple[int, dict]:
    if not can_comment_requirement(principal):
        return 403, {"ok": False, "error": "forbidden"}
    if not db.get(Requirement, rid):
        return 404, {"ok": False, "error": "not_found"}
    c = RequirementComment(requirement_id=rid, author=principal.username, body=body, quote=quote)
    db.add(c)
    db.flush()
    return 201, {"ok": True, "id": c.id}


def list_review_people(db: Session, product: str) -> dict:
    names = set(
        db.scalars(
            select(UserProduct.username).where(UserProduct.product == product)
        )
    )
    rows = [
        u
        for u in db.scalars(select(User))
        if u.role != "guest" and (u.role == "admin" or u.username in names)
    ]
    rows.sort(key=lambda u: ((u.display_name or u.username), u.username))
    labels = map_db(db)
    return {
        "ok": True,
        "items": [{"username": u.username, "name": labels.get(u.username, u.username), "role": u.role} for u in rows],
    }


def start_review(db: Session, principal: Principal, product: str, rid: str, usernames: list[str]) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    if canon_status(row.status) != "draft":
        return 400, {"ok": False, "error": "not_draft"}
    wanted = []
    seen: set[str] = set()
    for raw in usernames:
        u = (raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        wanted.append(u)
    if not wanted:
        return 400, {"ok": False, "error": "need_reviewers"}
    people = {p["username"] for p in list_review_people(db, product)["items"]}
    if any(u not in people for u in wanted):
        return 400, {"ok": False, "error": "invalid_reviewer"}
    rnd = RequirementReviewRound(requirement_id=rid, started_by=principal.username, started_at=_now())
    db.add(rnd)
    db.flush()
    for u in wanted:
        db.add(RequirementReviewer(requirement_id=rid, round_id=rnd.id, username=u))
    row.status = "review"
    _touch(row, principal.username)
    return 200, {"ok": True, "id": rid, "status": "review"}


def submit_verdict(
    db: Session, principal: Principal, product: str, rid: str, verdict: str, reason: str | None = None
) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if canon_status(row.status) != "review":
        return 400, {"ok": False, "error": "not_review"}
    rnd = _open_round(db, rid)
    if not rnd:
        return 400, {"ok": False, "error": "not_review"}
    mine = db.scalar(
        select(RequirementReviewer).where(
            RequirementReviewer.round_id == rnd.id, RequirementReviewer.username == principal.username
        )
    )
    if not mine:
        return 403, {"ok": False, "error": "not_reviewer"}
    v = (verdict or "").strip().lower()
    if v not in VERDICTS:
        return 400, {"ok": False, "error": "invalid_verdict"}
    note = (reason or "").strip()
    if v != "pass" and not note:
        return 400, {"ok": False, "error": "need_reason"}
    mine.verdict = v
    mine.reason = note if v != "pass" else None
    mine.decided_at = _now()
    _close_review_if_done(db, row)
    _touch(row, principal.username)
    return 200, {"ok": True, "id": rid, "status": canon_status(row.status), "verdict": v}


def _can_see_pending_link(principal: Principal, row: Requirement) -> bool:
    if not can_view_product(principal, row.product):
        return False
    if can_edit_requirement(principal, row.created_by):
        return True
    return can_link_requirements(principal)


def pending_links(db: Session, principal: Principal) -> dict:
    items = []
    for req in db.scalars(
        select(Requirement).where(Requirement.status == "pending_link").order_by(Requirement.updated_at.desc())
    ):
        if canon_status(req.status) != "pending_link":
            continue
        if not _can_see_pending_link(principal, req):
            continue
        items.append(
            {
                "product": req.product,
                "id": req.id,
                "title": req.title,
                "can_skip": can_edit_requirement(principal, req.created_by),
            }
        )
    return {"ok": True, "items": items, "count": len(items)}


def skip_link(db: Session, principal: Principal, product: str, rid: str) -> tuple[int, dict]:
    row = db.get(Requirement, rid)
    if not row or row.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_requirement(principal, row.created_by):
        return 403, {"ok": False, "error": "forbidden"}
    if canon_status(row.status) != "pending_link":
        return 400, {"ok": False, "error": "not_pending_link"}
    row.status = "done"
    _touch(row, principal.username)
    return 200, {"ok": True, "id": rid, "status": "done"}


def pending_reviews(db: Session, principal: Principal) -> dict:
    q = (
        select(Requirement, RequirementReviewer)
        .join(RequirementReviewer, RequirementReviewer.requirement_id == Requirement.id)
        .join(RequirementReviewRound, RequirementReviewer.round_id == RequirementReviewRound.id)
        .where(
            RequirementReviewer.username == principal.username,
            RequirementReviewer.verdict.is_(None),
            RequirementReviewRound.outcome.is_(None),
            Requirement.status == "review",
        )
    )
    items = []
    for req, _rev in db.execute(q):
        if canon_status(req.status) != "review":
            continue
        items.append({"product": req.product, "id": req.id, "title": req.title})
    return {"ok": True, "items": items, "count": len(items)}


def set_links(db: Session, principal: Principal, feature_id: str, product: str, req_ids: list[str]) -> tuple[int, dict]:
    if not can_link_requirements(principal):
        return 403, {"ok": False, "error": "forbidden"}
    existing = {
        l.requirement_id
        for l in db.scalars(
            select(RequirementFeatureLink).where(
                RequirementFeatureLink.product == product, RequirementFeatureLink.feature_id == feature_id
            )
        )
    }
    for rid in req_ids:
        if rid in existing:
            continue
        row = db.get(Requirement, rid)
        if not row:
            continue
        if canon_status(row.status) not in LINKABLE:
            continue
        db.add(RequirementFeatureLink(requirement_id=rid, product=product, feature_id=feature_id))
        existing.add(rid)
        if canon_status(row.status) == "pending_link":
            row.status = "developing"
            row.updated_at = _now()
    db.flush()
    sync_linked_completion(db, product, feature_id)
    return 200, {"ok": True}


def list_feature_links(db: Session, product: str, feature_id: str) -> dict:
    rows = list(
        db.scalars(
            select(RequirementFeatureLink).where(
                RequirementFeatureLink.product == product,
                RequirementFeatureLink.feature_id == feature_id,
            )
        )
    )
    items = []
    for l in rows:
        req = db.get(Requirement, l.requirement_id)
        if not req:
            continue
        items.append({"id": req.id, "title": req.title, "status": canon_status(req.status)})
    return {"ok": True, "items": items}
