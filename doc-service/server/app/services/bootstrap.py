from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.models import Grant, User, UserProduct
from app.services.auth import hash_password
from app.services.names import DIRECTORY
from app.settings import Settings

_SUPERVISOR = "donghualai"
_SUPERVISOR_PASSWORD = "donghualai"


def upsert_dev_admin(db: Session, settings: Settings) -> bool:
    pw = (settings.bootstrap_admin_password or "").strip()
    if not pw:
        return False
    row = db.get(User, "admin")
    if row:
        return False
    digest = hash_password(pw)
    db.add(User(username="admin", password_hash=digest, role="admin", display_name="管理员"))
    db.flush()
    for product in ("usp", "ors"):
        if not db.scalar(
            select(UserProduct).where(UserProduct.username == "admin", UserProduct.product == product)
        ):
            db.add(UserProduct(username="admin", product=product))
        if not db.scalar(
            select(Grant).where(
                Grant.username == "admin",
                Grant.product == product,
                Grant.kind == "product_all",
                Grant.resource_id == "",
            )
        ):
            db.add(Grant(username="admin", product=product, kind="product_all", resource_id="", perm="write"))
    return True


def apply_directory(db: Session) -> None:
    for uname, name in DIRECTORY.items():
        row = db.get(User, uname)
        if row and not (row.display_name or "").strip():
            row.display_name = name
    row = db.get(User, _SUPERVISOR)
    if not row:
        db.add(
            User(
                username=_SUPERVISOR,
                password_hash=hash_password(_SUPERVISOR_PASSWORD),
                role="supervisor",
                display_name=DIRECTORY[_SUPERVISOR],
            )
        )
        db.flush()
        row = db.get(User, _SUPERVISOR)
    else:
        row.role = "supervisor"
        row.display_name = DIRECTORY[_SUPERVISOR]
    assert row is not None
    for product in ("usp", "ors"):
        if not db.scalar(
            select(UserProduct).where(UserProduct.username == _SUPERVISOR, UserProduct.product == product)
        ):
            db.add(UserProduct(username=_SUPERVISOR, product=product))
        if not db.scalar(
            select(Grant).where(
                Grant.username == _SUPERVISOR,
                Grant.product == product,
                Grant.kind == "product_all",
                Grant.resource_id == "",
            )
        ):
            db.add(
                Grant(
                    username=_SUPERVISOR,
                    product=product,
                    kind="product_all",
                    resource_id="",
                    perm="write",
                )
            )
