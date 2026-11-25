from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Board, Module, ModuleVersion
from app.policy import Principal, can_delete_module, can_mutate_entity
from app.services.metrics import is_metrics_host
from app.services.modules import purge_module, repair_product_latest_pointers
from app.services.topology import board_host_module, can_edit_board_rel, geometry_only, module_view


def _pinned(m: Module) -> bool:
    if m.kind in ("product_hub", "product_release"):
        return True
    return is_metrics_host(m.product, m.id, m.kind)


class BoardSaveError(Exception):
    def __init__(self, code: int, error: str):
        self.code = code
        self.error = error


def save_board(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    rel_path: str,
    incoming: dict,
) -> dict:
    if not can_edit_board_rel(db, principal, product, rel_path):
        raise BoardSaveError(403, "forbidden")
    geom = geometry_only(incoming)
    old = db.scalar(select(Board).where(Board.product == product, Board.rel_path == rel_path))
    old_ids: set[str] = set()
    if old:
        old_ids = {n["id"] for n in (old.doc.get("nodes") or []) if n.get("id")}
    new_ids = {n["id"] for n in geom["nodes"]}
    group_ids = {g["id"] for g in geom["groups"] if g.get("id")}
    all_mods = {
        m.id: m
        for m in db.scalars(select(Module).where(Module.product == product))
    }
    existing = {mid: m for mid, m in all_mods.items() if m.board_rel == rel_path}
    old_gids: set[str] = set()
    if old:
        old_gids = {
            g["id"]
            for g in ((old.doc or {}).get("groups") or [])
            if isinstance(g, dict) and g.get("id")
        }
    old_group_docs = {
        g["id"]: g
        for g in (((old.doc or {}).get("groups") or []) if old else [])
        if isinstance(g, dict) and g.get("id")
    }
    for gid in old_gids - group_ids:
        m = all_mods.get(gid)
        if not m:
            continue
        if _pinned(m):
            prev = old_group_docs.get(gid)
            if prev:
                geom["groups"].append(prev)
                group_ids.add(gid)
            continue
        mv = module_view(m)
        if not can_delete_module(principal, mv):
            raise BoardSaveError(403, "protected" if m.admin_protected else "out_of_scope_delete")
    host = board_host_module(db, product, rel_path, bind=True)
    host_id = host.id if host else None
    old_nodes = {
        n["id"]: n
        for n in ((old.doc or {}).get("nodes") or [])
        if old and isinstance(n, dict) and n.get("id")
    } if old else {}
    for mid in old_ids - new_ids:
        if mid in group_ids:
            m = all_mods.get(mid)
            if m and not _pinned(m):
                m.kind = "group"
            continue
        m = existing.get(mid)
        if not m:
            continue
        if _pinned(m):
            prev = old_nodes.get(mid) or {}
            geom["nodes"].append(
                {
                    "id": mid,
                    "x": prev.get("x", 0),
                    "y": prev.get("y", 0),
                    "w": prev.get("w"),
                    "h": prev.get("h"),
                }
            )
            continue
        mv = module_view(m)
        if not can_delete_module(principal, mv):
            raise BoardSaveError(403, "protected" if m.admin_protected else "out_of_scope_delete")
        purge_module(db, product, mid)
        all_mods.pop(mid, None)
        existing.pop(mid, None)
    for n in incoming.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        mid = n["id"]
        m = all_mods.get(mid)
        protected = bool(m.admin_protected) if m else False
        if m:
            mv = module_view(m)
            if not can_mutate_entity(principal, mv, entity_protected=protected):
                continue
        title = str(n.get("title") or mid)
        sub = str(n.get("sub") or "")
        title_blob = crypto.encrypt(title, aad=f"module.title:{product}:{mid}")
        sub_blob = crypto.encrypt(sub, aad=f"module.sub:{product}:{mid}") if sub else None
        parent = n.get("parent_group") or n.get("parent_id") or host_id
        if m:
            m.title_blob = title_blob
            m.sub_blob = sub_blob
            m.encrypted = bool(n.get("encrypted", m.encrypted))
            m.nested_board = n.get("nested_board") or m.nested_board
            m.board_rel = rel_path
            if parent:
                m.parent_id = parent
        else:
            enc = n.get("encrypted")
            encrypted = bool(host.encrypted) if enc is None and host else bool(enc)
            m = Module(
                id=mid,
                product=product,
                board_rel=rel_path,
                kind=n.get("kind") or "feature",
                encrypted=encrypted,
                admin_protected=protected,
                title_blob=title_blob,
                sub_blob=sub_blob,
                current_version=n.get("version") or "v1.0",
                parent_id=parent,
                nested_board=n.get("nested_board"),
            )
            db.add(m)
            all_mods[mid] = m
            ver = n.get("version") or "v1.0"
            db.add(
                ModuleVersion(
                    product=product,
                    module_id=mid,
                    semver=ver,
                    lifecycle_band="pre",
                    progress=0,
                    expected_hours=n.get("expected_hours"),
                )
            )
    db.flush()
    for g in geom["groups"]:
        gid = g.get("id")
        if not gid:
            continue
        gm = all_mods.get(gid)
        if gm and not _pinned(gm):
            gm.kind = "group"
        for mid in g.get("members") or []:
            mm = all_mods.get(mid)
            if mm:
                mm.parent_id = gid
    if old:
        old.doc = geom
        old.updated_by = principal.username
    else:
        db.add(Board(product=product, rel_path=rel_path, doc=geom, updated_by=principal.username))
    ver_rows = list(db.scalars(select(ModuleVersion).where(ModuleVersion.product == product)))
    repair_product_latest_pointers(db, crypto, product, all_mods, ver_rows)
    return {"ok": True, "board": rel_path}
