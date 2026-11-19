from __future__ import annotations

import re

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import (
    Board,
    Module,
    ModuleVersion,
    ProductRelease,
    ProductTag,
    ReleaseCover,
    ReleaseNote,
)
from app.policy import (
    Principal,
    can_browse_module,
    can_create_version,
    can_edit_product_field,
    can_edit_release_covers,
    can_edit_release_lifecycle,
    can_publish_version,
)
from app.services.hours import changelog_hours, cover_child_map, cover_invested, iso_week_id

TAGS = ("stable", "latest", "dev")
TAG_LABELS = {"stable": "STABLE", "latest": "LATEST", "dev": "DEV"}
ANCHORS = {
    "usp": {"stable": "usp_stable", "latest": "usp_latest", "dev": "usp_dev"},
    "ors": {"stable": "ors_stable", "latest": "ors_latest", "dev": "ors_dev"},
}
SKIP_COVER_KINDS = frozenset({"product_hub", "product_release", "metrics"})
HUB_GROUPS = frozenset({"usp_algo", "ors_algo"})
_VER_KEY_RE = re.compile(r"\d+")
_COVER_BAND_ORDER = ("pre", "dev", "test", "done")


def _pin_maturity(band: str, prog: float) -> tuple[int, float]:
    idx = _COVER_BAND_ORDER.index(band) if band in _COVER_BAND_ORDER else 99
    return (idx, float(prog))


def stored_lifecycle(mv: ModuleVersion | None) -> tuple[str, float]:
    """Leaf band/progress. Missing band is done; missing progress is 50 for dev."""
    band = ((mv.lifecycle_band if mv else None) or "done").strip() or "done"
    if band == "build":
        try:
            p = float(mv.progress or 0) if mv else 0.0
        except (TypeError, ValueError):
            p = 0.0
        band = "test" if p >= 50 else "dev"
    elif band == "released":
        band = "done"
    elif band not in ("pre", "dev", "test", "done", "joint_test"):
        band = "done"
    if band == "done":
        return "done", 100.0
    if mv is not None and mv.progress is not None:
        try:
            prog = float(mv.progress)
        except (TypeError, ValueError):
            prog = 50.0 if band == "dev" else 0.0
    elif band == "dev":
        prog = 50.0
    else:
        prog = 0.0
    return band, max(0.0, min(100.0, prog))

RELEASE_COVER_TEST_LABEL = "研发自测中"
RELEASE_READY_PUBLISH_LABEL = "待联调测试"


def lifecycle_label(band: str | None, progress: float | None = None) -> str:
    b = (band or "").lower()
    if b == "pre":
        return "设计中"
    if b == "dev":
        return "开发中"
    if b == "test":
        return "测试中"
    if b == "joint_test":
        return "联合测试中"
    if b == "done":
        return "已完成"
    return band or ""


def _nested_node_ids(boards: dict[str, dict], rel: str | None) -> list[str]:
    if not rel:
        return []
    doc = boards.get(rel) or {}
    if not isinstance(doc, dict):
        return []
    return [str(n["id"]) for n in (doc.get("nodes") or []) if isinstance(n, dict) and n.get("id")]


def _cover_context(db: Session, product: str) -> tuple[dict, dict, dict]:
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    vers = {
        (r.module_id, r.semver): r
        for r in db.scalars(select(ModuleVersion).where(ModuleVersion.product == product))
    }
    boards = {b.rel_path: (b.doc or {}) for b in db.scalars(select(Board).where(Board.product == product))}
    return mods, vers, boards


def cover_pin_lifecycle(
    mid: str,
    ver: str,
    *,
    mods: dict[str, Module],
    vers: dict[tuple[str, str], ModuleVersion],
    boards: dict[str, dict],
    memo: dict,
    selected: dict[str, str] | None = None,
) -> tuple[str, float]:
    """Nested host uses its own band until it is done; only then take the least mature child.

    Product covers pass selected so only directly pinned children count.
    """
    sel_key = None if selected is None else tuple(sorted(selected.items()))
    key = (mid, ver, sel_key)
    if key in memo:
        return memo[key]
    m = mods.get(mid)
    if m and m.kind in SKIP_COVER_KINDS:
        memo[key] = ("done", 100.0)
        return memo[key]
    stored = stored_lifecycle(vers.get((mid, ver)))
    kids = [cid for cid in _nested_node_ids(boards, m.nested_board if m else None) if cid != mid]
    if selected is not None:
        kids = [cid for cid in kids if cid in selected]
    if kids and stored[0] == "done":
        child = []
        for cid in kids:
            cm = mods.get(cid)
            if selected is not None:
                cver = selected.get(cid) or (cm.current_version if cm else None) or "v1.0"
            else:
                cver = (cm.current_version if cm else None) or "v1.0"
            child.append(
                cover_pin_lifecycle(
                    cid, cver, mods=mods, vers=vers, boards=boards, memo=memo, selected=selected
                )
            )
        least = min(child, key=lambda bp: _pin_maturity(bp[0], bp[1]))
        memo[key] = least
        return memo[key]
    memo[key] = stored
    return memo[key]


def pin_lifecycle_is_derived(
    mid: str,
    ver: str,
    *,
    mods: dict[str, Module],
    boards: dict[str, dict],
    vers: dict[tuple[str, str], ModuleVersion] | None = None,
) -> bool:
    m = mods.get(mid)
    if not m or m.kind in SKIP_COVER_KINDS:
        return False
    kids = [cid for cid in _nested_node_ids(boards, m.nested_board) if cid != mid]
    if not kids:
        return False
    if vers is None:
        return True
    band, _ = stored_lifecycle(vers.get((mid, ver)))
    return band == "done"


def _cover_bottleneck(db: Session, product: str, release_id: str) -> tuple[str | None, float, int]:
    covers = list(db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == release_id)))
    mods, vers, boards = _cover_context(db, product)
    selected = {c.module_id: c.module_version for c in covers if c.module_id}
    memo: dict = {}
    pins: list[tuple[str, float]] = []
    for c in covers:
        m = mods.get(c.module_id)
        if m and m.kind in SKIP_COVER_KINDS:
            continue
        band, prog = cover_pin_lifecycle(
            c.module_id,
            c.module_version,
            mods=mods,
            vers=vers,
            boards=boards,
            memo=memo,
            selected=selected,
        )
        pins.append((band, prog))
    if not pins:
        return None, 0.0, 0
    bottleneck, prog = min(pins, key=lambda bp: _pin_maturity(bp[0], bp[1]))
    return bottleneck, prog, len(pins)


def release_display_lifecycle(db: Session, product: str, release_id: str) -> dict | None:
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return None
    published = rel.published_at is not None
    if published:
        band = (rel.lifecycle_band or "joint_test").lower()
        if band not in ("joint_test", "done"):
            band = "joint_test"
        progress = 100.0 if band == "done" else float(rel.progress or 0)
        return {
            "band": band,
            "progress": progress,
            "label": lifecycle_label(band, progress),
            "derived": False,
            "published": True,
            "can_publish": False,
        }
    band, prog, n = _cover_bottleneck(db, product, release_id)
    if not n or not band:
        return {
            "band": "pre",
            "progress": 0.0,
            "label": lifecycle_label("pre", 0),
            "derived": False,
            "published": False,
            "can_publish": False,
        }
    if band == "done":
        return {
            "band": "test",
            "progress": 100.0,
            "label": RELEASE_READY_PUBLISH_LABEL,
            "derived": True,
            "published": False,
            "can_publish": True,
        }
    label = RELEASE_COVER_TEST_LABEL if band == "test" else lifecycle_label(band, prog)
    return {
        "band": band,
        "progress": round(prog, 2),
        "label": label,
        "derived": True,
        "published": False,
        "can_publish": False,
    }


def resolve_page_release_id(db: Session, product: str, module_id: str) -> str | None:
    tag = tag_for_anchor(module_id)
    if tag:
        bound = tag_bindings(db, product).get(tag)
        return bound["id"] if bound else None
    rel = db.get(ProductRelease, module_id)
    if rel and rel.product == product:
        return rel.id
    return None


def save_release_lifecycle(
    db: Session,
    principal: Principal,
    product: str,
    module_id: str,
    *,
    band: str,
    progress: float | None,
) -> tuple[int, dict]:
    if not can_edit_release_lifecycle(principal, product):
        return 403, {"ok": False, "error": "产品版本联合测试进度仅测试工程师或管理员可改"}
    rid = resolve_page_release_id(db, product, module_id)
    if not rid:
        return 404, {"ok": False, "error": "not_found"}
    rel = db.get(ProductRelease, rid)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if rel.published_at is None:
        return 400, {"ok": False, "error": "发版前状态由涵盖功能汇总，不可手改"}
    display = release_display_lifecycle(db, product, rid) or {}
    if (display.get("band") or "") not in ("joint_test",):
        return 400, {"ok": False, "error": "当前产品版本状态不可编辑"}
    new_band = (band or "joint_test").strip()
    if new_band not in ("joint_test", "done"):
        return 400, {"ok": False, "error": "产品版本仅可推进至已完成"}
    if new_band == "done":
        rel.lifecycle_band = "done"
        rel.progress = 100.0
    else:
        rel.lifecycle_band = "joint_test"
        rel.progress = max(0.0, min(100.0, float(progress or 0)))
    out = release_display_lifecycle(db, product, rid) or {}
    return 200, {"ok": True, "id": module_id, "release_id": rid, "lifecycle": out}


def tag_for_anchor(module_id: str) -> str | None:
    mid = (module_id or "").strip()
    for mapping in ANCHORS.values():
        for tag, aid in mapping.items():
            if aid == mid:
                return tag
    return None


def is_release_anchor(module_id: str) -> bool:
    return tag_for_anchor(module_id) is not None


def tag_bindings(db: Session, product: str) -> dict[str, dict]:
    releases = {
        r.id: r for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product))
    }
    out: dict[str, dict] = {}
    for t in db.scalars(select(ProductTag).where(ProductTag.product == product)):
        rel = releases.get(t.release_id)
        out[t.tag] = {
            "id": t.release_id,
            "semver": rel.semver if rel else t.release_id,
            "published": bool(rel and rel.published_at),
        }
    return out


def _semver_key(semver: str) -> tuple[int, ...]:
    nums = [int(x) for x in _VER_KEY_RE.findall(semver or "")]
    return tuple(nums) if nums else (0,)


def _predecessor(db: Session, product: str, semver: str) -> ProductRelease | None:
    key = _semver_key(semver)
    best: ProductRelease | None = None
    best_k: tuple[int, ...] | None = None
    for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product)):
        k = _semver_key(r.semver)
        if k < key and (best_k is None or k > best_k):
            best, best_k = r, k
    return best


def list_releases(db: Session, product: str) -> dict:
    tags = tag_bindings(db, product)
    items = []
    for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product).order_by(ProductRelease.sort)):
        covers = [
            {"module_id": c.module_id, "version": c.module_version}
            for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == r.id))
        ]
        owned = [k for k, v in tags.items() if v["id"] == r.id]
        items.append(
            {
                "id": r.id,
                "semver": r.semver,
                "tag": owned[0] if owned else None,
                "tags": owned,
                "published": r.published_at is not None,
                "covers": covers,
                "cover_count": len(covers),
            }
        )
    return {"ok": True, "tags": tags, "versions": items}


def set_tags(
    db: Session, principal: Principal, product: str, mapping: dict[str, str | None]
) -> tuple[int, dict]:
    if not can_publish_version(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    ids = {r.id for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product))}
    rows = {
        t.tag: t for t in db.scalars(select(ProductTag).where(ProductTag.product == product))
    }
    for raw_tag, raw_rid in mapping.items():
        tag = (raw_tag or "").strip().lower()
        if tag not in TAGS:
            return 400, {"ok": False, "error": "bad_tag"}
        rid = (raw_rid or "").strip() or None
        if rid and rid not in ids:
            return 404, {"ok": False, "error": "not_found"}
        row = rows.get(tag)
        if not rid:
            if row:
                db.delete(row)
                rows.pop(tag, None)
            continue
        if row:
            row.release_id = rid
        else:
            row = ProductTag(product=product, tag=tag, release_id=rid)
            db.add(row)
            rows[tag] = row
    db.flush()
    return 200, {"ok": True, **list_releases(db, product)}


def set_covers(
    db: Session,
    principal: Principal,
    product: str,
    release_id: str,
    covers: list[dict],
) -> tuple[int, dict]:
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    tags = tag_bindings(db, product)
    is_dev = (tags.get("dev") or {}).get("id") == release_id
    if not can_edit_release_covers(principal, product, is_dev):
        return 403, {"ok": False, "error": "forbidden"}
    db.execute(sql_delete(ReleaseCover).where(ReleaseCover.release_id == release_id))
    for c in covers:
        mid = (c.get("module_id") or "").strip()
        ver = (c.get("version") or "v1.0").strip()
        if mid:
            db.add(ReleaseCover(release_id=release_id, module_id=mid, module_version=ver))
    return 200, {"ok": True}


def publish(db: Session, principal: Principal, product: str, release_id: str) -> tuple[int, dict]:
    if not can_publish_version(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if rel.published_at is not None:
        return 400, {"ok": False, "error": "该版本已发版"}
    band, _, n = _cover_bottleneck(db, product, release_id)
    if not n:
        return 400, {"ok": False, "error": "尚未关联涵盖功能"}
    if band != "done":
        return 400, {"ok": False, "error": "涵盖功能尚未全部完成"}
    from datetime import datetime, timezone

    rel.published_at = datetime.now(timezone.utc)
    rel.lifecycle_band = "joint_test"
    rel.progress = 0.0
    return 200, {"ok": True, "id": release_id}


def delete_release(db: Session, principal: Principal, product: str, release_id: str) -> tuple[int, dict]:
    if not can_create_version(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    if rel.published_at is not None:
        return 400, {"ok": False, "error": "已发布不可删除"}
    semver = rel.semver
    db.execute(sql_delete(ProductTag).where(ProductTag.release_id == release_id))
    db.execute(sql_delete(ReleaseCover).where(ReleaseCover.release_id == release_id))
    db.execute(sql_delete(ReleaseNote).where(ReleaseNote.release_id == release_id))
    db.delete(rel)
    return 200, {"ok": True, "id": release_id, "semver": semver}


def create_release(
    db: Session, principal: Principal, product: str, semver: str, copy_from: str | None = None
) -> tuple[int, dict]:
    if not can_create_version(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    semver = (semver or "").strip()
    if not semver:
        return 400, {"ok": False, "error": "bad_semver"}
    rid = f"{product}_{semver.replace('.', '')}"
    if not rid.startswith(f"{product}_v") and semver.lower().startswith("v"):
        rid = f"{product}_{semver.replace('.', '')}"
    if db.get(ProductRelease, rid):
        return 409, {"ok": False, "error": "exists"}
    max_sort = max(
        (int(r.sort or 0) for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product))),
        default=0,
    )
    db.add(ProductRelease(id=rid, product=product, semver=semver, sort=max_sort + 10))
    if copy_from:
        for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == copy_from)):
            db.add(
                ReleaseCover(release_id=rid, module_id=c.module_id, module_version=c.module_version)
            )
    db.flush()
    return 201, {"ok": True, "id": rid}


def changelog_pairs(db: Session, product: str, release_id: str) -> list[tuple[str, str]]:
    """Cover pins that are new or upgraded vs the previous semver (no ACL)."""
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return []
    curr = {
        c.module_id: c.module_version
        for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == release_id))
    }
    prev: dict[str, str] = {}
    prev_rel = _predecessor(db, product, rel.semver)
    if prev_rel:
        prev = {
            c.module_id: c.module_version
            for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == prev_rel.id))
        }
    return [
        (mid, ver)
        for mid, ver in curr.items()
        if mid not in prev or prev[mid] != ver
    ]


def release_hours(
    db: Session,
    product: str,
    release_id: str,
    *,
    page_id: str | None = None,
) -> dict:
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return {"ok": False, "error": "not_found"}
    pairs = changelog_pairs(db, product, release_id)
    agg = changelog_hours(db, product, pairs)
    agg.pop("week_map", None)
    if page_id is None:
        tags = tag_bindings(db, product)
        is_dev_page = (tags.get("dev") or {}).get("id") == release_id
    else:
        is_dev_page = tag_for_anchor(page_id) == "dev"
    display = release_display_lifecycle(db, product, release_id) or {}
    hours_view = None
    if not is_dev_page and display.get("band") == "done":
        invested = float(agg.get("invested_total") or 0)
        hours_view = "hidden" if invested <= 0 else "total_only"
    out = {
        "ok": True,
        "release_id": release_id,
        **agg,
        "can_register": False,
        "settled": False,
    }
    if hours_view:
        out["hours_view"] = hours_view
    return out


def decrypt_product_fields(crypto: ContentCrypto, rel: ProductRelease) -> dict:
    def _t(blob: bytes | None, field: str) -> str:
        if not blob:
            return ""
        return crypto.decrypt_text(blob, aad=f"release.{field}:{rel.id}")

    return {
        "added": _t(rel.added_blob, "added"),
        "fixed": _t(rel.fixed_blob, "fixed"),
        "remark": _t(rel.remark_blob, "remark"),
    }


def _title_of(crypto: ContentCrypto, product: str, m: Module | None, mid: str) -> str:
    if m and m.title_blob:
        return crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
    return mid


def cover_tree(db: Session, crypto: ContentCrypto, principal: Principal, product: str) -> list[dict]:
    """Forest from root-board groups + nested_board children (old coverable_tree)."""
    from app.services.topology import default_board_rel, module_view

    mods: dict[str, Module] = {}
    for m in db.scalars(select(Module).where(Module.product == product)):
        if m.kind in SKIP_COVER_KINDS:
            continue
        if not can_browse_module(principal, module_view(m)):
            continue
        mods[m.id] = m
    vers: dict[str, list[str]] = {}
    for row in db.scalars(select(ModuleVersion).where(ModuleVersion.product == product)):
        if row.module_id in mods:
            vers.setdefault(row.module_id, []).append(row.semver)
    boards = {
        b.rel_path: b.doc or {}
        for b in db.scalars(select(Board).where(Board.product == product))
    }

    def entry(mid: str, *, title: str | None = None, kind: str | None = None) -> dict | None:
        m = mods.get(mid)
        if m is None and kind != "group":
            return None
        if m is None:
            return {
                "id": mid,
                "title": title or mid,
                "kind": "group",
                "versions": ["v1.0"],
                "coverable": False,
                "children": [],
            }
        vs = vers.get(mid) or [m.current_version or "v1.0"]
        return {
            "id": mid,
            "title": _title_of(crypto, product, m, mid),
            "kind": "group" if m.kind == "group" or kind == "group" else "feature",
            "versions": vs,
            "coverable": True,
            "children": [],
        }

    seen: set[str] = set()

    def nested_kids(host_id: str) -> list[dict]:
        host = mods.get(host_id)
        if not host or not host.nested_board:
            return []
        doc = boards.get(host.nested_board) or {}
        out: list[dict] = []
        for n in doc.get("nodes") or []:
            if not isinstance(n, dict):
                continue
            cid = n.get("id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            node = entry(cid)
            if not node:
                continue
            node["children"] = nested_kids(cid)
            out.append(node)
        return out

    root_doc = boards.get(default_board_rel(product)) or {}
    groups = [
        g
        for g in (root_doc.get("groups") or [])
        if isinstance(g, dict) and g.get("id") and g["id"] not in HUB_GROUPS
    ]
    groups.sort(
        key=lambda g: (0 if g.get("surface") == "line" else 1, g.get("title") or g.get("id") or "")
    )
    member_ids: set[str] = set()
    for g in groups:
        for mid in g.get("members") or []:
            if isinstance(mid, str):
                member_ids.add(mid)

    forest: list[dict] = []
    for g in groups:
        gid = str(g["id"])
        seen.add(gid)
        root = entry(gid, title=g.get("title"), kind="group")
        if not root:
            continue
        kids: list[dict] = []
        for mid in g.get("members") or []:
            if not isinstance(mid, str) or mid in seen:
                continue
            seen.add(mid)
            node = entry(mid)
            if not node:
                continue
            node["children"] = nested_kids(mid)
            kids.append(node)
        root["children"] = kids
        forest.append(root)

    orphans: list[dict] = []
    for n in root_doc.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        mid = n.get("id")
        if not mid or mid in seen or mid in member_ids:
            continue
        seen.add(mid)
        node = entry(mid)
        if not node:
            continue
        node["children"] = nested_kids(mid)
        orphans.append(node)
    orphans.sort(key=lambda x: x["title"])
    forest.extend(orphans)
    return forest


def _cover_items(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    release_id: str,
) -> list[dict]:
    from app.services.topology import module_view

    mods, vers, boards = _cover_context(db, product)
    covers = list(db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == release_id)))
    week = iso_week_id()
    child_map = cover_child_map(db, product)
    memo: dict = {}
    pin_memo: dict = {}
    selected = {c.module_id: c.module_version for c in covers if c.module_id}
    items = []
    for c in covers:
        m = mods.get(c.module_id)
        if m and not can_browse_module(principal, module_view(m)):
            continue
        band, progress = cover_pin_lifecycle(
            c.module_id,
            c.module_version,
            mods=mods,
            vers=vers,
            boards=boards,
            memo=pin_memo,
            selected=selected,
        )
        items.append(
            {
                "module_id": c.module_id,
                "title": _title_of(crypto, product, m, c.module_id),
                "version": c.module_version,
                "band": band,
                "progress": progress,
                "week_invested": cover_invested(
                    db,
                    product,
                    c.module_id,
                    c.module_version,
                    child_map,
                    week=week,
                    memo=memo,
                    pins=selected,
                ),
            }
        )
    items.sort(key=lambda x: x["title"])
    return items


def _changelog_label(row: dict) -> str:
    if row.get("kind") == "upgrade" and row.get("prev_version"):
        return f"升级 · {row['prev_version']} → {row['version']}"
    return f"新增 · {row.get('version') or ''}"


def changelog_rows(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    release_id: str,
) -> list[dict]:
    rel = db.get(ProductRelease, release_id)
    if not rel:
        return []
    curr = {
        c.module_id: c.module_version
        for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == release_id))
    }
    prev_rel = _predecessor(db, product, rel.semver)
    prev = {}
    if prev_rel:
        prev = {
            c.module_id: c.module_version
            for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == prev_rel.id))
        }
    notes: dict[tuple[str, str], str] = {}
    for n in db.scalars(select(ReleaseNote).where(ReleaseNote.release_id == release_id)):
        text = ""
        if n.explanation_blob:
            text = crypto.decrypt_text(n.explanation_blob, aad=f"release.note:{release_id}:{n.module_id}")
        notes[(n.module_id, n.relative or "")] = text
    from app.services.topology import module_view

    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    rows = []
    for mid in sorted(curr.keys()):
        ver = curr[mid]
        if mid not in prev:
            kind, prev_ver = "new", ""
        elif prev[mid] != ver:
            kind, prev_ver = "upgrade", prev[mid]
        else:
            continue
        m = mods.get(mid)
        if m and not can_browse_module(principal, module_view(m)):
            continue
        row = {
            "module_id": mid,
            "title": _title_of(crypto, product, m, mid),
            "version": ver,
            "kind": kind,
            "prev_version": prev_ver,
            "note": notes.get((mid, ver)) or notes.get((mid, "")) or "",
        }
        row["label"] = _changelog_label(row)
        rows.append(row)
    return rows


def save_changelog_notes(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    release_id: str,
    notes: list[dict],
) -> tuple[int, dict]:
    if not can_edit_product_field(principal, "新增内容"):
        return 403, {"ok": False, "error": "forbidden"}
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    db.execute(sql_delete(ReleaseNote).where(ReleaseNote.release_id == release_id))
    for n in notes:
        mid = (n.get("module_id") or "").strip()
        if not mid:
            continue
        ver = (n.get("version") or "").strip()
        text = str(n.get("note") or "")
        blob = crypto.encrypt(text, aad=f"release.note:{release_id}:{mid}") if text else None
        db.add(ReleaseNote(release_id=release_id, module_id=mid, relative=ver, explanation_blob=blob))
    db.flush()
    return 200, {
        "ok": True,
        "changelog": changelog_rows(db, crypto, principal, product, release_id),
    }


def get_release(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    release_id: str,
    principal: Principal | None = None,
) -> tuple[int, dict]:
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    covers = [
        {"module_id": c.module_id, "version": c.module_version}
        for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id == rel.id))
    ]
    notes = decrypt_product_fields(crypto, rel)
    tags = tag_bindings(db, product)
    owned = [k for k, v in tags.items() if v["id"] == rel.id]
    is_dev = (tags.get("dev") or {}).get("id") == rel.id
    body = {
        "ok": True,
        "id": rel.id,
        "semver": rel.semver,
        "published": rel.published_at is not None,
        "tag": owned[0] if owned else None,
        "tags": owned,
        "is_dev": is_dev,
        "covers": covers,
        "lifecycle": release_display_lifecycle(db, product, rel.id),
        **notes,
    }
    if principal is not None:
        display = body.get("lifecycle") or {}
        body["can_edit_covers"] = can_edit_release_covers(principal, product, is_dev)
        body["can_publish"] = (
            can_publish_version(principal, product)
            and rel.published_at is None
            and bool(display.get("can_publish"))
        )
        body["can_edit_lifecycle"] = (
            can_edit_release_lifecycle(principal, product)
            and rel.published_at is not None
            and display.get("band") == "joint_test"
        )
        body["can_edit_notes"] = can_edit_product_field(principal, "新增内容")
        body["changelog"] = changelog_rows(db, crypto, principal, product, release_id)
        body["cover_items"] = _cover_items(db, crypto, principal, product, release_id)
        body["cover_tree"] = cover_tree(db, crypto, principal, product)
    return 200, body


def save_notes(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    release_id: str,
    fields: dict[str, str],
) -> tuple[int, dict]:
    rel = db.get(ProductRelease, release_id)
    if not rel or rel.product != product:
        return 404, {"ok": False, "error": "not_found"}
    _ = principal
    for key, text in fields.items():
        blob = crypto.encrypt(text, aad=f"release.{key}:{rel.id}") if text else None
        if key == "added":
            rel.added_blob = blob
        elif key == "fixed":
            rel.fixed_blob = blob
        elif key == "remark":
            rel.remark_blob = blob
    return 200, {"ok": True, "id": rel.id}


def _md_html(text: str, product: str) -> str:
    from app.services.markdown_html import md_to_html

    return md_to_html(text, product=product)


def product_page(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    module_id: str,
    module: Module | None,
) -> tuple[int, dict]:
    from app.services.topology import default_board_rel

    tag = tag_for_anchor(module_id)
    release_id = None
    unassigned = False
    if tag:
        bound = tag_bindings(db, product).get(tag)
        if bound:
            release_id = bound["id"]
        else:
            unassigned = True
    else:
        rel = db.get(ProductRelease, module_id)
        if rel and rel.product == product:
            release_id = rel.id
            tag = next((k for k, v in tag_bindings(db, product).items() if v["id"] == rel.id), None)
        elif module and module.kind == "product_release":
            unassigned = True
        else:
            return 404, {"ok": False, "error": "not_found"}
    title = TAG_LABELS.get(tag or "", "") or (module_id if not module else _title_of(crypto, product, module, module_id))
    if module and not tag:
        title = _title_of(crypto, product, module, module_id)
    base = {
        "ok": True,
        "id": module_id,
        "product": product,
        "kind": "product_release",
        "title": title,
        "tag": tag,
        "tag_unassigned": unassigned,
        "release_id": release_id,
        "encrypted": False,
        "can_edit": False,
        "can_edit_lifecycle": False,
        "can_edit_covers": False,
        "can_publish": False,
        "can_edit_notes": False,
        "board_rel": (module.board_rel if module and module.board_rel else "") or default_board_rel(product),
        "nested_board": None,
        "lifecycle": None,
        "markdown": "",
        "html": "",
        "content_hash": "",
        "edit_token": "",
        "added": "",
        "fixed": "",
        "remark": "",
        "changelog": [],
        "covers": [],
        "cover_items": [],
        "cover_tree": cover_tree(db, crypto, principal, product),
        "semver": "",
        "version": "",
        "published": False,
        "is_dev": False,
    }
    if unassigned or not release_id:
        return 200, base
    code, rel = get_release(db, crypto, product, release_id, principal)
    if code != 200:
        return code, rel
    parts = ["## 新增内容", "", rel.get("added") or "", "", "## 修复内容", "", rel.get("fixed") or "", "", "## 备注", "", rel.get("remark") or ""]
    markdown = "\n".join(parts).strip() + "\n"
    base.update(
        {
            "release_id": rel["id"],
            "semver": rel["semver"],
            "version": rel["semver"],
            "title": TAG_LABELS.get((rel.get("tag") or tag) or "", "") or rel["semver"],
            "published": rel["published"],
            "tag": rel.get("tag") or tag,
            "is_dev": rel.get("is_dev") or False,
            "added": rel.get("added") or "",
            "fixed": rel.get("fixed") or "",
            "remark": rel.get("remark") or "",
            "changelog": rel.get("changelog") or [],
            "covers": rel.get("covers") or [],
            "cover_items": rel.get("cover_items") or [],
            "cover_tree": rel.get("cover_tree") or base["cover_tree"],
            "can_edit_covers": rel.get("can_edit_covers") or False,
            "can_publish": rel.get("can_publish") or False,
            "can_edit_notes": rel.get("can_edit_notes") or False,
            "can_edit_lifecycle": rel.get("can_edit_lifecycle") or False,
            "can_edit": rel.get("can_edit_notes") or False,
            "lifecycle": rel.get("lifecycle"),
            "markdown": markdown,
            "html": _md_html(markdown, product),
        }
    )
    return 200, base
