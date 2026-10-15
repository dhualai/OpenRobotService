from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.db import get_db
from app.policy import (
    Principal,
    can_create_version,
    can_edit_product_field,
    can_manage_users,
    can_publish_version,
    can_view_product,
)
from app.services import audit as audit_svc
from app.services import boards as boards_svc
from app.services import hours as hours_svc
from app.services import modules as modules_svc
from app.services import packs as packs_svc
from app.services import releases as rel_svc
from app.services import requirements as req_svc
from app.services import doc_views as views_svc
from app.services import topology as topo_svc
from app.services import users as users_svc
from app.services import usp_test as usp_test_svc
from app.services.inline_media import get as media_get
from app.settings import get_settings
from app.api.v1.auth import require_user

router = APIRouter()


def crypto() -> ContentCrypto:
    return ContentCrypto(get_settings().topo_content_key)


def _product(p: Principal, product: str) -> str:
    if product not in ("usp", "ors"):
        raise HTTPException(400, detail="bad_product")
    if not can_view_product(p, product):
        raise HTTPException(403, detail="forbidden")
    return product


@router.get("/inline-media/{sha}")
def inline_media(sha: str, _p: Principal = Depends(require_user)):
    item = media_get(sha)
    if not item:
        raise HTTPException(404, detail="not_found")
    mime, data = item
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.get("/products/{product}/graph")
def graph(
    product: str,
    board: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    rel = topo_svc.resolve_board_rel(db, product, board)
    return topo_svc.graph_for(db, crypto(), p, product, rel)


@router.post("/products/{product}/test-sso")
def test_sso(product: str, p: Principal = Depends(require_user)):
    product = _product(p, product)
    if product != "usp":
        raise HTTPException(400, detail="bad_product")
    try:
        return usp_test_svc.issue_sso(p.username)
    except usp_test_svc.ButlerError as e:
        raise HTTPException(e.status, detail=e.detail) from None


@router.post("/products/{product}/modules/{module_id}/views")
def post_module_views(
    product: str,
    module_id: str,
    version: str | None = Query(None),
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    title = audit_svc.module_title(db, crypto(), product, module_id)
    code, out = views_svc.record_module_view(db, p, product, module_id, version, title=title)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/products/{product}/modules/{module_id}/views")
def get_module_views(
    product: str,
    module_id: str,
    version: str | None = Query(None),
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = views_svc.list_module_views(db, p, product, module_id, version)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/products/{product}/modules/{module_id}/nested-graph")
def nested_graph(
    product: str,
    module_id: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, body = topo_svc.nested_graph_for(db, crypto(), p, product, module_id)
    if code != 200:
        raise HTTPException(code, detail=body.get("error", "error"))
    return body


@router.get("/products/{product}/modules/{module_id}")
def module_page(
    product: str,
    module_id: str,
    version: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, body = topo_svc.get_module_page(db, crypto(), p, product, module_id, version)
    if code != 200:
        raise HTTPException(code, detail=body.get("error", "error"))
    kind = body.get("kind")
    if kind == "product_release":
        rid = body.get("release_id")
        body["hours"] = rel_svc.release_hours(db, product, rid, page_id=module_id) if rid else None
        body["can_log_hours"] = False
    elif kind in ("product_hub", "metrics"):
        body["hours"] = None
        body["can_log_hours"] = False
    else:
        ver = body.get("version")
        hrs = hours_svc.feature_hours(
            db, product, module_id, ver if isinstance(ver, str) else None, principal=p, crypto=crypto()
        )
        body["hours"] = hrs
        body["can_log_hours"] = bool(hrs.get("can_register"))
    return body


class DocIn(BaseModel):
    markdown: str
    version: str | None = None
    snapshot: bool = False


class LifecycleIn(BaseModel):
    band: str
    progress: float | None = None
    version: str | None = None


class ProtectIn(BaseModel):
    protected: bool


@router.put("/products/{product}/modules/{module_id}/protect")
def put_module_protect(
    product: str,
    module_id: str,
    body: ProtectIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = modules_svc.set_module_protected(db, p, product, module_id, body.protected)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="module.protect" if body.protected else "module.unprotect",
        product=product,
        resource=module_id,
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="保护模块" if body.protected else "取消保护模块",
    )
    return out


@router.put("/products/{product}/modules/{module_id}/versions/{version}/protect")
def put_version_protect(
    product: str,
    module_id: str,
    version: str,
    body: ProtectIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = modules_svc.set_version_protected(db, p, product, module_id, version, body.protected)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="version.protect" if body.protected else "version.unprotect",
        product=product,
        resource=f"{module_id}:{version}",
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="保护版本" if body.protected else "取消保护版本",
        extra={"version": version},
    )
    return out


@router.delete("/products/{product}/modules/{module_id}/versions/{version}")
def delete_module_version(
    product: str,
    module_id: str,
    version: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = modules_svc.delete_module_version(db, p, product, module_id, version)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="version.delete",
        product=product,
        resource=f"{module_id}:{version}",
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="删除功能版本",
        extra={"version": version},
    )
    return out


class FeatureVersionIn(BaseModel):
    semver: str


@router.post("/products/{product}/modules/{module_id}/versions")
def post_module_version(
    product: str,
    module_id: str,
    body: FeatureVersionIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = modules_svc.create_module_version(db, crypto(), p, product, module_id, body.semver)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    ver = str(out.get("version") or body.semver)
    audit_svc.write(
        db,
        actor=p.username,
        action="version.create",
        product=product,
        resource=f"{module_id}:{ver}",
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="新建功能版本",
        extra={"version": ver},
    )
    return out


@router.put("/products/{product}/modules/{module_id}/document")
def put_doc(
    product: str,
    module_id: str,
    body: DocIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = topo_svc.save_document(
        db, crypto(), p, product, module_id, body.markdown, body.version, snapshot=body.snapshot
    )
    if code != 200:
        _raise_svc(code, out)
    c = crypto()
    audit_svc.write(
        db,
        actor=p.username,
        action="module.document",
        product=product,
        resource=module_id,
        title=audit_svc.module_title(db, c, product, module_id),
        summary="编辑正文",
        extra={"version": body.version},
    )
    return out


@router.put("/products/{product}/modules/{module_id}/lifecycle")
def put_lifecycle(
    product: str,
    module_id: str,
    body: LifecycleIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = topo_svc.save_lifecycle(
        db, p, product, module_id, band=body.band, progress=body.progress, version=body.version
    )
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    req_svc.sync_linked_completion(db, product, module_id)
    lc = out.get("lifecycle") if isinstance(out, dict) else None
    if isinstance(lc, dict):
        band, prog = lc.get("band"), lc.get("progress")
    else:
        band = out.get("band") if isinstance(out, dict) else body.band
        prog = out.get("progress") if isinstance(out, dict) else body.progress
    audit_svc.write(
        db,
        actor=p.username,
        action="module.lifecycle",
        product=product,
        resource=module_id,
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary=audit_svc.lifecycle_summary(str(band or ""), None if prog is None else float(prog)),
        extra={"band": band, "progress": prog, "version": body.version},
    )
    return out


class BoardIn(BaseModel):
    doc: dict


@router.put("/products/{product}/boards/{board_rel:path}")
def put_board(
    product: str,
    board_rel: str,
    body: BoardIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    from sqlalchemy import select

    from app.infra.models import Board

    old = db.scalar(select(Board).where(Board.product == product, Board.rel_path == board_rel))
    old_doc = dict(old.doc) if old and isinstance(old.doc, dict) else None
    try:
        out = boards_svc.save_board(db, crypto(), p, product, board_rel, body.doc)
    except boards_svc.BoardSaveError as e:
        raise HTTPException(e.code, detail=e.error) from e
    row = db.scalar(select(Board).where(Board.product == product, Board.rel_path == board_rel))
    saved_doc = dict(row.doc) if row and isinstance(row.doc, dict) else {}
    if not audit_svc.board_geom_equivalent(old_doc, saved_doc):
        title, _ = audit_svc.board_display(db, crypto(), product, board_rel)
        audit_svc.write(
            db,
            actor=p.username,
            action="board.save",
            product=product,
            resource=board_rel,
            title=title,
            summary=audit_svc.board_change_summary(old_doc, saved_doc),
        )
    return out


class HoursIn(BaseModel):
    feature_id: str
    version: str = "v1.0"
    hours: float | None = None
    expected_hours: float | None = None
    week: str | None = None


@router.put("/products/{product}/hours")
def put_hours(product: str, body: HoursIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    week = body.week or hours_svc.iso_week_id()
    if body.hours is None and body.expected_hours is None:
        raise HTTPException(400, detail="invalid_hours")
    out: dict = {"ok": True, "week": week}
    if body.expected_hours is not None:
        code, exp = hours_svc.set_expected(
            db,
            p,
            product=product,
            feature_id=body.feature_id,
            version=body.version,
            expected_hours=body.expected_hours,
        )
        if code != 200:
            raise HTTPException(code, detail=exp.get("error", "error"))
        out = {**out, **exp}
    if body.hours is not None:
        code, out_h = hours_svc.upsert_hours(
            db,
            p,
            product=product,
            week=week,
            feature_id=body.feature_id,
            version=body.version,
            hours=body.hours,
            hard_cap=get_settings().week_hard_cap,
        )
        if code != 200:
            raise HTTPException(code, detail=out_h.get("error", "error"))
        out = {**out, **out_h}
    return out


@router.get("/products/{product}/hours")
def list_hours(
    product: str,
    week: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    week = week or hours_svc.iso_week_id()
    return hours_svc.list_week(db, crypto(), p, product, week, get_settings().week_hard_cap)


@router.get("/products/{product}/hours/register")
def hours_register(
    product: str,
    week: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    week = week or hours_svc.iso_week_id()
    return hours_svc.list_registerable(db, crypto(), p, product, week)


class WeekSaveIn(BaseModel):
    week: str | None = None
    allocations: list[dict] = []
    remaining: list[dict] | None = None


@router.put("/products/{product}/hours/week")
def put_hours_week(
    product: str, body: WeekSaveIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    week = body.week or hours_svc.iso_week_id()
    code, out = hours_svc.save_week(
        db,
        p,
        product=product,
        week=week,
        allocations=body.allocations,
        remaining=body.remaining,
        hard_cap=get_settings().week_hard_cap,
    )
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/products/{product}/hours/history")
def hours_history(product: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    return hours_svc.list_history(db, crypto(), p, product, get_settings().week_hard_cap)


@router.get("/products/{product}/hours/summary")
def hours_summary(
    product: str,
    week: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    week = week or hours_svc.iso_week_id()
    return hours_svc.snake_summary(db, p, product, week)


@router.get("/products/{product}/hours/feature/{feature_id}")
def get_feature_hours(
    product: str,
    feature_id: str,
    version: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    return {"ok": True, **hours_svc.feature_hours(db, product, feature_id, version, principal=p, crypto=crypto())}


class TagsIn(BaseModel):
    stable: str | None = None
    latest: str | None = None
    dev: str | None = None


@router.get("/products/{product}/releases")
def releases(product: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    out = rel_svc.list_releases(db, product)
    out["can_create"] = can_create_version(p, product)
    out["can_publish"] = can_publish_version(p, product)
    out["can_assign_tags"] = can_publish_version(p, product)
    out["can_delete"] = can_create_version(p, product)
    return out


@router.put("/products/{product}/tags")
def put_tags(
    product: str,
    body: TagsIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    mapping = {k: (v if v else None) for k, v in body.model_dump(exclude_unset=True).items()}
    code, out = rel_svc.set_tags(db, p, product, mapping)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="product.tags",
        product=product,
        resource=product,
        title=audit_svc.product_label(product),
        summary="版本标签",
        extra={"tags": mapping},
    )
    return out


@router.get("/products/{product}/releases/{release_id}")
def get_release(
    product: str, release_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = rel_svc.get_release(db, crypto(), product, release_id, p)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


class NotesIn(BaseModel):
    added: str | None = None
    fixed: str | None = None
    remark: str | None = None


@router.put("/products/{product}/releases/{release_id}/notes")
def put_notes(
    product: str,
    release_id: str,
    body: NotesIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    labels = {"added": "新增内容", "fixed": "修复内容", "remark": "备注"}
    for key in fields:
        if not can_edit_product_field(p, labels[key]):
            raise HTTPException(403, detail="forbidden")
    code, out = rel_svc.save_notes(db, crypto(), p, product, release_id, fields)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    changed = "、".join(labels[k] for k in fields)
    audit_svc.write(
        db,
        actor=p.username,
        action="product.notes",
        product=product,
        resource=release_id,
        title=audit_svc.release_label(db, product, release_id),
        summary=changed or "编辑发布页",
    )
    return out


class ChangelogNotesIn(BaseModel):
    notes: list[dict]


@router.put("/products/{product}/releases/{release_id}/changelog-notes")
def put_changelog_notes(
    product: str,
    release_id: str,
    body: ChangelogNotesIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = rel_svc.save_changelog_notes(db, crypto(), p, product, release_id, body.notes)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="product.changelog",
        product=product,
        resource=release_id,
        title=audit_svc.release_label(db, product, release_id),
        summary="发版说明",
    )
    return out


class CoversIn(BaseModel):
    covers: list[dict]


@router.put("/products/{product}/releases/{release_id}/covers")
def put_covers(
    product: str,
    release_id: str,
    body: CoversIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = rel_svc.set_covers(db, p, product, release_id, body.covers)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="product.covers",
        product=product,
        resource=release_id,
        title=audit_svc.release_label(db, product, release_id),
        summary="涵盖功能",
    )
    return out


@router.post("/products/{product}/releases/{release_id}/publish")
def publish(product: str, release_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    code, out = rel_svc.publish(db, p, product, release_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="product.publish",
        product=product,
        resource=release_id,
        title=audit_svc.release_label(db, product, release_id),
        summary="发版",
    )
    return out


@router.get("/products/{product}/releases/{release_id}/hours")
def rel_hours(product: str, release_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    return rel_svc.release_hours(db, product, release_id)


class ReqIn(BaseModel):
    title: str
    markdown: str = ""


@router.get("/products/{product}/requirements")
def list_req(product: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    return req_svc.list_requirements(db, crypto(), product, p)


@router.post("/products/{product}/requirements", status_code=201)
def create_req(product: str, body: ReqIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    code, out = req_svc.create_requirement(db, crypto(), p, product, body.title, body.markdown)
    if code != 201:
        _raise_svc(code, out)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.create",
        product=product,
        resource=str(out.get("id") or ""),
        title=body.title,
        summary="新建需求",
    )
    return out


@router.get("/products/{product}/requirements/{req_id}")
def get_req(
    product: str,
    req_id: str,
    include_markdown: bool = Query(True),
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    _product(p, product)
    code, out = req_svc.get_requirement(db, crypto(), p, req_id, include_markdown=include_markdown)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/products/{product}/requirements/{req_id}/views")
def post_req_views(
    product: str,
    req_id: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = views_svc.record_requirement_view(db, p, product, req_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/products/{product}/requirements/{req_id}/views")
def get_req_views(
    product: str,
    req_id: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = views_svc.list_requirement_views(db, p, product, req_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


class ReqPatch(BaseModel):
    title: str | None = None
    markdown: str | None = None
    status: str | None = None
    priority: int | None = None


@router.patch("/products/{product}/requirements/{req_id}")
def patch_req(
    product: str, req_id: str, body: ReqPatch, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    _product(p, product)
    code, out = req_svc.update_requirement(
        db, crypto(), p, req_id, title=body.title, markdown=body.markdown, status=body.status, priority=body.priority
    )
    if code != 200:
        _raise_svc(code, out)
    bits = []
    if body.title is not None:
        bits.append("标题")
    if body.markdown is not None:
        bits.append("正文")
    if body.status is not None:
        bits.append(audit_svc.REQ_STATUS.get(body.status, body.status))
    if body.priority is not None:
        bits.append(f"P{body.priority}")
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.update",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or body.title or req_id,
        summary="、".join(bits) or "编辑需求",
    )
    return out


@router.get("/products/{product}/requirements/{req_id}/edit-token")
def req_edit_token(
    product: str,
    req_id: str,
    since: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = packs_svc.requirement_edit_token_status(db, p, product, req_id, since or "")
    if code != 200:
        _raise_svc(code, out)
    return out


@router.get("/products/{product}/requirements/{req_id}/pack")
def req_pack_export(
    product: str, req_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = packs_svc.export_requirement_pack(db, crypto(), p, product, req_id)
    if code != 200:
        _raise_svc(code, out if isinstance(out, dict) else {"error": "error"})
    return Response(
        content=out,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{req_id}.zip"'},
    )


@router.post("/products/{product}/requirements/{req_id}/pack")
async def req_pack_import(
    product: str,
    req_id: str,
    request: Request,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    raw = await request.body()
    code, out = packs_svc.import_requirement_pack(db, crypto(), p, product, req_id, raw)
    if code != 200:
        _raise_svc(code, out)
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="pack.import",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or req_id,
        summary="导入工程包",
    )
    return out


@router.delete("/products/{product}/requirements/{req_id}")
def delete_req(product: str, req_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    code, out = req_svc.delete_requirement(db, p, product, req_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.delete",
        product=product,
        resource=req_id,
        title=str(out.get("title") or req_id),
        summary="删除需求",
    )
    return out


class RestoreIn(BaseModel):
    revision_id: int


@router.get("/products/{product}/requirements/{req_id}/revisions")
def list_req_revisions(
    product: str, req_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = req_svc.list_revisions(db, p, product, req_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/products/{product}/requirements/{req_id}/revisions/restore")
def restore_req_revision(
    product: str,
    req_id: str,
    body: RestoreIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = req_svc.restore_revision(db, crypto(), p, product, req_id, body.revision_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.restore",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or req_id,
        summary="回退正文",
    )
    return out


@router.get("/products/{product}/review-people")
def review_people(product: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    product = _product(p, product)
    return req_svc.list_review_people(db, product)


class ReviewIn(BaseModel):
    usernames: list[str]


@router.post("/products/{product}/requirements/{req_id}/review")
def start_review(
    product: str, req_id: str, body: ReviewIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = req_svc.start_review(db, p, product, req_id, body.usernames)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.review",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or req_id,
        summary="发起评审",
    )
    return out


class VerdictIn(BaseModel):
    verdict: str
    reason: str | None = None


@router.post("/products/{product}/requirements/{req_id}/verdict")
def submit_verdict(
    product: str, req_id: str, body: VerdictIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = req_svc.submit_verdict(db, p, product, req_id, body.verdict, body.reason)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.verdict",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or req_id,
        summary={"pass": "通过", "clarify": "待明确", "reject": "拒绝"}.get(body.verdict, "评审"),
    )
    return out


@router.get("/inbox/reviews")
def inbox_reviews(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return req_svc.pending_reviews(db, p)


@router.get("/inbox/pending-links")
def inbox_pending_links(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    return req_svc.pending_links(db, p)


@router.post("/products/{product}/requirements/{req_id}/skip-link")
def skip_req_link(
    product: str, req_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = req_svc.skip_link(db, p, product, req_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    from app.infra.models import Requirement

    req_row = db.get(Requirement, req_id)
    audit_svc.write(
        db,
        actor=p.username,
        action="requirement.skip_link",
        product=product,
        resource=req_id,
        title=(req_row.title if req_row else None) or req_id,
        summary="确认无相关功能开发",
    )
    return out


class CommentIn(BaseModel):
    body: str
    quote: str | None = None


@router.post("/products/{product}/requirements/{req_id}/comments", status_code=201)
def comment(
    product: str, req_id: str, body: CommentIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    _product(p, product)
    code, out = req_svc.add_comment(db, p, req_id, body.body, body.quote)
    if code != 201:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


class LinksIn(BaseModel):
    requirement_ids: list[str]


@router.get("/products/{product}/features/{feature_id}/requirements")
def get_links(
    product: str,
    feature_id: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    return req_svc.list_feature_links(db, product, feature_id)


@router.put("/products/{product}/features/{feature_id}/requirements")
def links(
    product: str,
    feature_id: str,
    body: LinksIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = req_svc.set_links(db, p, feature_id, product, body.requirement_ids)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


def _raise_svc(code: int, out: dict | bytes) -> None:
    if not isinstance(out, dict):
        raise HTTPException(code, detail="error")
    err = out.get("error") or "error"
    extra = {k: out[k] for k in ("hint", "module_id", "message") if out.get(k)}
    if extra.get("hint") and "message" not in extra:
        extra["message"] = extra["hint"]
    if extra:
        raise HTTPException(code, detail={"error": err, **extra})
    raise HTTPException(code, detail=err)


@router.get("/products/{product}/writable-modules")
def writable_modules(
    product: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    return packs_svc.list_writable_modules(db, crypto(), p, product)


@router.get("/products/{product}/packs/{module_id}/edit-token")
def pack_edit_token(
    product: str,
    module_id: str,
    since: str | None = None,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = packs_svc.edit_token_status(db, p, product, module_id, since or "")
    if code != 200:
        _raise_svc(code, out)
    return out


@router.get("/products/{product}/packs/{module_id}")
def pack_export(
    product: str, module_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = packs_svc.export_pack(db, crypto(), p, product, module_id)
    if code != 200:
        _raise_svc(code, out)
    return Response(
        content=out,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{module_id}.zip"'},
    )


@router.post("/products/{product}/packs/{module_id}")
async def pack_import(
    product: str,
    module_id: str,
    request: Request,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    raw = await request.body()
    code, out = packs_svc.import_pack(db, crypto(), p, product, module_id, raw)
    if code != 200:
        _raise_svc(code, out)
    audit_svc.write(
        db,
        actor=p.username,
        action="pack.import",
        product=product,
        resource=module_id,
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="导入工程包",
    )
    return out


@router.get("/products/{product}/modules/{module_id}/revisions")
def list_revisions(
    product: str,
    module_id: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = topo_svc.list_revisions(db, p, product, module_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/products/{product}/modules/{module_id}/revisions/restore")
def restore_revision(
    product: str,
    module_id: str,
    body: RestoreIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = topo_svc.restore_revision(db, crypto(), p, product, module_id, body.revision_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="doc.restore",
        product=product,
        resource=f"{module_id}:{body.revision_id}",
        title=audit_svc.module_title(db, crypto(), product, module_id),
        summary="回退正文",
    )
    return out


class ReleaseCreateIn(BaseModel):
    semver: str
    copy_from: str | None = None


@router.post("/products/{product}/releases", status_code=201)
def create_release(
    product: str,
    body: ReleaseCreateIn,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = rel_svc.create_release(db, p, product, body.semver, body.copy_from)
    if code not in (200, 201):
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.delete("/products/{product}/releases/{release_id}")
def delete_release(
    product: str, release_id: str, p: Principal = Depends(require_user), db: Session = Depends(get_db)
):
    product = _product(p, product)
    code, out = rel_svc.delete_release(db, p, product, release_id)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    semver = str(out.get("semver") or "")
    audit_svc.write(
        db,
        actor=p.username,
        action="product.delete",
        product=product,
        resource=release_id,
        title=f"{audit_svc.product_label(product)} {semver}".strip(),
        summary="删除版本",
    )
    return out


@router.get("/admin/users")
def admin_users(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    if not can_manage_users(p):
        raise HTTPException(403, detail="forbidden")
    return users_svc.list_users(db)


class UserSaveIn(BaseModel):
    username: str
    role: str | None = None
    products: list[str] | None = None
    module_perms: dict | None = None
    password: str | None = None
    display_name: str | None = None


@router.post("/admin/users")
def save_user(body: UserSaveIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    if not can_manage_users(p):
        raise HTTPException(403, detail="forbidden")
    code, out = users_svc.save_user(
        db,
        username=body.username,
        role=body.role,
        products=body.products,
        module_perms=body.module_perms,
        password=body.password,
        display_name=body.display_name,
    )
    if code not in (200, 201):
        raise HTTPException(code, detail=out.get("error", "error"))
    audit_svc.write(
        db,
        actor=p.username,
        action="user.save",
        resource=body.username,
        title=body.username,
        summary="用户",
        extra={"role": body.role},
    )
    return out


@router.get("/admin/catalog")
def admin_catalog(
    product: str = "usp",
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not can_manage_users(p):
        raise HTTPException(403, detail="forbidden")
    if product not in ("usp", "ors"):
        raise HTTPException(400, detail="bad_product")
    return users_svc.pack_catalog(db, crypto(), product)


@router.get("/admin/audit")
def audit(
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=7),
    limit: int = Query(200, le=200),
):
    code, out = audit_svc.list_updates(db, p, crypto(), days=days, limit=limit)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.get("/admin/visits")
def visits(
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
    days: int = Query(7, ge=1, le=7),
    limit: int = Query(500, le=500),
):
    code, out = views_svc.list_visits(db, p, crypto(), days=days, limit=limit)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out


@router.post("/products/{product}/visits")
def post_product_visit(
    product: str,
    p: Principal = Depends(require_user),
    db: Session = Depends(get_db),
):
    product = _product(p, product)
    code, out = views_svc.record_home_view(db, p, product)
    if code != 200:
        raise HTTPException(code, detail=out.get("error", "error"))
    return out
