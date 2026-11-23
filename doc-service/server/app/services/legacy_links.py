from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_APP_PAGES = {
    "login": "/login",
    "account": "account",
    "admin-users": "users",
    "hours": "hours/history",
    "hours-history": "hours/history",
    "req": "requirements",
    "editor": "editor",
    "product-versions": "releases",
    "updates": "audit",
    "index": "/",
    "portal": "/",
    "usp": "/usp",
    "ors": "/ors",
}

_HREF_RE = re.compile(r"""href\s*=\s*(['"])([^'"]+)\1""", re.I)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_MOD_FILE_RE = re.compile(
    r"""^(?:(?:\./|\.\./(?:m/)?|/m/|m/))?(?:
        (?P<id>[A-Za-z][A-Za-z0-9_]*)
        |
        (?P<numbered>\d+[a-z]?-(?:[A-Za-z][A-Za-z0-9_]*)(?:-[A-Za-z][A-Za-z0-9_]*)*)
    )(?:-(?P<ver>v\d+(?:\.\d+)*))?\.(?:html|md)$""",
    re.I | re.VERBOSE,
)
_OLD_PORT = re.compile(r":8766$", re.I)
_NUM_PREFIX = re.compile(r"^\d+[a-z]?-", re.I)
_FILE_ALIASES = {
    "sv": "sv_plan",
    "mv": "mv_plan",
    "mapf": "mv_cent",
    "biz_assign": "biz",
    "assign": "school_as",
    "env": "env_ext",
    "behavior": "beh_ext",
    "assign_prio": "beh_assign_prio",
    "path_prio": "beh_path_prio",
    "task_dep": "beh_task_dep",
}


def _semver_key(raw: str) -> tuple:
    s = (raw or "").strip().lstrip("vV")
    parts: list[tuple] = []
    for p in re.split(r"[.\-+]", s):
        if not p:
            continue
        parts.append((0, int(p)) if p.isdigit() else (1, p))
    return tuple(parts)


def _previous_version(current: str | None, versions: list[str]) -> str | None:
    ordered = sorted({v for v in versions if v}, key=_semver_key)
    if not ordered:
        return None
    cur = (current or "").strip()
    if cur in ordered:
        i = ordered.index(cur)
        return ordered[i - 1] if i > 0 else None
    others = [v for v in ordered if v != cur]
    return others[-1] if others else None


def _is_old_origin(netloc: str) -> bool:
    host = (netloc or "").strip().lower()
    if not host:
        return False
    if _OLD_PORT.search(host):
        return True
    name = host.split(":")[0]
    return name in {"10.10.10.202"}


def _product_for_module(
    mid: str,
    default: str,
    ids_by_product: dict[str, set[str]],
) -> str:
    usp = ids_by_product.get("usp") or set()
    ors = ids_by_product.get("ors") or set()
    in_usp = mid in usp
    in_ors = mid in ors
    if in_ors and not in_usp:
        return "ors"
    if in_usp and not in_ors:
        return "usp"
    if mid.startswith("ors"):
        return "ors"
    if mid.startswith("usp"):
        return "usp"
    return default


def _all_ids(ids_by_product: dict[str, set[str]]) -> set[str]:
    out: set[str] = set()
    for s in ids_by_product.values():
        out |= set(s or ())
    return out


def _stem_to_module_id(stem: str, known: set[str]) -> str:
    raw = (stem or "").strip()
    if not raw:
        return raw
    if raw in known:
        return raw
    slug = _NUM_PREFIX.sub("", raw).replace("-", "_")
    cand = _FILE_ALIASES.get(slug, slug)
    if not known or cand in known:
        return cand
    if slug in known:
        return slug
    return cand


def convert_legacy_href(
    raw: str,
    *,
    product: str,
    module_id: str | None = None,
    versions: list[str] | None = None,
    current_version: str | None = None,
    ids_by_product: dict[str, set[str]] | None = None,
    known_ids: set[str] | None = None,
) -> str:
    """Map old-portal URLs to new SPA paths. Unrecognized values are returned unchanged."""
    src = (raw or "").strip()
    if not src or re.match(r"^(mailto:|javascript:|data:|blob:)", src, re.I):
        return src
    if src.startswith("/api/"):
        return src
    prod = (product or "").strip().lower() or "usp"
    catalog = {k: set(v or ()) for k, v in (ids_by_product or {}).items()}
    if known_ids:
        catalog.setdefault(prod, set()).update(x for x in known_ids if x)
    known = _all_ids(catalog)

    if src.startswith("#"):
        return src

    parsed = urlparse(src)
    frag = ("#" + parsed.fragment) if parsed.fragment else ""
    if parsed.scheme in ("http", "https") and parsed.netloc and not _is_old_origin(parsed.netloc):
        return src

    path = parsed.path or ""
    query = parsed.query
    if parsed.scheme in ("http", "https") and _is_old_origin(parsed.netloc):
        path = parsed.path or "/"
    elif not path and not parsed.netloc:
        path = src.split("?", 1)[0].split("#", 1)[0]
        query = parsed.query

    qs = parse_qs(query)
    file_part = path.strip()
    if file_part.startswith("/m/"):
        file_part = file_part[3:]
    elif file_part.startswith("m/"):
        file_part = file_part[2:]
    file_part = file_part.rsplit("/", 1)[-1]

    req_id = (qs.get("id") or [None])[0]
    stem = file_part.lower().rsplit(".", 1)[0] if file_part else ""
    if stem in ("req",) or file_part.lower() in {"req.html", "req.md"}:
        if req_id:
            req_prod = "ors" if str(req_id).startswith("ors") else prod
            return f"/{req_prod}/requirements/{req_id}{frag}"
        return f"/{prod}/requirements{frag}"

    hit = _MOD_FILE_RE.match(file_part) or _MOD_FILE_RE.match(path.lstrip("/"))
    if not hit:
        rel = file_part or path.lstrip("/")
        hit = _MOD_FILE_RE.match(rel)

    if hit:
        numbered = hit.group("numbered")
        mid, ver = (numbered or hit.group("id") or ""), hit.group("ver")
        key = (mid or "").lower()
        if key in _APP_PAGES and mid not in known:
            dest = _APP_PAGES[key]
            if dest == "/login":
                return "/login" + frag
            if dest.startswith("/"):
                return dest + frag
            return f"/{prod}/{dest}{frag}"
        dest_id, dest_ver = mid, ver
        if numbered:
            dest_id = _stem_to_module_id(mid, known)
        elif not dest_ver and module_id and known and mid not in known:
            prev = _previous_version(current_version, list(versions or []))
            if prev:
                dest_id, dest_ver = module_id, prev
        dest_prod = _product_for_module(dest_id, prod, catalog)
        dest = f"/{dest_prod}/m/{dest_id}"
        if dest_ver:
            dest += f"?v={dest_ver}"
        return dest + frag

    if path in {"/", ""} and parsed.scheme in ("http", "https") and _is_old_origin(parsed.netloc):
        return "/usp" + frag
    return src


def rewrite_legacy_markdown(
    text: str,
    *,
    product: str,
    module_id: str | None = None,
    versions: list[str] | None = None,
    current_version: str | None = None,
    ids_by_product: dict[str, set[str]] | None = None,
    known_ids: set[str] | None = None,
) -> str:
    kwargs = dict(
        product=product,
        module_id=module_id,
        versions=versions,
        current_version=current_version,
        ids_by_product=ids_by_product,
        known_ids=known_ids,
    )

    def md_repl(m: re.Match[str]) -> str:
        return f"[{m.group(1)}]({convert_legacy_href(m.group(2), **kwargs)})"

    def href_repl(m: re.Match[str]) -> str:
        return f"href={m.group(1)}{convert_legacy_href(m.group(2), **kwargs)}{m.group(1)}"

    out = _MD_LINK_RE.sub(md_repl, text or "")
    return _HREF_RE.sub(href_repl, out)


def rewrite_stored_legacy_links(db, crypto) -> dict[str, int]:
    """Rewrite leftover old-portal .md/.html hrefs in stored markdown. Idempotent."""
    from sqlalchemy import select

    from app.infra.models import (
        Document,
        Draft,
        Module,
        ModuleVersion,
        ProductRelease,
        Requirement,
        RequirementDoc,
    )

    catalog = {
        "usp": set(db.scalars(select(Module.id).where(Module.product == "usp"))),
        "ors": set(db.scalars(select(Module.id).where(Module.product == "ors"))),
    }
    vers: dict[tuple[str, str], list[str]] = {}
    for row in db.scalars(select(ModuleVersion)):
        vers.setdefault((row.product, row.module_id), []).append(row.semver)

    changed = {"doc": 0, "req": 0, "draft": 0, "rel": 0}

    for d in db.scalars(select(Document)):
        aad = f"document:{d.product}:{d.module_id}:{d.version}"
        body = crypto.decrypt_text(d.body_blob, aad=aad)
        new = rewrite_legacy_markdown(
            body,
            product=d.product,
            module_id=d.module_id,
            versions=vers.get((d.product, d.module_id), []),
            current_version=d.version,
            ids_by_product=catalog,
        )
        if new != body:
            d.body_blob = crypto.encrypt(new, aad=aad)
            d.content_hash = crypto.content_hash(new)
            changed["doc"] += 1

    for r in db.scalars(select(RequirementDoc)):
        req = db.get(Requirement, r.requirement_id)
        prod = req.product if req else "usp"
        aad = f"requirement:{r.requirement_id}"
        body = crypto.decrypt_text(r.body_blob, aad=aad)
        new = rewrite_legacy_markdown(body, product=prod, ids_by_product=catalog)
        if new != body:
            r.body_blob = crypto.encrypt(new, aad=aad)
            r.content_hash = crypto.content_hash(new)
            changed["req"] += 1

    for r in db.scalars(select(Draft)):
        aad = f"draft:{r.id}"
        body = crypto.decrypt_text(r.body_blob, aad=aad)
        new = rewrite_legacy_markdown(body, product="usp", ids_by_product=catalog)
        if new != body:
            r.body_blob = crypto.encrypt(new, aad=aad)
            r.content_hash = crypto.content_hash(new)
            changed["draft"] += 1

    for rel in db.scalars(select(ProductRelease)):
        for field, blob in (("added", rel.added_blob), ("fixed", rel.fixed_blob), ("remark", rel.remark_blob)):
            if not blob:
                continue
            aad = f"release.{field}:{rel.id}"
            body = crypto.decrypt_text(blob, aad=aad)
            new = rewrite_legacy_markdown(body, product=rel.product, ids_by_product=catalog)
            if new == body:
                continue
            setattr(rel, f"{field}_blob", crypto.encrypt(new, aad=aad))
            changed["rel"] += 1

    return changed
