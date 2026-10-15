from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete as sql_delete, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import AuditLog, Module, ProductRelease, ProductTag
from app.policy import Principal, can_manage_users
from app.services.names import map_db

CONTENT_ACTIONS = frozenset(
    {
        "pack.import",
        "board.save",
        "module.document",
        "module.lifecycle",
        "doc.restore",
        "requirement.create",
        "requirement.update",
        "requirement.delete",
        "requirement.review",
        "requirement.verdict",
        "requirement.restore",
        "requirement.skip_link",
        "product.notes",
        "product.covers",
        "product.changelog",
        "product.tags",
        "product.publish",
        "product.delete",
        "module.protect",
        "module.unprotect",
        "version.protect",
        "version.unprotect",
        "version.delete",
        "version.create",
    }
)

ACTION_LABEL = {
    "pack.import": "导入工程包",
    "board.save": "编辑拓扑",
    "module.document": "编辑正文",
    "module.lifecycle": "状态/进度",
    "doc.restore": "回退正文",
    "requirement.create": "新建需求",
    "requirement.update": "编辑需求",
    "requirement.delete": "删除需求",
    "requirement.review": "发起评审",
    "requirement.verdict": "评审",
    "requirement.restore": "回退正文",
    "requirement.skip_link": "无相关功能开发",
    "product.notes": "编辑发布页",
    "product.covers": "涵盖功能",
    "product.changelog": "发版说明",
    "product.tags": "版本标签",
    "product.publish": "发版",
    "product.delete": "删除产品版本",
    "module.protect": "保护模块",
    "module.unprotect": "取消保护模块",
    "version.protect": "保护版本",
    "version.unprotect": "取消保护版本",
    "version.delete": "删除功能版本",
    "version.create": "新建功能版本",
    "user.save": "用户",
}

BAND_LABEL = {
    "pre": "设计中",
    "dev": "开发中",
    "test": "测试中",
    "done": "已完成",
    "joint_test": "联合测试中",
}

REQ_STATUS = {
    "draft": "草稿",
    "review": "待评审",
    "pending_link": "待关联",
    "developing": "开发中",
    "done": "已完成",
}


PRODUCT_LABEL = {"usp": "USP", "ors": "ORS"}
TAG_LABEL = {"stable": "STABLE", "latest": "LATEST", "dev": "DEV"}
KEEP_DAYS = 7


def purge_expired(db: Session, *, days: int = KEEP_DAYS) -> None:
    keep = max(1, int(days or KEEP_DAYS))
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep)
    db.execute(sql_delete(AuditLog).where(AuditLog.at < cutoff))


def product_label(product: str | None) -> str:
    p = (product or "usp").lower()
    return PRODUCT_LABEL.get(p, p.upper())


def _looks_internal(s: str | None) -> bool:
    t = (s or "").strip()
    if not t:
        return False
    low = t.lower()
    if ".json" in low or "boards/" in low:
        return True
    return False


def _ids(doc: dict | None, key: str) -> set[str]:
    out: set[str] = set()
    for n in (doc or {}).get(key) or []:
        if isinstance(n, dict) and n.get("id"):
            out.add(str(n["id"]))
    return out


def _edge_keys(doc: dict | None) -> set[str]:
    out: set[str] = set()
    for e in (doc or {}).get("edges") or []:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            out.add(f"{e['from']}\t{e['to']}")
    return out


def _geom(doc: dict | None) -> dict:
    from app.services.topology import geometry_only

    return geometry_only(doc or {})


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _node_sig(n: dict) -> tuple:
    return (_num(n.get("x")) or 0.0, _num(n.get("y")) or 0.0, _num(n.get("w")), _num(n.get("h")))


def _group_sig(g: dict) -> tuple:
    members = tuple(str(x) for x in (g.get("members") or []))
    return (
        _num(g.get("x")) or 0.0,
        _num(g.get("y")) or 0.0,
        _num(g.get("w")) or 0.0,
        _num(g.get("h")) or 0.0,
        members,
        str(g.get("title") or g.get("id") or ""),
    )


def board_geom_equivalent(old: dict | None, new: dict | None) -> bool:
    if not old:
        return False
    o, n = _geom(old), _geom(new)
    if _num(o.get("node_w")) != _num(n.get("node_w")) or _num(o.get("node_h")) != _num(n.get("node_h")):
        return False
    om = {x["id"]: x for x in o["nodes"]}
    nm = {x["id"]: x for x in n["nodes"]}
    if om.keys() != nm.keys():
        return False
    if any(_node_sig(om[i]) != _node_sig(nm[i]) for i in om):
        return False
    og = {x["id"]: x for x in o["groups"]}
    ng = {x["id"]: x for x in n["groups"]}
    if og.keys() != ng.keys():
        return False
    if any(_group_sig(og[i]) != _group_sig(ng[i]) for i in og):
        return False
    return _edge_keys(o) == _edge_keys(n)


def board_change_summary(old: dict | None, new: dict | None) -> str:
    if not old:
        return ""
    old, new = _geom(old), _geom(new)
    bits: list[str] = []
    add_n = _ids(new, "nodes") - _ids(old, "nodes")
    del_n = _ids(old, "nodes") - _ids(new, "nodes")
    if add_n:
        bits.append(f"新增{len(add_n)}个功能")
    if del_n:
        bits.append(f"删除{len(del_n)}个功能")
    if _ids(new, "groups") != _ids(old, "groups"):
        bits.append("调整分组")
    if _edge_keys(new) != _edge_keys(old):
        bits.append("调整连线")
    return "、".join(bits)


def lifecycle_summary(band: str | None, progress: float | None) -> str:
    parts = [BAND_LABEL.get(band or "", band or "")]
    if progress is not None:
        n = float(progress)
        parts.append(f"{int(n) if n == int(n) else n}%")
    return " · ".join(x for x in parts if x)


def board_display(db: Session, crypto: ContentCrypto, product: str, board_rel: str) -> tuple[str, str]:
    from app.services.topology import default_board_rel, domain_boards

    p = (product or "usp").lower()
    pl = product_label(p)
    rel = (board_rel or "").strip() or default_board_rel(p)
    root = default_board_rel(p)
    for d in domain_boards(p):
        if d["rel_path"] == rel:
            href = f"/{p}/editor" if d["rel_path"] == root else f"/{p}/editor?domain={d['id']}"
            return f"{pl} · {d['title']}", href
    host = db.scalar(select(Module).where(Module.product == p, Module.nested_board == rel))
    if host:
        return module_title(db, crypto, p, host.id), f"/{p}/m/{host.id}"
    if rel.startswith("boards/") and rel.endswith(".json"):
        mid = rel[len("boards/") : -len(".json")]
        if db.scalar(select(Module.id).where(Module.product == p, Module.id == mid)):
            return module_title(db, crypto, p, mid), f"/{p}/m/{mid}"
    return f"{pl} · 拓扑", f"/{p}/editor"


def release_label(db: Session, product: str, release_id: str) -> str:
    p = (product or "usp").lower()
    pl = product_label(p)
    tags = [
        t.tag
        for t in db.scalars(select(ProductTag).where(ProductTag.product == p, ProductTag.release_id == release_id))
    ]
    if tags:
        return f"{pl} · {TAG_LABEL.get(tags[0], tags[0].upper())}"
    rel = db.get(ProductRelease, release_id)
    if rel and rel.semver:
        return f"{pl} {rel.semver}"
    return pl


def board_title(board_rel: str, product: str | None = None) -> str:
    from app.services.topology import default_board_rel, domain_boards

    p = (product or "usp").lower()
    pl = product_label(p)
    rel = (board_rel or "").strip() or default_board_rel(p)
    for d in domain_boards(p):
        if d["rel_path"] == rel:
            return f"{pl} · {d['title']}"
    return f"{pl} · 拓扑"


def module_title(db: Session, crypto: ContentCrypto, product: str, module_id: str) -> str:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == module_id))
    if not m:
        return module_id
    if m.title_blob:
        try:
            return crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        except Exception:
            return m.id
    return m.id


def write(
    db: Session,
    *,
    actor: str,
    action: str,
    product: str | None = None,
    resource: str = "",
    title: str = "",
    summary: str = "",
    extra: dict | None = None,
) -> None:
    purge_expired(db)
    detail: dict = {}
    if title:
        detail["title"] = title
    if summary:
        detail["summary"] = summary
    if extra:
        detail.update(extra)
    db.add(
        AuditLog(
            actor=actor,
            action=action,
            product=product,
            resource=(resource or "")[:120],
            detail=detail or None,
        )
    )


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _href(action: str, product: str | None, resource: str, db: Session | None = None, crypto: ContentCrypto | None = None) -> str | None:
    p = (product or "usp").lower()
    if action == "board.save":
        if db is not None and crypto is not None:
            return board_display(db, crypto, p, resource)[1]
        return f"/{p}/editor"
    if action.startswith("requirement"):
        return f"/{p}/requirements/{resource}" if resource else f"/{p}/requirements"
    if action.startswith("product"):
        return f"/{p}/releases"
    rid = resource.split(":")[0] if resource else ""
    if rid.startswith("req_"):
        return f"/{p}/requirements/{rid}"
    if rid:
        return f"/{p}/m/{rid}"
    return f"/{p}"


def _display_title(row: AuditLog, db: Session, crypto: ContentCrypto) -> str:
    d = dict(row.detail or {})
    title = str(d.get("title") or "")
    p = row.product or "usp"
    if row.action == "board.save":
        return board_display(db, crypto, p, row.resource)[0]
    if row.action == "product.delete":
        return title or product_label(p)
    if row.action.startswith("product"):
        return release_label(db, p, row.resource)
    if _looks_internal(title):
        if row.action.startswith("requirement"):
            return row.resource or "需求"
        mid = (row.resource or "").split(":")[0]
        return module_title(db, crypto, p, mid) if mid else ACTION_LABEL.get(row.action, row.action)
    if title:
        return title
    if row.action.startswith("requirement"):
        return row.resource or "需求"
    mid = (row.resource or "").split(":")[0]
    if mid:
        return module_title(db, crypto, p, mid)
    return ACTION_LABEL.get(row.action, row.action)


def _safe_href(href: str | None, action: str, product: str | None, resource: str, db: Session, crypto: ContentCrypto) -> str | None:
    h = (href or "").strip() or None
    if h and _looks_internal(h):
        h = _href(action, product, resource, db, crypto)
    if h and _looks_internal(h):
        p = (product or "usp").lower()
        return f"/{p}/editor" if action == "board.save" else f"/{p}"
    return h


def _item(row: AuditLog, db: Session, crypto: ContentCrypto, labels: dict[str, str] | None = None) -> dict:
    d = dict(row.detail or {})
    summary = str(d.get("summary") or "")
    action_label = ACTION_LABEL.get(row.action, row.action)
    if summary == action_label or _looks_internal(summary):
        summary = ""
    title = _display_title(row, db, crypto)
    if _looks_internal(title):
        title = action_label
    href = _safe_href(_href(row.action, row.product, row.resource, db, crypto), row.action, row.product, row.resource, db, crypto)
    return {
        "id": row.id,
        "at": _iso(row.at),
        "actor": (labels or {}).get(row.actor, row.actor),
        "action": row.action,
        "action_label": action_label,
        "product": row.product,
        "title": title,
        "summary": summary,
        "href": href,
    }


def list_updates(
    db: Session, principal: Principal, crypto: ContentCrypto, *, days: int = KEEP_DAYS, limit: int = 200
) -> tuple[int, dict]:
    if not can_manage_users(principal):
        return 403, {"ok": False, "error": "forbidden"}
    days = max(1, min(int(days or KEEP_DAYS), KEEP_DAYS))
    limit = max(1, min(int(limit or 200), 200))
    purge_expired(db)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.at >= cutoff, AuditLog.action.in_(CONTENT_ACTIONS))
            .order_by(AuditLog.id.desc())
            .limit(limit)
        )
    )
    labels = map_db(db)
    return 200, {"ok": True, "days": days, "items": [_item(r, db, crypto, labels) for r in rows]}
