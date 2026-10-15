from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.infra.db import get_db
from app.infra.models import User
from app.infra.crypto import ContentCrypto
from app.policy import (
    Principal,
    can_create_version,
    can_delete,
    can_edit_requirements,
    can_log_hours,
    can_manage_users,
    can_protect,
    can_view_product,
)
from app.policy.engine import DOC_EDITOR_ROLES
from app.services import auth as auth_svc
from app.services import hours as hours_svc
from app.services import packs as packs_svc
from app.services import doc_views as views_svc
from app.doc_updates import migrated_url
from app.settings import get_settings

router = APIRouter()


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    cookie = request.cookies.get(get_settings().session_cookie)
    p = auth_svc.session_principal(db, cookie)
    if p:
        return p
    return auth_svc.agent_principal(db, request.headers.get("Authorization"))


def require_user(p: Principal | None = Depends(current_principal)) -> Principal:
    if not p:
        raise HTTPException(401, detail="未登录")
    return p


def _http_origin(raw: str) -> str | None:
    u = urlparse((raw or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc or u.username or u.password:
        return None
    return f"{u.scheme}://{u.netloc}"


def site_url_for_pack(request: Request) -> str:
    configured = _http_origin(get_settings().topo_public_url)
    if configured:
        return configured
    for raw in (
        request.headers.get("x-topo-origin"),
        request.headers.get("origin"),
        request.headers.get("referer"),
    ):
        found = _http_origin(raw or "")
        if found:
            return found
    return str(request.base_url).rstrip("/")


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    user = db.get(User, body.username.strip())
    if not user or not auth_svc.verify_password(body.password, user.password_hash):
        raise HTTPException(401, detail="invalid_credentials")
    raw = auth_svc.create_session(db, user.username, get_settings().session_hours)
    views_svc.record_login(db, user.username)
    response.set_cookie(
        get_settings().session_cookie,
        raw,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=get_settings().session_hours * 3600,
    )
    return {"ok": True, "username": user.username, "role": user.role}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    auth_svc.destroy_session(db, request.cookies.get(get_settings().session_cookie))
    response.delete_cookie(get_settings().session_cookie)
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(require_user), product: str = "usp"):
    if product not in ("usp", "ors"):
        product = "usp"
    return {
        "ok": True,
        "user": {
            "username": p.username,
            "name": p.name or p.username,
            "role": p.role,
            "products": sorted(p.products),
        },
        "can_view": can_view_product(p, product),
        "week": hours_svc.iso_week_id(),
        "can_log_hours": can_log_hours(p, product),
        "can_edit_requirements": can_edit_requirements(p),
        "can_manage_users": can_manage_users(p),
        "can_delete": can_delete(p),
        "can_protect": can_protect(p),
        "can_product_versions": p.role in ("engineer", "tester", "pm", "pjm") or can_create_version(p, product),
        "can_auth_pack": p.role in DOC_EDITOR_ROLES,
        "migrated_url": migrated_url(),
        "grants": [{"product": a, "kind": b, "resource": c} for a, b, c in sorted(p.grants)],
    }


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


@router.post("/account/password")
def password(body: PasswordIn, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    user = db.get(User, p.username)
    if not user or not auth_svc.verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, detail="bad_password")
    user.password_hash = auth_svc.hash_password(body.new_password)
    return {"ok": True}


@router.post("/account/agent-token")
def agent_token(p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    raw = auth_svc.issue_agent_token(db, p.username)
    return {"ok": True, "token": raw}


@router.get("/account/auth-pack")
def auth_pack(request: Request, p: Principal = Depends(require_user), db: Session = Depends(get_db)):
    if p.role not in DOC_EDITOR_ROLES:
        raise HTTPException(403, detail="forbidden")
    raw = auth_svc.issue_agent_token(db, p.username)
    blob = packs_svc.build_auth_pack(
        db,
        ContentCrypto(get_settings().topo_content_key),
        p,
        raw,
        site_url_for_pack(request),
    )
    fname = f"topo-agent-auth-{p.username}.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"},
    )
