from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.infra.models import AgentToken, Grant, Session as DbSession, User, UserProduct
from app.policy.engine import Principal

PBKDF2_ROUNDS = 120_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return salt.hex() + "$" + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_part, dk_part = stored.split("$", 1)
    except ValueError:
        return False
    dk_target: bytes
    salt: bytes
    try:
        if len(salt_part) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in salt_part):
            salt = bytes.fromhex(salt_part)
            dk_target = bytes.fromhex(dk_part)
        else:
            import base64

            pad = "=" * (-len(salt_part) % 4)
            salt = base64.urlsafe_b64decode(salt_part + pad)
            pad2 = "=" * (-len(dk_part) % 4)
            dk_target = base64.urlsafe_b64decode(dk_part + pad2)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return hmac.compare_digest(dk, dk_target)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load_principal(db: Session, username: str, *, via: str = "session") -> Principal | None:
    user = db.get(User, username)
    if not user:
        return None
    products = frozenset(
        r.product for r in db.scalars(select(UserProduct).where(UserProduct.username == username))
    )
    if user.role == "admin":
        products = frozenset({"usp", "ors"}) | products
    raw = [
        (g.product, g.kind, g.resource_id)
        for g in db.scalars(select(Grant).where(Grant.username == username))
        if (g.perm or "write").strip().lower() == "write"
    ]
    seeds: dict[str, set[str]] = {}
    domain_seeds: dict[str, set[str]] = {}
    for product, kind, rid in raw:
        if kind == "module" and rid:
            seeds.setdefault(product, set()).add(rid)
        elif kind == "domain" and rid:
            domain_seeds.setdefault(product, set()).add(rid)
    expanded = set(raw)
    if seeds or domain_seeds:
        from app.services.hours import expand_domain_grant_ids, expand_module_grant_ids

        for product, ids in seeds.items():
            for mid in expand_module_grant_ids(db, product, ids):
                expanded.add((product, "module", mid))
        for product, domains in domain_seeds.items():
            for did in domains:
                for mid in expand_domain_grant_ids(db, product, did):
                    expanded.add((product, "module", mid))
    grants = frozenset(expanded)
    from app.services.names import label

    return Principal(
        username=user.username,
        role=user.role,
        products=products,
        grants=grants,
        name=label(user.username, user.display_name),
        via=via,
    )


def create_session(db: Session, username: str, hours: int) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(
        DbSession(
            token_hash=hash_token(raw),
            username=username,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
        )
    )
    return raw


def session_principal(db: Session, raw_token: str | None) -> Principal | None:
    if not raw_token:
        return None
    row = db.get(DbSession, hash_token(raw_token))
    if not row:
        return None
    now = datetime.now(timezone.utc)
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        db.delete(row)
        return None
    return load_principal(db, row.username)


def destroy_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    row = db.get(DbSession, hash_token(raw_token))
    if row:
        db.delete(row)


def issue_agent_token(db: Session, username: str) -> str:
    db.execute(delete(AgentToken).where(AgentToken.username == username))
    raw = secrets.token_urlsafe(32)
    db.add(AgentToken(token_hash=hash_token(raw), username=username))
    return raw


def agent_principal(db: Session, bearer: str | None) -> Principal | None:
    if not bearer:
        return None
    token = bearer.removeprefix("Bearer ").strip()
    if not token:
        return None
    row = db.get(AgentToken, hash_token(token))
    if not row:
        return None
    return load_principal(db, row.username, via="agent")
