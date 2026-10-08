"""可达 USP 内网环境：开发者模式 CRUD + 讨论区选项 + AI 内取 SSH 配置。

配置写在 usp_env 表。SSH 密码以 AES-GCM 密文入库，管理接口不返回明文。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from Crypto.Cipher import AES
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal, engine
from app.integrations.api import verify_sync_api_key
from app.models.usp_env import UspEnv
from app.modules.admin.api.auth import require_permission
from app.modules.admin.api.dispatch_dev import PERM, ensure_dispatch_dev_permission
from app.modules.admin.schemas.response import DataResponse

admin_router = APIRouter(prefix="/dispatch-dev/usp-envs", tags=["admin-dispatch-dev-usp-envs"])
public_router = APIRouter(prefix="/usp-envs", tags=["usp-envs"])

_table_ready = False
_ENC_PREFIX = "enc1:"


def _aes_key() -> bytes:
    secret = (settings.SECRET_KEY or "").encode("utf-8")
    if len(secret) < 16:
        raise HTTPException(status_code=500, detail="服务未配置密钥，不能保存 SSH 密码")
    return hashlib.sha256(b"usp-env-ssh-v1\0" + secret).digest()


def _encrypt_secret(plain: Optional[str]) -> Optional[str]:
    text = (plain or "").strip()
    if not text:
        return None
    nonce = secrets.token_bytes(12)
    cipher = AES.new(_aes_key(), AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(text.encode("utf-8"))
    blob = base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")
    return _ENC_PREFIX + blob


def _decrypt_secret(stored: Optional[str]) -> str:
    if not stored:
        return ""
    if not str(stored).startswith(_ENC_PREFIX):
        raise HTTPException(status_code=500, detail="SSH 密码无法解密，请在可达环境里重新填写")
    try:
        raw = base64.urlsafe_b64decode(str(stored)[len(_ENC_PREFIX):].encode("ascii"))
        nonce, ciphertext, tag = raw[:12], raw[12:-16], raw[-16:]
        cipher = AES.new(_aes_key(), AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="SSH 密码无法解密，请在可达环境里重新填写")


class UspEnvCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    code: Optional[str] = Field(None, max_length=64)
    enabled: bool = True
    notes: Optional[str] = None
    project_id: Optional[str] = Field(None, max_length=64, description="预留关联项目")
    ssh_host: str = Field(..., min_length=1, max_length=255)
    ssh_port: int = Field(22, ge=1, le=65535)
    ssh_user: str = Field(..., min_length=1, max_length=128)
    ssh_auth_type: str = Field("password", description="key | password")
    ssh_private_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_connect_timeout_s: float = Field(8.0, ge=1.0, le=120.0)
    export_script: str = Field(..., min_length=1, max_length=512)
    export_workdir: str = Field(..., min_length=1, max_length=512)
    log_interval_min: int = Field(15, ge=1, le=1440)
    docker_container: Optional[str] = Field(None, max_length=128, description="Docker 容器名，如 usp_app")
    docker_sudo: bool = Field(False, description="宿主机执行 docker 是否加 sudo（需 NOPASSWD）")
    capabilities: Optional[List[str]] = None


class UspEnvUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    code: Optional[str] = Field(None, max_length=64)
    enabled: Optional[bool] = None
    notes: Optional[str] = None
    project_id: Optional[str] = Field(None, max_length=64)
    ssh_host: Optional[str] = Field(None, min_length=1, max_length=255)
    ssh_port: Optional[int] = Field(None, ge=1, le=65535)
    ssh_user: Optional[str] = Field(None, min_length=1, max_length=128)
    ssh_auth_type: Optional[str] = None
    ssh_private_key_path: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_connect_timeout_s: Optional[float] = Field(None, ge=1.0, le=120.0)
    export_script: Optional[str] = Field(None, min_length=1, max_length=512)
    export_workdir: Optional[str] = Field(None, min_length=1, max_length=512)
    log_interval_min: Optional[int] = Field(None, ge=1, le=1440)
    docker_container: Optional[str] = Field(None, max_length=128)
    docker_sudo: Optional[bool] = None
    capabilities: Optional[List[str]] = None
    clear_password: bool = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fmt_dt(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_auth(v: Optional[str]) -> str:
    t = (v or "password").strip().lower()
    if t not in ("key", "password"):
        raise HTTPException(status_code=422, detail="ssh_auth_type 须为 key 或 password")
    return t


def _default_caps(caps: Optional[List[str]]) -> List[str]:
    if caps is None:
        return ["ssh_export_logs"]
    out = [str(c).strip() for c in caps if str(c).strip()]
    return out or ["ssh_export_logs"]


def _blank(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    UspEnv.__table__.create(bind=engine, checkfirst=True)
    _table_ready = True


def _open() -> Session:
    _ensure_table()
    return SessionLocal()


def _caps(row: UspEnv) -> List[str]:
    raw = row.capabilities
    if isinstance(raw, list):
        return [str(c) for c in raw if str(c).strip()]
    return []


def _public_dict(row: UspEnv) -> Dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name or "",
        "code": row.code,
        "enabled": bool(row.enabled),
        "project_id": row.project_id,
        "capabilities": _caps(row),
        "notes": row.notes,
    }


def _admin_dict(row: UspEnv) -> Dict[str, Any]:
    data = _public_dict(row)
    data.update({
        "ssh_host": row.ssh_host or "",
        "ssh_port": int(row.ssh_port or 22),
        "ssh_user": row.ssh_user or "",
        "ssh_auth_type": row.ssh_auth_type or "password",
        "ssh_private_key_path": row.ssh_private_key_path,
        "ssh_password_set": bool(row.ssh_password),
        "ssh_connect_timeout_s": float(row.ssh_connect_timeout_s or 8.0),
        "export_script": row.export_script or "",
        "export_workdir": row.export_workdir or "",
        "log_interval_min": int(row.log_interval_min or 15),
        "docker_container": row.docker_container or "",
        "docker_sudo": bool(row.docker_sudo),
        "created_at": _fmt_dt(row.created_at),
        "updated_at": _fmt_dt(row.updated_at),
    })
    return data


def _ssh_dict(row: UspEnv) -> Dict[str, Any]:
    return {
        "id": int(row.id),
        "name": row.name or "",
        "enabled": bool(row.enabled),
        "capabilities": _caps(row),
        "ssh_host": row.ssh_host or "",
        "ssh_port": int(row.ssh_port or 22),
        "ssh_user": row.ssh_user or "",
        "ssh_auth_type": row.ssh_auth_type or "password",
        "ssh_private_key_path": row.ssh_private_key_path or "",
        "ssh_password": _decrypt_secret(row.ssh_password),
        "ssh_connect_timeout_s": float(row.ssh_connect_timeout_s or 8.0),
        "export_script": row.export_script or "",
        "export_workdir": row.export_workdir or "",
        "log_interval_min": int(row.log_interval_min or 15),
        "docker_container": (row.docker_container or "").strip(),
        "docker_sudo": bool(row.docker_sudo),
    }


def _validate_ssh_ready(auth: str, key_path: Optional[str], password: Optional[str], *, require_secret: bool) -> None:
    if auth == "key":
        if require_secret and not (key_path or "").strip():
            raise HTTPException(status_code=422, detail="key 认证需要 ssh_private_key_path")
    else:
        if require_secret and not (password or "").strip():
            raise HTTPException(status_code=422, detail="password 认证需要 ssh_password")


def _code_taken(db: Session, code: str, except_id: Optional[int] = None) -> bool:
    query = db.query(UspEnv.id).filter(UspEnv.code == code)
    if except_id is not None:
        query = query.filter(UspEnv.id != except_id)
    return query.first() is not None


def _commit(db: Session, code: Optional[str]) -> None:
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        if code:
            raise HTTPException(status_code=409, detail=f"code 已存在: {code}") from e
        raise HTTPException(status_code=409, detail="环境保存冲突") from e


# ── 开发者模式 CRUD ──

@admin_router.get("", response_model=DataResponse, summary="USP 环境列表")
async def list_envs(
    enabled: Optional[bool] = None,
    project_id: Optional[str] = None,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    db = _open()
    try:
        query = db.query(UspEnv)
        if enabled is not None:
            query = query.filter(UspEnv.enabled == bool(enabled))
        if project_id and project_id.strip():
            query = query.filter(UspEnv.project_id == project_id.strip())
        rows = query.order_by(UspEnv.id.desc()).limit(200).all()
        data = [_admin_dict(row) for row in rows]
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.post("", response_model=DataResponse, summary="新建 USP 环境")
async def create_env(
    body: UspEnvCreate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    auth = _norm_auth(body.ssh_auth_type)
    _validate_ssh_ready(auth, body.ssh_private_key_path, body.ssh_password, require_secret=True)
    code = _blank(body.code)
    now = _utcnow()
    db = _open()
    try:
        if code and _code_taken(db, code):
            raise HTTPException(status_code=409, detail=f"code 已存在: {code}")
        row = UspEnv(
            name=body.name.strip(),
            code=code,
            enabled=bool(body.enabled),
            notes=_blank(body.notes),
            project_id=_blank(body.project_id),
            ssh_host=body.ssh_host.strip(),
            ssh_port=int(body.ssh_port),
            ssh_user=body.ssh_user.strip(),
            ssh_auth_type=auth,
            ssh_private_key_path=_blank(body.ssh_private_key_path),
            ssh_password=_encrypt_secret(body.ssh_password),
            ssh_connect_timeout_s=float(body.ssh_connect_timeout_s),
            export_script=body.export_script.strip(),
            export_workdir=body.export_workdir.strip(),
            log_interval_min=int(body.log_interval_min),
            docker_container=_blank(body.docker_container),
            docker_sudo=bool(body.docker_sudo),
            capabilities=_default_caps(body.capabilities),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        _commit(db, code)
        db.refresh(row)
        data = _admin_dict(row)
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.get("/{env_id}", response_model=DataResponse, summary="USP 环境详情")
async def get_env(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    db = _open()
    try:
        row = db.get(UspEnv, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        data = _admin_dict(row)
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.put("/{env_id}", response_model=DataResponse, summary="更新 USP 环境")
async def update_env(
    env_id: int,
    body: UspEnvUpdate,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    payload = body.model_dump(exclude_unset=True)
    clear_password = bool(payload.pop("clear_password", False))
    if "ssh_auth_type" in payload and payload["ssh_auth_type"] is not None:
        payload["ssh_auth_type"] = _norm_auth(payload["ssh_auth_type"])
    db = _open()
    try:
        row = db.get(UspEnv, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        if "code" in payload:
            code = _blank(payload["code"])
            if code and _code_taken(db, code, except_id=env_id):
                raise HTTPException(status_code=409, detail=f"code 已存在: {code}")
            row.code = code
        for key in ("name", "ssh_host", "ssh_user", "export_script", "export_workdir"):
            if key in payload and isinstance(payload[key], str):
                setattr(row, key, payload[key].strip())
        for key in ("notes", "project_id", "ssh_private_key_path", "docker_container"):
            if key in payload:
                value = payload[key]
                setattr(row, key, _blank(value) if isinstance(value, str) or value is None else value)
        if "capabilities" in payload:
            row.capabilities = _default_caps(payload["capabilities"])
        for key in ("enabled", "ssh_port", "ssh_auth_type", "ssh_connect_timeout_s", "log_interval_min", "docker_sudo"):
            if key in payload and payload[key] is not None:
                setattr(row, key, payload[key])
        if clear_password:
            row.ssh_password = None
        elif "ssh_password" in payload:
            pwd = payload.get("ssh_password")
            if pwd is not None and str(pwd).strip():
                row.ssh_password = _encrypt_secret(str(pwd))
        row.updated_at = _utcnow()
        auth = row.ssh_auth_type or "password"
        if auth == "key" and not (row.ssh_private_key_path or "").strip():
            raise HTTPException(status_code=422, detail="key 认证需要 ssh_private_key_path")
        if auth == "password" and not (row.ssh_password or "").strip():
            raise HTTPException(status_code=422, detail="password 认证需要 ssh_password")
        _commit(db, row.code)
        db.refresh(row)
        data = _admin_dict(row)
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)


@admin_router.delete("/{env_id}", response_model=DataResponse, summary="删除 USP 环境")
async def delete_env(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    _ = current_user
    db = _open()
    try:
        row = db.get(UspEnv, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        db.delete(row)
        db.commit()
    finally:
        db.close()
    return DataResponse(code=0, message="success", data={"id": env_id})


@admin_router.post("/{env_id}/test-ssh", response_model=DataResponse, summary="测试 SSH 连通")
async def test_ssh(
    env_id: int,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """开发者模式：试连 SSH 并执行 echo，不跑 export_logs。"""
    ensure_dispatch_dev_permission()
    _ = current_user
    db = _open()
    try:
        row = db.get(UspEnv, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        cfg = _ssh_dict(row)
    finally:
        db.close()

    try:
        import paramiko
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"后端未安装 paramiko: {e}") from e

    host = cfg["ssh_host"]
    port = int(cfg["ssh_port"] or 22)
    user = cfg["ssh_user"]
    timeout = float(cfg.get("ssh_connect_timeout_s") or 8.0)
    auth = (cfg.get("ssh_auth_type") or "password").strip().lower()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs = {
            "hostname": host,
            "port": port,
            "username": user,
            "timeout": timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if auth == "key":
            key_path = (cfg.get("ssh_private_key_path") or "").strip()
            if not key_path:
                raise HTTPException(status_code=422, detail="未配置私钥路径")
            kwargs["key_filename"] = key_path
        else:
            password = cfg.get("ssh_password") or ""
            if not password:
                raise HTTPException(status_code=422, detail="未配置密码")
            kwargs["password"] = password
        client.connect(**kwargs)
        _stdin, stdout, stderr = client.exec_command("echo ors_usp_ok", timeout=15)
        out = (stdout.read() or b"").decode("utf-8", errors="replace").strip()
        err = (stderr.read() or b"").decode("utf-8", errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        ok = code == 0 and "ors_usp_ok" in out
        return DataResponse(
            code=0,
            message="success",
            data={
                "ok": ok,
                "host": host,
                "port": port,
                "user": user,
                "exit_code": code,
                "stdout": out[:200],
                "stderr": err[:200],
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        return DataResponse(
            code=0,
            message="success",
            data={
                "ok": False,
                "host": host,
                "port": port,
                "user": user,
                "error": f"{type(e).__name__}: {e}",
            },
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


# ── 讨论区选项（仅开发者模式权限，试验期）──

@public_router.get("/options", response_model=DataResponse, summary="讨论区可选 USP 环境")
async def list_options(
    project_id: Optional[str] = None,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """只返回已启用环境的公开字段；与开发者模式同一权限（试验期）。"""
    ensure_dispatch_dev_permission()
    _ = current_user
    db = _open()
    try:
        query = db.query(UspEnv).filter(UspEnv.enabled.is_(True))
        if project_id and project_id.strip():
            pid = project_id.strip()
            query = query.filter((UspEnv.project_id.is_(None)) | (UspEnv.project_id == pid))
        rows = query.order_by(UspEnv.name.asc()).limit(200).all()
        data = []
        for row in rows:
            caps = _caps(row)
            if "ssh_export_logs" not in caps:
                continue
            data.append(_public_dict(row))
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)


# ── AI 内取 SSH 配置（X-API-Key）──

@public_router.get("/{env_id}/ssh-config", response_model=DataResponse, summary="AI 取 SSH 配置")
async def get_ssh_config(
    env_id: int,
    _: str = Depends(verify_sync_api_key),
):
    db = _open()
    try:
        row = db.get(UspEnv, env_id)
        if not row:
            raise HTTPException(status_code=404, detail="环境不存在")
        if not bool(row.enabled):
            raise HTTPException(status_code=409, detail="环境已停用")
        caps = _caps(row)
        if "ssh_export_logs" not in caps:
            raise HTTPException(status_code=409, detail="未开通 ssh_export_logs")
        data = _ssh_dict(row)
    finally:
        db.close()
    return DataResponse(code=0, message="success", data=data)
