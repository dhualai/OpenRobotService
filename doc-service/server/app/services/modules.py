from __future__ import annotations

import re

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import (
    Document,
    DocumentRevision,
    HourAllocation,
    HourExpectation,
    HoursWeekClose,
    Module,
    ModuleVersion,
    ProductRelease,
    ReleaseCover,
    ReleaseNote,
    RequirementFeatureLink,
)
from app.policy import Principal, can_delete_module_version, can_edit_module_content, can_protect
from app.services.topology import module_view


def _release_ids(db: Session, product: str):
    return select(ProductRelease.id).where(ProductRelease.product == product)


def _module_versions(db: Session, product: str, module_id: str) -> list[ModuleVersion]:
    return list(
        db.scalars(
            select(ModuleVersion).where(ModuleVersion.product == product, ModuleVersion.module_id == module_id)
        )
    )


def _oldest_version(rows: list[ModuleVersion]) -> ModuleVersion | None:
    if not rows:
        return None
    from app.services.releases import _semver_key

    return sorted(rows, key=lambda r: _semver_key(r.semver))[0]


_SEMVER_OK = re.compile(r"^v?\d+(\.\d+){0,3}$", re.I)


def normalize_feature_semver(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s or not _SEMVER_OK.match(s):
        return None
    return s if s[0] in "vV" else f"v{s}"


def _copy_document(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    module_id: str,
    src_ver: str,
    dst_ver: str,
) -> None:
    if not src_ver or src_ver == dst_ver:
        return
    dst = db.scalar(
        select(Document).where(
            Document.product == product, Document.module_id == module_id, Document.version == dst_ver
        )
    )
    if dst:
        return
    src = db.scalar(
        select(Document).where(
            Document.product == product, Document.module_id == module_id, Document.version == src_ver
        )
    )
    if not src:
        return
    body = crypto.decrypt_text(src.body_blob, aad=f"document:{product}:{module_id}:{src_ver}")
    db.add(
        Document(
            product=product,
            module_id=module_id,
            version=dst_ver,
            body_blob=crypto.encrypt(body, aad=f"document:{product}:{module_id}:{dst_ver}"),
            content_hash=src.content_hash,
            updated_by=src.updated_by,
        )
    )
    db.flush()


def _recorded_semvers(
    db: Session,
    product: str,
    module_id: str,
    ver_rows: list[ModuleVersion] | None = None,
) -> set[str]:
    found: set[str] = set()
    rows = ver_rows if ver_rows is not None else _module_versions(db, product, module_id)
    for row in rows:
        if row.semver:
            found.add(row.semver)
    for ver in db.scalars(
        select(Document.version).where(Document.product == product, Document.module_id == module_id)
    ):
        if ver:
            found.add(str(ver))
    return found


def _has_document(db: Session, product: str, module_id: str, version: str) -> bool:
    return (
        db.scalar(
            select(Document.id).where(
                Document.product == product, Document.module_id == module_id, Document.version == version
            )
        )
        is not None
    )


def _fill_document(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    module_id: str,
    latest: str,
    candidates: list[str],
) -> None:
    """Copy at most one source document onto latest. Flush after each add so the next SELECT sees it."""
    seen: set[str] = set()
    for src in candidates:
        if not src or src == latest or src in seen:
            continue
        seen.add(src)
        if _has_document(db, product, module_id, latest):
            return
        _copy_document(db, crypto, product, module_id, src, latest)


def ensure_latest_pointer(
    db: Session, crypto: ContentCrypto, product: str, m: Module
) -> str:
    """Highest *recorded* semver is latest. A dangling current_version is not a version."""
    from app.services.releases import _semver_key

    found = _recorded_semvers(db, product, m.id)
    if not found:
        found.add(m.current_version or "v1.0")
    latest = sorted(found, key=_semver_key)[-1]
    prev = m.current_version or "v1.0"
    older = sorted((v for v in found if v != latest), key=_semver_key, reverse=True)
    _fill_document(db, crypto, product, m.id, latest, [prev, *older])
    if prev != latest:
        m.current_version = latest
        db.flush()
    return latest


def repair_product_latest_pointers(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    mods: dict[str, Module],
    ver_rows: list[ModuleVersion],
) -> None:
    """Max recorded semver wins. Dangling current_version is snapped back, not materialized."""
    from app.services.releases import _semver_key

    by_mod: dict[str, set[str]] = {}
    for r in ver_rows:
        if r.semver:
            by_mod.setdefault(r.module_id, set()).add(r.semver)
    docs_by_mod: dict[str, set[str]] = {}
    for mid, ver in db.execute(
        select(Document.module_id, Document.version).where(Document.product == product)
    ):
        if ver:
            docs_by_mod.setdefault(str(mid), set()).add(str(ver))
    changed = False
    for m in mods.values():
        if m.kind in ("product_hub", "product_release"):
            continue
        found = set(by_mod.get(m.id) or ()) | set(docs_by_mod.get(m.id) or ())
        prev = m.current_version or "v1.0"
        if not found:
            found.add(prev)
        latest = sorted(found, key=_semver_key)[-1]
        older = sorted((v for v in found if v != latest), key=_semver_key, reverse=True)
        missing = latest not in (docs_by_mod.get(m.id) or ())
        if latest == prev and not missing:
            continue
        _fill_document(db, crypto, product, m.id, latest, [prev, *older])
        if prev != latest:
            m.current_version = latest
        changed = True
    if changed:
        db.flush()


def create_module_version(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    semver: str,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if m.kind in ("product_hub", "product_release"):
        return 403, {"ok": False, "error": "forbidden"}
    if not can_edit_module_content(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    ver = normalize_feature_semver(semver)
    if not ver:
        return 400, {"ok": False, "error": "bad_semver"}
    from app.services.releases import _semver_key

    current = ensure_latest_pointer(db, crypto, product, m)
    if _semver_key(ver) <= _semver_key(current):
        return 400, {"ok": False, "error": "not_newer"}
    from app.services.releases import _cover_context, cover_pin_lifecycle

    mods, vers_map, boards = _cover_context(db, product)
    band, _ = cover_pin_lifecycle(
        m.id, current, mods=mods, vers=vers_map, boards=boards, memo={}
    )
    if band != "done":
        return 400, {"ok": False, "error": "not_done"}
    exists = db.scalar(
        select(ModuleVersion.id).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == module_id,
            ModuleVersion.semver == ver,
        )
    )
    if exists:
        return 400, {"ok": False, "error": "exists"}
    db.add(
        ModuleVersion(
            product=product,
            module_id=module_id,
            semver=ver,
            lifecycle_band="pre",
            progress=0,
        )
    )
    _copy_document(db, crypto, product, module_id, current, ver)
    m.current_version = ver
    db.flush()
    return 200, {"ok": True, "id": module_id, "version": ver, "latest_version": ver}


def _ensure_oldest_version_protected(db: Session, product: str, module_id: str) -> str | None:
    rows = _module_versions(db, product, module_id)
    if not rows:
        return None
    if any(r.admin_protected for r in rows):
        return None
    old = _oldest_version(rows)
    if not old:
        return None
    old.admin_protected = True
    return old.semver


def purge_version_rows(db: Session, product: str, module_id: str, semver: str) -> None:
    db.execute(
        sql_delete(DocumentRevision).where(
            DocumentRevision.product == product,
            DocumentRevision.module_id == module_id,
            DocumentRevision.version == semver,
        )
    )
    db.execute(
        sql_delete(Document).where(
            Document.product == product,
            Document.module_id == module_id,
            Document.version == semver,
        )
    )
    db.execute(
        sql_delete(HourAllocation).where(
            HourAllocation.product == product,
            HourAllocation.feature_id == module_id,
            HourAllocation.version == semver,
        )
    )
    db.execute(
        sql_delete(HourExpectation).where(
            HourExpectation.product == product,
            HourExpectation.feature_id == module_id,
            HourExpectation.version == semver,
        )
    )
    db.execute(
        sql_delete(HoursWeekClose).where(
            HoursWeekClose.product == product,
            HoursWeekClose.feature_id == module_id,
            HoursWeekClose.version == semver,
        )
    )
    db.execute(
        sql_delete(ReleaseCover).where(
            ReleaseCover.module_id == module_id,
            ReleaseCover.module_version == semver,
            ReleaseCover.release_id.in_(_release_ids(db, product)),
        )
    )
    db.execute(
        sql_delete(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == module_id,
            ModuleVersion.semver == semver,
        )
    )


def purge_module(db: Session, product: str, module_id: str) -> None:
    vers = list(
        db.scalars(
            select(ModuleVersion.semver).where(
                ModuleVersion.product == product, ModuleVersion.module_id == module_id
            )
        )
    )
    for semver in vers:
        purge_version_rows(db, product, module_id, semver)
    db.execute(
        sql_delete(RequirementFeatureLink).where(
            RequirementFeatureLink.product == product,
            RequirementFeatureLink.feature_id == module_id,
        )
    )
    db.execute(
        sql_delete(ReleaseNote).where(
            ReleaseNote.module_id == module_id,
            ReleaseNote.release_id.in_(_release_ids(db, product)),
        )
    )
    db.execute(
        sql_delete(ReleaseCover).where(
            ReleaseCover.module_id == module_id,
            ReleaseCover.release_id.in_(_release_ids(db, product)),
        )
    )
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if m:
        db.delete(m)


def set_module_protected(
    db: Session, principal: Principal, product: str, module_id: str, protected: bool
) -> tuple[int, dict]:
    if not can_protect(principal):
        return 403, {"ok": False, "error": "forbidden"}
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if m.kind in ("product_hub", "product_release"):
        return 403, {"ok": False, "error": "forbidden"}
    m.admin_protected = bool(protected)
    if m.admin_protected:
        _ensure_oldest_version_protected(db, product, module_id)
    return 200, {"ok": True, "id": module_id, "admin_protected": m.admin_protected}


def set_version_protected(
    db: Session, principal: Principal, product: str, module_id: str, version: str, protected: bool
) -> tuple[int, dict]:
    if not can_protect(principal):
        return 403, {"ok": False, "error": "forbidden"}
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if m.kind in ("product_hub", "product_release"):
        return 403, {"ok": False, "error": "forbidden"}
    row = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == module_id,
            ModuleVersion.semver == version,
        )
    )
    if not row:
        return 404, {"ok": False, "error": "unknown_version"}
    if not protected and m.admin_protected:
        others = [r for r in _module_versions(db, product, module_id) if r.semver != version and r.admin_protected]
        if not others:
            return 403, {"ok": False, "error": "last_protected_version"}
    row.admin_protected = bool(protected)
    return 200, {"ok": True, "id": module_id, "version": version, "admin_protected": row.admin_protected}


def delete_module_version(
    db: Session, principal: Principal, product: str, module_id: str, version: str
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    mv = module_view(m)
    row = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == module_id,
            ModuleVersion.semver == version,
        )
    )
    if not row:
        return 404, {"ok": False, "error": "unknown_version"}
    if not can_delete_module_version(principal, mv, version_protected=bool(row.admin_protected)):
        return 403, {"ok": False, "error": "protected" if row.admin_protected else "forbidden"}
    others = [
        r.semver
        for r in db.scalars(
            select(ModuleVersion).where(
                ModuleVersion.product == product, ModuleVersion.module_id == module_id
            )
        )
        if r.semver != version
    ]
    if not others:
        return 400, {"ok": False, "error": "last_version"}
    from app.services.releases import _semver_key

    purge_version_rows(db, product, module_id, version)
    if m.current_version == version:
        m.current_version = sorted(others, key=_semver_key)[-1]
    return 200, {"ok": True, "id": module_id, "version": version, "current_version": m.current_version}
