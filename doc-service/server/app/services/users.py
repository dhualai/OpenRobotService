from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import Board, Grant, Module, User, UserProduct
from app.policy.engine import ROLES
from app.services.auth import hash_password
from app.services.names import label
from app.services.topology import domain_boards, domain_of

HUB_GROUPS = frozenset({"usp_algo", "ors_algo"})
SKIP_GRANT_KINDS = frozenset({"product_hub", "product_release", "metrics"})

ROLE_LABEL = {
    "admin": "管理员",
    "engineer": "开发工程师",
    "tester": "测试工程师",
    "pm": "产品经理",
    "pjm": "项目经理",
    "supervisor": "部门主管",
    "guest": "访客",
}


def _products_of(db: Session, username: str) -> list[str]:
    return list(db.scalars(select(UserProduct.product).where(UserProduct.username == username)))


def _perms_of(db: Session, username: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for g in db.scalars(select(Grant).where(Grant.username == username)):
        if g.kind == "product_all":
            out[f"{g.product}:*"] = g.perm
        elif g.kind == "domain":
            out[f"{g.product}:{g.resource_id}:*"] = g.perm
        else:
            out[g.resource_id] = g.perm
    return out


def public_user(db: Session, user: User) -> dict:
    return {
        "username": user.username,
        "name": label(user.username, user.display_name),
        "role": user.role,
        "role_label": ROLE_LABEL.get(user.role, user.role),
        "products": _products_of(db, user.username),
        "module_perms": _perms_of(db, user.username),
    }


def list_users(db: Session) -> dict:
    rows = list(db.scalars(select(User)))
    rows.sort(key=lambda u: (0 if u.role == "admin" else 1, u.username))
    return {"ok": True, "users": [public_user(db, u) for u in rows]}


def _parse_perms(db: Session, module_perms: dict, products: list[str]) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(product: str, kind: str, resource_id: str, perm: str) -> None:
        key = (product, kind, resource_id)
        if key in seen:
            return
        seen.add(key)
        out.append((product, kind, resource_id, perm))

    for raw_key, raw_level in (module_perms or {}).items():
        mid = str(raw_key or "").strip()
        level = str(raw_level or "").strip().lower()
        if not mid or level in ("", "none", "null"):
            continue
        if level not in ("write", "read"):
            continue
        if mid in ("*", "all", "ALL"):
            for p in products:
                add(p, "product_all", "", level)
            continue
        parts = mid.split(":")
        if len(parts) == 2 and parts[1] == "*":
            prod = parts[0].lower()
            if prod in ("usp", "ors"):
                add(prod, "product_all", "", level)
            continue
        if len(parts) == 3 and parts[2] == "*":
            prod = parts[0].lower()
            if prod in ("usp", "ors") and parts[1]:
                add(prod, "domain", parts[1], level)
            continue
        for m in db.scalars(select(Module).where(Module.id == mid)):
            add(m.product, "module", m.id, level)
        if not any(t[2] == mid and t[1] == "module" for t in out):
            for p in products:
                add(p, "module", mid, level)
    return out


def save_user(
    db: Session,
    *,
    username: str,
    role: str | None = None,
    products: list[str] | None = None,
    module_perms: dict | None = None,
    password: str | None = None,
    display_name: str | None = None,
) -> tuple[int, dict]:
    uname = (username or "").strip()
    if not uname:
        return 400, {"ok": False, "error": "missing_username"}
    user = db.get(User, uname)
    created = False
    if not user:
        if not password or len(password) < 8:
            return 400, {"ok": False, "error": "need_password"}
        role = role or "guest"
        if role not in ROLES:
            return 400, {"ok": False, "error": "invalid_role"}
        user = User(username=uname, password_hash=hash_password(password), role=role)
        db.add(user)
        db.flush()
        created = True
    else:
        if role is not None:
            if role not in ROLES:
                return 400, {"ok": False, "error": "invalid_role"}
            if user.role == "admin" and role != "admin":
                admins = list(db.scalars(select(User).where(User.role == "admin")))
                if len(admins) <= 1:
                    return 400, {"ok": False, "error": "last_admin"}
            user.role = role
        if password:
            if len(password) < 8:
                return 400, {"ok": False, "error": "need_password"}
            user.password_hash = hash_password(password)

    if display_name is not None:
        user.display_name = display_name.strip() or None

    if products is not None:
        cleaned: list[str] = []
        for p in products:
            p = str(p).strip().lower()
            if p in ("usp", "ors") and p not in cleaned:
                cleaned.append(p)
        db.execute(delete(UserProduct).where(UserProduct.username == uname))
        for p in cleaned:
            db.add(UserProduct(username=uname, product=p))
        products_now = cleaned
    else:
        products_now = _products_of(db, uname)

    if module_perms is not None:
        if not isinstance(module_perms, dict):
            return 400, {"ok": False, "error": "invalid_perms"}
        db.execute(delete(Grant).where(Grant.username == uname))
        for product, kind, resource_id, perm in _parse_perms(db, module_perms, products_now):
            db.add(
                Grant(
                    username=uname,
                    product=product,
                    kind=kind,
                    resource_id=resource_id,
                    perm=perm,
                )
            )

    db.flush()
    return (201 if created else 200), {"ok": True, "user": public_user(db, user)}


def _doc_nodes(doc: dict | None) -> list[dict]:
    return [n for n in (doc or {}).get("nodes") or [] if isinstance(n, dict) and n.get("id")]


def _nested_rel(m: Module, boards: dict[str, dict]) -> str | None:
    explicit = m.nested_board if _doc_nodes(boards.get(m.nested_board or "")) else None
    if explicit:
        return explicit
    conv = f"boards/{m.id}.json"
    return conv if _doc_nodes(boards.get(conv)) else None


def _grant_forest(
    crypto: ContentCrypto,
    product: str,
    rel: str,
    mods: dict[str, Module],
    boards: dict[str, dict],
) -> list[dict]:
    def title_of(mid: str, fallback: str = "") -> str:
        m = mods.get(mid)
        if m and m.title_blob:
            return crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        return fallback or mid

    def entry(mid: str, *, fallback: str = "", kind: str | None = None) -> dict | None:
        m = mods.get(mid)
        if m is None:
            if kind != "group":
                return None
            return {
                "id": mid,
                "title": fallback or mid,
                "kind": "group",
                "encrypted": False,
                "children": [],
            }
        if m.kind in SKIP_GRANT_KINDS:
            return None
        return {
            "id": m.id,
            "title": title_of(m.id, fallback),
            "kind": "group" if m.kind == "group" or kind == "group" else "feature",
            "encrypted": bool(m.encrypted),
            "children": [],
        }

    seen: set[str] = set()

    def nested_kids(host_id: str) -> list[dict]:
        host = mods.get(host_id)
        if not host:
            return []
        rel_n = _nested_rel(host, boards)
        if not rel_n:
            return []
        out: list[dict] = []
        for n in _doc_nodes(boards.get(rel_n)):
            cid = str(n["id"])
            if cid in seen:
                continue
            seen.add(cid)
            node = entry(cid)
            if not node:
                continue
            node["children"] = nested_kids(cid)
            out.append(node)
        return out

    doc = boards.get(rel) or {}
    groups = [
        g
        for g in (doc.get("groups") or [])
        if isinstance(g, dict) and g.get("id") and g["id"] not in HUB_GROUPS
    ]
    member_ids: set[str] = set()
    for g in groups:
        for mid in g.get("members") or []:
            if isinstance(mid, str):
                member_ids.add(mid)
    forest: list[dict] = []
    for g in groups:
        gid = str(g["id"])
        seen.add(gid)
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
        root = entry(gid, fallback=str(g.get("title") or gid), kind="group")
        if not root:
            forest.extend(kids)
            continue
        root["children"] = kids
        forest.append(root)
    orphans: list[dict] = []
    for n in _doc_nodes(doc):
        mid = str(n["id"])
        if mid in seen or mid in member_ids:
            continue
        seen.add(mid)
        node = entry(mid)
        if not node:
            continue
        node["children"] = nested_kids(mid)
        orphans.append(node)
    orphans.sort(key=lambda x: x["title"])
    forest.extend(orphans)
    on_tree = set()

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            on_tree.add(n["id"])
            walk(n.get("children") or [])

    walk(forest)
    extras = []
    for m in mods.values():
        if m.id in on_tree or m.kind in SKIP_GRANT_KINDS or m.board_rel != rel:
            continue
        extras.append(
            {
                "id": m.id,
                "title": title_of(m.id),
                "kind": "group" if m.kind == "group" else "feature",
                "encrypted": bool(m.encrypted),
                "children": nested_kids(m.id) if m.id not in seen else [],
            }
        )
    extras.sort(key=lambda x: x["title"])
    forest.extend(extras)
    return forest


def pack_catalog(db: Session, crypto: ContentCrypto, product: str) -> dict:
    domains = domain_boards(product)
    skip = SKIP_GRANT_KINDS
    modules = []
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    for m in mods.values():
        if m.kind in skip:
            continue
        title = m.id
        if m.title_blob:
            title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        modules.append(
            {
                "id": m.id,
                "title": title,
                "kind": "group" if m.kind == "group" else "feature",
                "domain": domain_of(product, m.board_rel) or "algo",
                "encrypted": bool(m.encrypted),
            }
        )
    modules.sort(key=lambda x: (0 if x["domain"] == "algo" else 1, x["domain"], x["title"]))
    boards = {
        b.rel_path: (b.doc or {})
        for b in db.scalars(select(Board).where(Board.product == product))
    }
    tree = []
    for d in domains:
        tree.append(
            {
                "id": d["id"],
                "tag": d["title"],
                "nodes": _grant_forest(crypto, product, d["rel_path"], mods, boards),
            }
        )
    return {
        "ok": True,
        "product": product,
        "domains": [{"id": d["id"], "tag": d["title"]} for d in domains],
        "modules": modules,
        "tree": tree,
    }
