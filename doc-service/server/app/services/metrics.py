from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Board, Document, Module, ModuleVersion

METRICS_KIND = "metrics"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "metrics"

# 158×46 nodes, 32px 横向间隙 / 58px 纵向间隙（原 agv-eval 按 132 宽排，直接套会叠）。
_NW, _NH = 158, 46
_GX, _GY = 32, 58
_PX, _PY = _NW + _GX, _NH + _GY
_X0, _Y0 = 40, 40
_CX = _X0 + (5 * _NW + 4 * _GX) / 2


def _row_x(count: int, i: int) -> int:
    span = count * _NW + max(0, count - 1) * _GX
    start = _CX - span / 2
    return int(start + i * _PX)


_POS = {
    "theory": (_row_x(1, 0), _Y0 + 0 * _PY),
    "mapf": (_row_x(3, 0), _Y0 + 1 * _PY),
    "assign": (_row_x(3, 1), _Y0 + 1 * _PY),
    "method": (_row_x(3, 2), _Y0 + 1 * _PY),
    "lmapf": (_row_x(2, 0), _Y0 + 2 * _PY),
    "lassign": (_row_x(2, 1), _Y0 + 2 * _PY),
    "bridge": (_row_x(2, 0), _Y0 + 3 * _PY),
    "obsCoding": (_row_x(2, 1), _Y0 + 3 * _PY),
    "ljoint": (_row_x(2, 0), _Y0 + 4 * _PY),
    "fact": (_row_x(2, 1), _Y0 + 4 * _PY),
    "boardA": (_row_x(5, 0), _Y0 + 5 * _PY),
    "boardB": (_row_x(5, 1), _Y0 + 5 * _PY),
    "boardC": (_row_x(5, 2), _Y0 + 5 * _PY),
    "boardD": (_row_x(5, 3), _Y0 + 5 * _PY),
    "sense": (_row_x(5, 4), _Y0 + 5 * _PY),
    "client": (_row_x(2, 0), _Y0 + 6 * _PY),
    "delivery": (_row_x(2, 1), _Y0 + 6 * _PY),
    "biz": (_row_x(1, 0), _Y0 + 7 * _PY),
}

_EDGES = [
    ("theory", "mapf"),
    ("theory", "assign"),
    ("theory", "method"),
    ("mapf", "lmapf"),
    ("assign", "lassign"),
    ("method", "bridge"),
    ("lmapf", "bridge"),
    ("lassign", "bridge"),
    ("bridge", "obsCoding"),
    ("bridge", "ljoint"),
    ("bridge", "fact"),
    ("obsCoding", "fact"),
    ("ljoint", "boardA"),
    ("ljoint", "boardB"),
    ("fact", "boardC"),
    ("fact", "boardD"),
    ("fact", "sense"),
    ("boardA", "client"),
    ("boardB", "client"),
    ("boardC", "delivery"),
    ("boardD", "delivery"),
    ("sense", "client"),
    ("sense", "delivery"),
    ("client", "biz"),
    ("delivery", "biz"),
]


def metrics_host_id(product: str) -> str:
    return f"{product}_metrics"


def metrics_child_id(product: str, stem: str) -> str:
    return f"{product}_m_{stem}"


def metrics_board_rel(product: str) -> str:
    return f"boards/{metrics_host_id(product)}.json"


def is_metrics_host(product: str, module_id: str, kind: str | None = None) -> bool:
    if module_id != metrics_host_id(product):
        return False
    return kind is None or kind == METRICS_KIND


def _catalog() -> dict:
    path = DATA_DIR / "portal.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"modules": []}


def _rewrite_links(text: str, product: str, modules: list[dict]) -> str:
    dest = {m["id"]: f"/{product}/m/{metrics_child_id(product, m['id'])}" for m in modules}
    hrefs: dict[str, str] = {}
    for m in modules:
        d = dest[m["id"]]
        fname = m.get("file") or ""
        for raw in (
            fname,
            f"./{fname}",
            f"m/{m['id']}.html",
            f"./m/{m['id']}.html",
            f"{m['id']}.html",
            f"{m['id']}.md",
            f"./{m['id']}.md",
        ):
            if raw:
                hrefs[raw] = d

    def md_repl(match: re.Match[str]) -> str:
        label, link = match.group(1), match.group(2)
        return f"[{label}]({hrefs.get(link, link)})"

    def href_repl(match: re.Match[str]) -> str:
        q, link = match.group(1), match.group(2)
        return f"href={q}{hrefs.get(link, link)}{q}"

    out = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", md_repl, text or "")
    out = re.sub(r"""href\s*=\s*(['"])([^'"]+)\1""", href_repl, out, flags=re.I)
    out = out.replace("读图时只需记住三件事：\n\n", "")
    return out


def _hub_markdown(product: str) -> str:
    path = DATA_DIR / product / "hub.md"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _child_markdown(product: str, stem: str, modules: list[dict]) -> str:
    path = DATA_DIR / product / f"{stem}.md"
    if not path.is_file():
        return ""
    return _rewrite_links(path.read_text(encoding="utf-8"), product, modules)


def _upsert_module(
    db: Session,
    crypto: ContentCrypto,
    *,
    product: str,
    mid: str,
    title: str,
    sub: str,
    board_rel: str,
    nested_board: str | None,
    parent_id: str | None,
) -> Module:
    m = db.scalar(select(Module).where(Module.product == product, Module.id == mid))
    title_blob = crypto.encrypt(title, aad=f"module.title:{product}:{mid}")
    sub_blob = crypto.encrypt(sub, aad=f"module.sub:{product}:{mid}") if sub else None
    if m:
        m.title_blob = title_blob
        m.sub_blob = sub_blob
        m.kind = METRICS_KIND
        m.board_rel = board_rel
        m.parent_id = parent_id
        if nested_board:
            m.nested_board = nested_board
        return m
    m = Module(
        id=mid,
        product=product,
        board_rel=board_rel,
        kind=METRICS_KIND,
        encrypted=False,
        title_blob=title_blob,
        sub_blob=sub_blob,
        current_version="v1.0",
        parent_id=parent_id,
        nested_board=nested_board,
    )
    db.add(m)
    db.add(
        ModuleVersion(
            product=product,
            module_id=mid,
            semver="v1.0",
            lifecycle_band="done",
            progress=100.0,
        )
    )
    return m


def _put_doc(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    mid: str,
    markdown: str,
    *,
    overwrite: bool,
) -> None:
    row = db.scalar(
        select(Document).where(Document.product == product, Document.module_id == mid, Document.version == "v1.0")
    )
    if row and not overwrite:
        body = crypto.decrypt_text(row.body_blob, aad=f"document:{product}:{mid}:v1.0")
        if (body or "").strip():
            return
    blob = crypto.encrypt(markdown, aad=f"document:{product}:{mid}:v1.0")
    h = crypto.content_hash(markdown)
    if row:
        row.body_blob = blob
        row.content_hash = h
        return
    db.add(
        Document(
            product=product,
            module_id=mid,
            version="v1.0",
            body_blob=blob,
            content_hash=h,
            updated_by="system",
        )
    )


def _usp_board_doc(product: str, modules: list[dict]) -> dict:
    nodes = []
    for m in modules:
        stem = m["id"]
        x, y = _POS.get(stem, (40, 40))
        nodes.append(
            {
                "id": metrics_child_id(product, stem),
                "x": x,
                "y": y,
                "w": 158,
                "h": 46,
            }
        )
    return {
        "node_w": 158,
        "node_h": 46,
        "nodes": nodes,
        "groups": [],
        "edges": [
            {"from": metrics_child_id(product, a), "to": metrics_child_id(product, b)}
            for a, b in _EDGES
            if a in _POS and b in _POS
        ],
    }


def seed_product_metrics(
    db: Session,
    crypto: ContentCrypto,
    product: str,
    *,
    overwrite: bool = False,
) -> dict:
    """Create the per-product 指标 host. USP also loads the AGV eval graph + docs."""
    product = (product or "").strip().lower()
    if product not in ("usp", "ors"):
        raise ValueError("bad_product")
    hub_id = "usp_algo" if product == "usp" else "ors_algo"
    host_id = metrics_host_id(product)
    rel = metrics_board_rel(product)
    root = "board.json" if product == "usp" else "boards/ors.json"
    catalog = _catalog()
    modules = list(catalog.get("modules") or []) if product == "usp" else []

    _upsert_module(
        db,
        crypto,
        product=product,
        mid=host_id,
        title="指标",
        sub="",
        board_rel=root,
        nested_board=rel,
        parent_id=hub_id,
    )
    _put_doc(db, crypto, product, host_id, _hub_markdown(product), overwrite=overwrite)

    if product == "usp" and modules:
        for spec in modules:
            stem = spec["id"]
            cid = metrics_child_id(product, stem)
            _upsert_module(
                db,
                crypto,
                product=product,
                mid=cid,
                title=spec.get("title") or stem,
                sub=spec.get("sub") or "",
                board_rel=rel,
                nested_board=None,
                parent_id=host_id,
            )
            _put_doc(db, crypto, product, cid, _child_markdown(product, stem, modules), overwrite=overwrite)
        board_doc = _usp_board_doc(product, modules)
    else:
        board_doc = {"node_w": 158, "node_h": 46, "nodes": [], "groups": [], "edges": []}

    board = db.scalar(select(Board).where(Board.product == product, Board.rel_path == rel))
    if board:
        if overwrite or not ((board.doc or {}).get("nodes") or []):
            board.doc = board_doc
            board.updated_by = "system"
    else:
        db.add(Board(product=product, rel_path=rel, doc=board_doc, updated_by="system"))
    db.flush()
    return {"ok": True, "product": product, "host": host_id, "children": len(modules)}


def sync_metrics_layout(db: Session, product: str = "usp") -> dict:
    """Rewrite 指标子图坐标，不改正文。"""
    product = (product or "").strip().lower()
    if product != "usp":
        return {"ok": True, "updated": 0}
    catalog = _catalog()
    modules = list(catalog.get("modules") or [])
    rel = metrics_board_rel(product)
    board = db.scalar(select(Board).where(Board.product == product, Board.rel_path == rel))
    if not board:
        return {"ok": False, "error": "missing_board"}
    board.doc = _usp_board_doc(product, modules)
    board.updated_by = "system"
    db.flush()
    return {"ok": True, "updated": len(modules)}
