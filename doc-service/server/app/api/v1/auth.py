from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.infra.db import get_db
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
from app.services import ors_auth
from app.services import packs as packs_svc
from app.doc_updates import migrated_url
from app.settings import get_settings

router = APIRouter()


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal | None:
    """账号统一：只认摇人吧签发的 Bearer token，本地账密登录已关闭。"""
    bearer = request.headers.get("Authorization")
    p = ors_auth.principal_from_ors(db, bearer)
    if p:
        return p
    # Agent 授权包（topo-pack）通道保留
    return auth_svc.agent_principal(db, bearer)


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
    """本地账密登录已关闭：账号与权限统一由摇人吧登录态提供（见 app/services/ors_auth.py）。"""
    raise HTTPException(410, detail="local_login_disabled")


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """仅清理历史遗留的本地 session cookie；统一登录态的登出由摇人吧负责。"""
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
def password(body: PasswordIn, p: Principal = Depends(require_user)):
    """本地口令已废止：改密请到摇人吧账号体系操作。"""
    raise HTTPException(410, detail="local_password_disabled")


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
