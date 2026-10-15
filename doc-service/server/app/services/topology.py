from __future__ import annotations

from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Board, Document, DocumentRevision, Module, ModuleVersion
from app.policy import (
    ModuleView,
    Principal,
    can_browse_module,
    can_delete_module,
    can_delete_module_version,
    can_edit_board,
    can_edit_lifecycle,
    can_edit_module_content,
    can_protect,
    can_view_product,
)
from app.services.metrics import METRICS_KIND, is_metrics_host, metrics_host_id
from app.services.names import map_db


def _edit_token(content_hash: str | None) -> str:
    from app.services.packs import compose_edit_token

    return compose_edit_token(content_hash)


_HEADING_PREFIXES = ("功能 · ", "功能·")
_SKIP_HEADING_KINDS = frozenset({"product_hub", "product_release", METRICS_KIND})


def first_atx_h1(markdown: str) -> str | None:
    for line in (markdown or "").splitlines():
        s = line.strip()
        if s.startswith("##"):
            continue
        if s.startswith("#"):
            return s[1:].strip() or None
    return None


def heading_matches_module_title(heading: str, module_title: str) -> bool:
    h = (heading or "").strip()
    t = (module_title or "").strip()
    if not t:
        return True
    for p in _HEADING_PREFIXES:
        if h.startswith(p):
            h = h[len(p) :].strip()
            break
    return h == t


def document_title_error(kind: str | None, markdown: str, module_title: str) -> str | None:
    if (kind or "") in _SKIP_HEADING_KINDS:
        return None
    heading = first_atx_h1(markdown)
    if heading is None:
        return None
    if heading_matches_module_title(heading, module_title):
        return None
    return "title_mismatch"


def default_board_rel(product: str) -> str:
    return "boards/ors.json" if product == "ors" else "board.json"


def domain_boards(product: str) -> list[dict]:
    if product == "ors":
        rows = (
            ("algo", "算法", "boards/ors.json"),
            ("backend", "后端", "boards/ors_backend.json"),
            ("frontend", "前端", "boards/ors_frontend.json"),
        )
    else:
        rows = (
            ("algo", "算法", "board.json"),
            ("backend", "后端", "boards/usp_backend.json"),
            ("frontend", "前端", "boards/usp_frontend.json"),
        )
    return [{"id": i, "title": t, "rel_path": p} for i, t, p in rows]


def resolve_board_rel(db: Session, product: str, board_rel: str | None) -> str:
    default = default_board_rel(product)
    rel = (board_rel or "").strip() or default
    found = db.scalar(select(Board.id).where(Board.product == product, Board.rel_path == rel))
    if found:
        return rel
    if rel == "board.json":
        return default
    return rel


def _doc_has_nodes(doc: dict | None) -> bool:
    for n in (doc or {}).get("nodes") or []:
        if isinstance(n, dict) and n.get("id"):
            return True
    return False


def _drill_nested(m: Module, boards: dict[str, dict]) -> str | None:
    explicit = m.nested_board if _doc_has_nodes(boards.get(m.nested_board or "")) else None
    if explicit:
        return explicit
    if m.kind in ("product_hub", "product_release"):
        return None
    conv = f"boards/{m.id}.json"
    return conv if _doc_has_nodes(boards.get(conv)) else None


def domain_of(product: str, board_rel: str | None) -> str | None:
    rel = (board_rel or "").strip()
    for d in domain_boards(product):
        if d["rel_path"] == rel:
            return d["id"]
    return None


def _host_envelope_group(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    board_rel: str,
    nodes_out: list[dict],
    groups_out: list[dict],
) -> None:
    """Nested feature boards get a wrapping dashed group (host module), matching group-crop pages."""
    skip = {d["rel_path"] for d in domain_boards(product)}
    skip.add(default_board_rel(product))
    if board_rel in skip or not nodes_out:
        return
    host = board_host_module(db, product, board_rel)
    if not host:
        return
    if host.kind == METRICS_KIND:
        return
    if any(g.get("id") == host.id for g in groups_out):
        return
    title = host.id
    if host.title_blob:
        title = crypto.decrypt_text(host.title_blob, aad=f"module.title:{product}:{host.id}")
    groups_out.append(
        {
            "id": host.id,
            "x": 0,
            "y": 0,
            "w": 0,
            "h": 0,
            "members": [n["id"] for n in nodes_out],
            "title": title,
            "kind": host.kind or "group",
            "linked": True,
        }
    )


def board_host_module(db: Session, product: str, rel_path: str | None, *, bind: bool = False) -> Module | None:
    rel = (rel_path or "").strip()
    if not rel:
        return None
    host = db.scalar(select(Module).where(Module.product == product, Module.nested_board == rel))
    if host:
        return host
    if not (rel.startswith("boards/") and rel.endswith(".json")):
        return None
    mid = rel[len("boards/") : -len(".json")]
    m = db.scalar(select(Module).where(Module.product == product, Module.id == mid))
    if m and bind and not m.nested_board:
        m.nested_board = rel
    return m


def can_edit_board_rel(db: Session, principal: Principal | None, product: str, rel_path: str | None) -> bool:
    """顶层/域板：ALL 或对应 domain grant；子拓扑板：宿主模块写权限。"""
    if not principal or not can_view_product(principal, product):
        return False
    if principal.role not in ("admin", "engineer", "tester", "supervisor"):
        return False
    rel = (rel_path or default_board_rel(product)).strip() or default_board_rel(product)
    if can_edit_board(principal, product):
        return True
    dom = domain_of(product, rel)
    if dom and (product, "domain", dom) in principal.grants:
        return True
    host = board_host_module(db, product, rel)
    if host:
        return can_edit_module_content(principal, module_view(host))
    return False


def module_view(m: Module, domain: str | None = None) -> ModuleView:
    return ModuleView(
        id=m.id,
        product=m.product,
        encrypted=m.encrypted,
        admin_protected=m.admin_protected,
        parent_id=m.parent_id,
        kind=m.kind,
        domain=domain if domain is not None else domain_of(m.product, m.board_rel),
    )


def geometry_only(doc: dict) -> dict:
    nodes = []
    for n in doc.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue
        nodes.append(
            {
                "id": n["id"],
                "x": n.get("x", 0),
                "y": n.get("y", 0),
                "w": n.get("w"),
                "h": n.get("h"),
            }
        )
    groups = []
    for g in doc.get("groups") or []:
        if not isinstance(g, dict) or not g.get("id"):
            continue
        groups.append(
            {
                "id": g["id"],
                "x": g.get("x", 0),
                "y": g.get("y", 0),
                "w": g.get("w", 0),
                "h": g.get("h", 0),
                "members": list(g.get("members") or []),
                "title": g.get("title") or g["id"],
            }
        )
    edges = []
    for e in doc.get("edges") or []:
        if isinstance(e, dict):
            a, b = e.get("from") or e.get("a"), e.get("to") or e.get("b")
            if not a or not b:
                continue
            item = {"from": a, "to": b}
            for k in ("from_side", "to_side", "from_t", "to_t", "label", "type"):
                if e.get(k) not in (None, ""):
                    item[k] = e[k]
            edges.append(item)
        elif isinstance(e, (list, tuple)) and len(e) >= 2 and e[0] and e[1]:
            edges.append({"from": e[0], "to": e[1]})
    return {
        "node_w": doc.get("node_w", 158),
        "node_h": doc.get("node_h", 46),
        "nodes": nodes,
        "groups": groups,
        "edges": edges,
    }


def graph_for(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    board_rel: str = "board.json",
) -> dict:
    board_rel = resolve_board_rel(db, product, board_rel)
    board = db.scalar(
        select(Board).where(Board.product == product, Board.rel_path == board_rel)
    )
    geom = geometry_only(board.doc if board else {})
    mods = {
        m.id: m
        for m in db.scalars(select(Module).where(Module.product == product))
    }
    boards = {
        b.rel_path: (b.doc or {})
        for b in db.scalars(select(Board).where(Board.product == product))
    }
    ver_rows = list(db.scalars(select(ModuleVersion).where(ModuleVersion.product == product)))
    from app.services.modules import repair_product_latest_pointers

    repair_product_latest_pointers(db, crypto, product, mods, ver_rows)
    vers = {(r.module_id, r.semver): r for r in ver_rows}
    from app.services.releases import (
        TAG_LABELS,
        cover_pin_lifecycle,
        is_release_anchor,
        release_display_lifecycle,
        stored_lifecycle,
        tag_bindings,
        tag_for_anchor,
    )

    bindings = tag_bindings(db, product)
    pin_memo: dict = {}
    nodes_out = []
    hidden = set()
    stored = 0
    for n in geom["nodes"]:
        stored += 1
        m = mods.get(n["id"])
        if not m:
            hidden.add(n["id"])
            continue
        if m.kind == "product_release" and not is_release_anchor(m.id):
            hidden.add(n["id"])
            continue
        mv = module_view(m)
        if not can_browse_module(principal, mv):
            hidden.add(n["id"])
            continue
        title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}") if m.title_blob else m.id
        sub = (
            crypto.decrypt_text(m.sub_blob, aad=f"module.sub:{product}:{m.id}")
            if m.sub_blob
            else ""
        )
        mv_row = vers.get((m.id, m.current_version))
        band, progress = stored_lifecycle(mv_row)
        nested = _drill_nested(m, boards)
        tag = tag_for_anchor(m.id) if m.kind == "product_release" else None
        tag_unassigned = False
        tagged_release_id = None
        if tag:
            title = TAG_LABELS[tag]
            sub = ""
            bound = bindings.get(tag)
            if bound:
                tagged_release_id = bound["id"]
                version = bound["semver"]
                display = release_display_lifecycle(db, product, bound["id"])
                if display:
                    band, progress = display.get("band") or "dev", display.get("progress")
                elif bound.get("published"):
                    band, progress = "done", 100.0
                else:
                    band, progress = "dev", progress
            else:
                version = ""
                tag_unassigned = True
                if mv_row:
                    band, progress = stored_lifecycle(mv_row)
                else:
                    band, progress = "pre", 0.0
        else:
            version = m.current_version
            if m.kind not in ("product_hub", "product_release", METRICS_KIND):
                band, progress = cover_pin_lifecycle(
                    m.id, version or "v1.0", mods=mods, vers=vers, boards=boards, memo=pin_memo
                )
        if m.kind == METRICS_KIND:
            band, progress = "done", 100.0
            version = ""
        nodes_out.append(
            {
                **n,
                "title": title,
                "sub": sub,
                "kind": m.kind,
                "encrypted": m.encrypted,
                "version": version,
                "nested_board": nested,
                "band": band,
                "progress": progress,
                "tag": tag,
                "tag_unassigned": tag_unassigned,
                "tagged_release_id": tagged_release_id,
                "admin_protected": bool(m.admin_protected),
                "can_delete": can_delete_module(principal, mv),
            }
        )
    groups_out = []
    for g in geom["groups"]:
        if g["id"] in hidden:
            continue
        gm = mods.get(g["id"])
        gtitle = g.get("title") or g["id"]
        if gm and gm.title_blob:
            gtitle = crypto.decrypt_text(gm.title_blob, aad=f"module.title:{product}:{gm.id}")
        if g["id"] in ("usp_algo", "ors_algo"):
            gkind = "product_hub"
        elif gm:
            gkind = gm.kind
        else:
            gkind = g.get("kind")
        metrics_id = metrics_host_id(product) if gkind == "product_hub" and metrics_host_id(product) in mods else None
        test_sso = gkind == "product_hub" and product == "usp"
        groups_out.append(
            {**g, "title": gtitle, "kind": gkind, "linked": gm is not None, "metrics_id": metrics_id, "test_sso": test_sso}
        )
    _host_envelope_group(db, crypto, product, board_rel, nodes_out, groups_out)
    for g in groups_out:
        gm = mods.get(g["id"])
        if not gm:
            g["admin_protected"] = False
            g["can_delete"] = g.get("kind") not in ("product_hub", "product_release") and not is_metrics_host(
                product, g["id"], g.get("kind")
            )
            continue
        g["admin_protected"] = bool(gm.admin_protected)
        g["can_delete"] = can_delete_module(principal, module_view(gm))
    visible = {n["id"] for n in nodes_out} | {g["id"] for g in groups_out}
    edges_out = [e for e in geom["edges"] if e["from"] in visible and e["to"] in visible]
    return {
        "product": product,
        "board": board_rel,
        "node_w": geom["node_w"],
        "node_h": geom["node_h"],
        "nodes": nodes_out,
        "groups": groups_out,
        "edges": edges_out,
        "domains": domain_boards(product),
        "elided": max(0, stored - len(nodes_out)),
        "can_edit": can_edit_board_rel(db, principal, product, board_rel),
        "can_protect": can_protect(principal),
    }


def _crop_group_graph(full: dict, group: dict) -> dict | None:
    members = {mid for mid in (group.get("members") or []) if mid}
    nodes = [dict(n) for n in full.get("nodes") or [] if n.get("id") in members]
    if not nodes:
        return None
    min_x = min(float(n.get("x") or 0) for n in nodes)
    min_y = min(float(n.get("y") or 0) for n in nodes)
    pad = 40.0
    for n in nodes:
        n["x"] = float(n.get("x") or 0) - min_x + pad
        n["y"] = float(n.get("y") or 0) - min_y + pad
    visible = {n["id"] for n in nodes} | {group["id"]}
    g = dict(group)
    g["x"] = pad
    g["y"] = pad
    g["w"] = 0
    g["h"] = 0
    g["members"] = [m for m in (g.get("members") or []) if m in {n["id"] for n in nodes}]
    g["linked"] = False
    edges = [dict(e) for e in full.get("edges") or [] if e.get("from") in visible and e.get("to") in visible]
    out = dict(full)
    out["nodes"] = nodes
    out["groups"] = [g]
    out["edges"] = edges
    return out


def _empty_nested(product: str, board_rel: str) -> dict:
    return {
        "ok": True,
        "skip": False,
        "empty": True,
        "source": "empty",
        "product": product,
        "board": board_rel,
        "editor_board": board_rel,
        "node_w": 158,
        "node_h": 46,
        "nodes": [],
        "groups": [],
        "edges": [],
        "domains": domain_boards(product),
        "elided": 0,
        "can_edit": False,
    }


def nested_graph_for(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if not can_browse_module(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    if m.kind in ("product_hub", "product_release"):
        skipped = {**_empty_nested(product, m.board_rel or default_board_rel(product)), "skip": True, "source": "skip"}
        skipped["can_edit"] = False
        return 200, skipped
    host_write = can_edit_module_content(principal, module_view(m))
    parent_rel = m.board_rel or default_board_rel(product)
    parent = graph_for(db, crypto, principal, product, parent_rel)
    group = next((g for g in parent.get("groups") or [] if g.get("id") == module_id), None)
    if group and (group.get("members") or []):
        cropped = _crop_group_graph(parent, group)
        if cropped and cropped.get("nodes"):
            cropped["ok"] = True
            cropped["skip"] = False
            cropped["empty"] = False
            cropped["source"] = "group"
            cropped["editor_board"] = parent_rel
            cropped["editor_group"] = module_id
            cropped["can_edit"] = can_edit_board_rel(db, principal, product, parent_rel)
            return 200, cropped
    boards = {
        b.rel_path: (b.doc or {})
        for b in db.scalars(select(Board).where(Board.product == product))
    }
    nested_rel = _drill_nested(m, boards)
    if not nested_rel:
        empty = _empty_nested(product, f"boards/{m.id}.json")
        empty["can_edit"] = host_write
        return 200, empty
    nested = graph_for(db, crypto, principal, product, nested_rel)
    nested["ok"] = True
    nested["skip"] = False
    nested["empty"] = not nested.get("nodes")
    nested["source"] = "board"
    nested["editor_board"] = nested_rel
    nested["can_edit"] = host_write or bool(nested.get("can_edit"))
    return 200, nested


def _module_semvers(db: Session, product: str, module_id: str, current: str | None) -> list[str]:
    from app.services.releases import _semver_key

    found: set[str] = set()
    for row in db.scalars(
        select(ModuleVersion.semver).where(
            ModuleVersion.product == product, ModuleVersion.module_id == module_id
        )
    ):
        if row:
            found.add(str(row))
    for ver in db.scalars(
        select(Document.version).where(Document.product == product, Document.module_id == module_id)
    ):
        if ver:
            found.add(str(ver))
    if current:
        found.add(current)
    return sorted(found, key=_semver_key)


def get_module_page(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    version: str | None = None,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        from app.infra.models import ProductRelease
        from app.services.releases import product_page

        rel = db.get(ProductRelease, module_id)
        if rel and rel.product == product:
            return product_page(db, crypto, principal, product, module_id, None)
        return 404, {"ok": False, "error": "not_found"}
    mv = module_view(m)
    if not can_browse_module(principal, mv):
        return 403, {"ok": False, "error": "forbidden"}
    if m.kind == "product_release":
        from app.services.releases import product_page

        return product_page(db, crypto, principal, product, module_id, m)
    from app.services.modules import ensure_latest_pointer

    latest = ensure_latest_pointer(db, crypto, product, m)
    versions = _module_semvers(db, product, module_id, latest)
    want = (version or "").strip() or None
    if want:
        matched = next((v for v in versions if v.lower() == want.lower()), None)
        if not matched:
            return 404, {"ok": False, "error": "unknown_version"}
        ver = matched
    else:
        ver = latest
    title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}") if m.title_blob else m.id
    doc = db.scalar(
        select(Document).where(
            Document.product == product,
            Document.module_id == module_id,
            Document.version == ver,
        )
    )
    body = ""
    html = ""
    content_hash = ""
    if doc:
        body = crypto.decrypt_text(doc.body_blob, aad=f"document:{product}:{module_id}:{ver}")
        content_hash = doc.content_hash
        from app.services.markdown_html import md_to_html

        known = {
            "usp": set(db.scalars(select(Module.id).where(Module.product == "usp"))),
            "ors": set(db.scalars(select(Module.id).where(Module.product == "ors"))),
        }
        html = md_to_html(
            body,
            product=product,
            module_id=module_id,
            versions=versions,
            current_version=ver,
            ids_by_product=known,
        )
    from app.services.releases import _cover_context, cover_pin_lifecycle, pin_lifecycle_is_derived

    mods, vers_map, boards = _cover_context(db, product)
    dband, dprog = cover_pin_lifecycle(
        module_id, ver, mods=mods, vers=vers_map, boards=boards, memo={}
    )
    derived = pin_lifecycle_is_derived(module_id, ver, mods=mods, boards=boards, vers=vers_map)
    is_latest = ver == latest
    if not is_latest:
        dband, dprog = "done", 100.0
        derived = False
    writable = can_edit_module_content(principal, mv) and is_latest
    mv_row = vers_map.get((module_id, ver))
    band = {
        "band": dband,
        "progress": dprog,
        "expected_hours": mv_row.expected_hours if mv_row else None,
        "derived": derived,
    }
    revision_count = int(
        db.scalar(
            select(func.count())
            .select_from(DocumentRevision)
            .where(
                DocumentRevision.product == product,
                DocumentRevision.module_id == module_id,
                DocumentRevision.version == ver,
            )
        )
        or 0
    )
    return 200, {
        "ok": True,
        "id": m.id,
        "product": product,
        "title": title,
        "kind": m.kind,
        "version": ver,
        "versions": versions,
        "latest_version": latest,
        "encrypted": m.encrypted,
        "admin_protected": bool(m.admin_protected),
        "version_protected": bool(mv_row.admin_protected) if mv_row else False,
        "revision_count": revision_count,
        "can_edit": writable,
        "can_create_version": writable,
        "can_protect": can_protect(principal),
        "can_delete_version": can_delete_module_version(
            principal, mv, version_protected=bool(mv_row.admin_protected) if mv_row else False
        )
        and len(versions) > 1,
        "can_edit_lifecycle": (
            False
            if m.kind in ("product_hub", METRICS_KIND)
            or not is_latest
            or (principal.role != "admin" and (derived or dband == "done"))
            else can_edit_lifecycle(principal, mv)
        ),
        "lifecycle": band,
        "board_rel": m.board_rel or default_board_rel(product),
        "nested_board": _drill_nested(m, boards),
        "markdown": body,
        "html": html,
        "content_hash": content_hash,
        "edit_token": _edit_token(content_hash),
    }


def save_document(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    markdown: str,
    version: str | None = None,
    *,
    snapshot: bool = False,
    from_pack: bool = False,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if m.kind == "product_release":
        return 403, {"ok": False, "error": "forbidden"}
    mv = module_view(m)
    if not can_edit_module_content(principal, mv):
        return 403, {"ok": False, "error": "forbidden"}
    if getattr(principal, "via", "session") == "agent" and not from_pack:
        from app.services.packs import use_pack_denied

        return use_pack_denied()
    from app.services.modules import ensure_latest_pointer, normalize_feature_semver

    latest = ensure_latest_pointer(db, crypto, product, m)
    if version:
        want = normalize_feature_semver(version) or version.strip()
        if want != latest:
            return 403, {"ok": False, "error": "frozen"}
        ver = latest
    else:
        ver = latest
    title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}") if m.title_blob else m.id
    mismatch = document_title_error(m.kind, markdown, title)
    if mismatch:
        return 400, {"ok": False, "error": mismatch}
    blob = crypto.encrypt(markdown, aad=f"document:{product}:{module_id}:{ver}")
    h = crypto.content_hash(markdown)
    doc = db.scalar(
        select(Document).where(
            Document.product == product, Document.module_id == module_id, Document.version == ver
        )
    )
    if doc:
        if snapshot and doc.content_hash != h:
            db.add(
                DocumentRevision(
                    product=product,
                    module_id=module_id,
                    version=ver,
                    body_blob=doc.body_blob,
                    content_hash=doc.content_hash,
                    author=principal.username,
                )
            )
        doc.body_blob = blob
        doc.content_hash = h
        doc.updated_by = principal.username
    else:
        db.add(
            Document(
                product=product,
                module_id=module_id,
                version=ver,
                body_blob=blob,
                content_hash=h,
                updated_by=principal.username,
            )
        )
    return 200, {"ok": True, "content_hash": h, "edit_token": _edit_token(h)}


BANDS = ("pre", "dev", "test", "done")
BAND_RANK = {b: i for i, b in enumerate(BANDS)}


def _admin_session(principal: Principal) -> bool:
    return principal.role == "admin" and getattr(principal, "via", "session") != "agent"


def lifecycle_may_advance(old_band: str | None, new_band: str | None) -> tuple[bool, str]:
    ob = (old_band or "pre").strip()
    nb = (new_band or ob).strip()
    if nb not in BAND_RANK:
        return False, "invalid_band"
    if BAND_RANK.get(nb, 0) < BAND_RANK.get(ob, 0):
        return False, "cannot_regress"
    return True, ""


def save_lifecycle(
    db: Session,
    principal: Principal,
    product: str,
    module_id: str,
    *,
    band: str,
    progress: float | None,
    version: str | None = None,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if getattr(principal, "via", "session") == "agent":
        return 403, {"ok": False, "error": "page_only"}
    admin = _admin_session(principal)
    if m.kind in ("product_hub", METRICS_KIND):
        return 403, {"ok": False, "error": "forbidden"}
    if m.kind == "product_release":
        from app.services.releases import save_release_lifecycle

        return save_release_lifecycle(
            db, principal, product, module_id, band=band, progress=progress
        )
    mv = module_view(m)
    if not can_edit_lifecycle(principal, mv):
        return 403, {"ok": False, "error": "forbidden"}
    if band not in BANDS:
        return 400, {"ok": False, "error": "invalid_band"}
    from app.infra.crypto import ContentCrypto
    from app.settings import get_settings
    from app.services.modules import create_module_version, ensure_latest_pointer, normalize_feature_semver

    crypto = ContentCrypto(get_settings().topo_content_key)
    latest = ensure_latest_pointer(db, crypto, product, m)
    ver = (normalize_feature_semver(version) if version else None) or latest
    from app.services.releases import _semver_key

    if _semver_key(ver) < _semver_key(latest):
        return 403, {"ok": False, "error": "frozen"}
    if ver != latest:
        code, created = create_module_version(db, crypto, principal, product, module_id, ver)
        if code != 200:
            return code, created
        latest = ver
    from app.services.releases import _cover_context, pin_lifecycle_is_derived

    mods, vers_map, boards = _cover_context(db, product)
    if pin_lifecycle_is_derived(module_id, ver, mods=mods, boards=boards, vers=vers_map) and not admin:
        return 403, {"ok": False, "error": "derived"}
    row = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == module_id,
            ModuleVersion.semver == ver,
        )
    )
    stored = (row.lifecycle_band if row else None) or "pre"
    if stored == "done" and not admin:
        return 403, {"ok": False, "error": "forbidden"}
    ok_adv, err_adv = lifecycle_may_advance(stored, band)
    if not ok_adv and not admin:
        return 400, {"ok": False, "error": err_adv}
    prog = 100.0 if band == "done" else (None if progress is None else max(0.0, min(100.0, float(progress))))
    if row:
        row.lifecycle_band = band
        row.progress = prog
        if band == "done":
            invested = 0.0
            from sqlalchemy import func
            from app.infra.models import HourAllocation

            invested = float(
                db.scalar(
                    select(func.coalesce(func.sum(HourAllocation.hours), 0.0)).where(
                        HourAllocation.product == product,
                        HourAllocation.feature_id == module_id,
                        HourAllocation.version == ver,
                    )
                )
                or 0
            )
            if invested > 0:
                row.expected_hours = invested
    else:
        db.add(
            ModuleVersion(
                product=product,
                module_id=module_id,
                semver=ver,
                lifecycle_band=band,
                progress=prog,
            )
        )
    return 200, {"ok": True, "band": band, "progress": prog, "version": ver}


def list_revisions(db: Session, principal: Principal, product: str, module_id: str) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if not can_browse_module(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    rows = list(
        db.scalars(
            select(DocumentRevision)
            .where(
                DocumentRevision.product == product,
                DocumentRevision.module_id == module_id,
            )
            .order_by(DocumentRevision.id.desc())
            .limit(20)
        )
    )
    labels = map_db(db)
    def _rev_iso(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()

    return 200, {
        "ok": True,
        "can_restore": can_edit_module_content(principal, module_view(m)),
        "items": [
            {
                "id": r.id,
                "author": labels.get(r.author, r.author),
                "created_at": _rev_iso(r.created_at),
                "content_hash": r.content_hash,
                "version": r.version,
            }
            for r in rows
        ],
    }


def restore_revision(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    revision_id: int,
) -> tuple[int, dict]:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return 404, {"ok": False, "error": "not_found"}
    if not can_edit_module_content(principal, module_view(m)):
        return 403, {"ok": False, "error": "forbidden"}
    rev = db.get(DocumentRevision, revision_id)
    if not rev or rev.product != product or rev.module_id != module_id:
        return 404, {"ok": False, "error": "not_found"}
    body = crypto.decrypt_text(rev.body_blob, aad=f"document:{product}:{module_id}:{rev.version}")
    return save_document(db, crypto, principal, product, module_id, body, version=rev.version)

