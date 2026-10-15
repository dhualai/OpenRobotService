from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.crypto import ContentCrypto
from app.infra.models import (
    Board,
    HourAllocation,
    HourExpectation,
    HoursWeekClose,
    Module,
    ModuleVersion,
    ProductRelease,
    ProductTag,
    ReleaseCover,
)
from app.policy import Principal, can_browse_module, can_log_hours
from app.services.names import map_db
from app.services.topology import module_view

HOURS_SKIP_KINDS = frozenset({"group", "product_hub", "product_release", "metrics"})
HOURS_CHILD_SKIP_KINDS = frozenset({"product_hub", "product_release", "metrics"})
EXPECTED_HOURS_MAX = 64.0
_HOURS_TZ = ZoneInfo("Asia/Shanghai")


def clamp_personal_expected(n: float) -> float:
    return round(min(max(float(n), 0.0), EXPECTED_HOURS_MAX), 2)


def singleton_feature_user(db: Session, product: str, feature_id: str) -> str | None:
    names = [
        u
        for u in db.scalars(
            select(HourAllocation.username)
            .where(HourAllocation.product == product, HourAllocation.feature_id == feature_id)
            .distinct()
        )
        if u
    ]
    return names[0] if len(names) == 1 else None


def seed_singleton_expectations(db: Session) -> int:
    pairs = list(db.execute(select(HourAllocation.product, HourAllocation.feature_id).distinct()))
    n = 0
    for product, fid in pairs:
        who = singleton_feature_user(db, product, fid)
        if not who:
            continue
        m = db.scalar(select(Module).where(Module.product == product, Module.id == fid))
        ver = (m.current_version if m else None) or "v1.0"
        exists = db.scalar(
            select(HourExpectation.id).where(
                HourExpectation.username == who,
                HourExpectation.product == product,
                HourExpectation.feature_id == fid,
                HourExpectation.version == ver,
            )
        )
        if exists:
            continue
        mv = db.scalar(
            select(ModuleVersion).where(
                ModuleVersion.product == product,
                ModuleVersion.module_id == fid,
                ModuleVersion.semver == ver,
            )
        )
        if not mv or mv.expected_hours is None or float(mv.expected_hours) <= 0:
            continue
        db.add(
            HourExpectation(
                username=who,
                product=product,
                feature_id=fid,
                version=ver,
                expected_hours=clamp_personal_expected(mv.expected_hours),
            )
        )
        n += 1
    if n:
        db.flush()
    return n


def iso_week_id(d: date | None = None) -> str:
    d = d or date.today()
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_bounds(week_id: str) -> tuple[date, date]:
    year_s, w_s = week_id.split("-W")
    year, week = int(year_s), int(w_s)
    jan4 = date(year, 1, 4)
    start = jan4 - timedelta(days=jan4.isoweekday() - 1) + timedelta(weeks=week - 1)
    return start, start + timedelta(days=6)


def week_is_closed(week_id: str, now: datetime | None = None) -> bool:
    try:
        _, end = week_bounds(week_id)
    except (ValueError, TypeError):
        return False
    close = datetime.combine(end, time(23, 59, 59), tzinfo=_HOURS_TZ)
    now = now or datetime.now(_HOURS_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_HOURS_TZ)
    return now > close


def _expectation_row(
    db: Session, username: str, product: str, feature_id: str, version: str
) -> HourExpectation | None:
    return db.scalar(
        select(HourExpectation).where(
            HourExpectation.username == username,
            HourExpectation.product == product,
            HourExpectation.feature_id == feature_id,
            HourExpectation.version == version,
        )
    )


def personal_expected(
    db: Session, username: str, product: str, feature_id: str, version: str
) -> float:
    row = _expectation_row(db, username, product, feature_id, version)
    val = float(row.expected_hours) if row else 0.0
    return val if val > 0 else 0.0


def _clamp_current_week_hours(
    db: Session,
    username: str,
    product: str,
    feature_id: str,
    version: str,
    max_hours: float,
) -> None:
    week = iso_week_id()
    row = db.scalar(
        select(HourAllocation).where(
            HourAllocation.week == week,
            HourAllocation.username == username,
            HourAllocation.product == product,
            HourAllocation.feature_id == feature_id,
            HourAllocation.version == version,
        )
    )
    if not row:
        return
    cap = max(0.0, round(float(max_hours), 2))
    if cap <= 0:
        db.delete(row)
    elif float(row.hours) > cap + 1e-6:
        row.hours = cap


def close_elapsed_weeks(db: Session, now: datetime | None = None) -> int:
    closed = {
        (r.week, r.username, r.product, r.feature_id, r.version)
        for r in db.scalars(select(HoursWeekClose))
    }
    n = 0
    for r in list(db.scalars(select(HourAllocation))):
        key = (r.week, r.username, r.product, r.feature_id, r.version)
        if key in closed:
            continue
        if not week_is_closed(r.week, now):
            continue
        h = round(float(r.hours or 0), 2)
        exp = _expectation_row(db, r.username, r.product, r.feature_id, r.version)
        if exp:
            nxt = round(max(0.0, float(exp.expected_hours) - h), 2)
            if nxt <= 0:
                db.delete(exp)
            else:
                exp.expected_hours = nxt
        db.add(
            HoursWeekClose(
                week=r.week,
                username=r.username,
                product=r.product,
                feature_id=r.feature_id,
                version=r.version,
                hours=h,
            )
        )
        closed.add(key)
        n += 1
    if n:
        db.flush()
    return n


def week_total(db: Session, username: str, week: str) -> float:
    v = db.scalar(
        select(func.coalesce(func.sum(HourAllocation.hours), 0.0)).where(
            HourAllocation.username == username,
            HourAllocation.week == week,
        )
    )
    return float(v or 0)


def list_week(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    week: str,
    hard_cap: float,
) -> dict:
    close_elapsed_weeks(db)
    rows = list(
        db.scalars(
            select(HourAllocation).where(
                HourAllocation.product == product,
                HourAllocation.week == week,
            )
        )
    )
    mods = {
        m.id: m
        for m in db.scalars(select(Module).where(Module.product == product))
    }

    def title_of(fid: str) -> str:
        m = mods.get(fid)
        if not m or not can_browse_module(principal, module_view(m)):
            return fid
        if m.title_blob:
            return crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        return m.id

    labels = map_db(db)
    items = [
        {
            "user": r.username,
            "name": labels.get(r.username, r.username),
            "feature_id": r.feature_id,
            "title": title_of(r.feature_id),
            "version": r.version,
            "hours": r.hours,
        }
        for r in rows
    ]
    mine = sum(r.hours for r in rows if r.username == principal.username)
    features = []
    for m in sorted(mods.values(), key=lambda x: x.id):
        if m.kind in HOURS_SKIP_KINDS:
            continue
        if not can_browse_module(principal, module_view(m)):
            continue
        features.append(
            {
                "id": m.id,
                "title": title_of(m.id),
                "version": m.current_version,
            }
        )
    features.sort(key=lambda x: x["title"])
    weeks = {
        w
        for w in db.scalars(
            select(HourAllocation.week).where(HourAllocation.product == product).distinct()
        )
        if w
    }
    weeks.add(week)
    return {
        "ok": True,
        "week": week,
        "my_total": mine,
        "cap": hard_cap,
        "can_log": can_log_hours(principal, product),
        "weeks": sorted(weeks, reverse=True),
        "features": features,
        "items": items,
    }


def list_registerable(
    db: Session,
    crypto: ContentCrypto,
    principal: Principal,
    product: str,
    week: str,
) -> dict:
    close_elapsed_weeks(db)
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    vers = {
        (r.module_id, r.semver): r
        for r in db.scalars(select(ModuleVersion).where(ModuleVersion.product == product))
    }
    mine_feat: dict[tuple[str, str], float] = {}
    for r in db.scalars(
        select(HourAllocation).where(
            HourAllocation.product == product,
            HourAllocation.week == week,
            HourAllocation.username == principal.username,
        )
    ):
        key = (r.feature_id, r.version)
        mine_feat[key] = mine_feat.get(key, 0.0) + r.hours
    invested_rows = db.execute(
        select(
            HourAllocation.feature_id,
            func.coalesce(func.sum(HourAllocation.hours), 0.0),
        )
        .where(HourAllocation.product == product)
        .group_by(HourAllocation.feature_id)
    ).all()
    invested_map = {a: float(b) for a, b in invested_rows}
    out = []
    for m in mods.values():
        if m.kind in HOURS_SKIP_KINDS:
            continue
        if not can_browse_module(principal, module_view(m)):
            continue
        ver = m.current_version or "v1.0"
        mv = vers.get((m.id, ver))
        band = (mv.lifecycle_band if mv else None) or "pre"
        if band == "done":
            continue
        invested = invested_map.get(m.id, 0.0)
        expected = mv.expected_hours if mv else None
        remaining = None if expected is None else max(0.0, round(float(expected) - invested, 2))
        title = m.id
        if m.title_blob:
            title = crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        out.append(
            {
                "id": m.id,
                "title": title,
                "version": ver,
                "band": band,
                "invested_total": invested,
                "expected_hours": expected,
                "has_expected_hours": expected is not None,
                "remaining_hours": remaining,
                "my_week": float(mine_feat.get((m.id, ver), 0) or 0),
            }
        )
    out.sort(key=lambda x: x["title"])
    return {"ok": True, "week": week, "features": out}


def set_remaining(
    db: Session,
    principal: Principal,
    *,
    product: str,
    feature_id: str,
    version: str,
    remaining_hours: float | None,
) -> tuple[int, dict]:
    if not can_log_hours(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    mv = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == feature_id,
            ModuleVersion.semver == version,
        )
    )
    if not mv:
        return 404, {"ok": False, "error": "not_found"}
    if mv.lifecycle_band == "done":
        return 403, {"ok": False, "error": "settled"}
    if remaining_hours is None:
        return 200, {"ok": True}
    if remaining_hours < 0:
        return 400, {"ok": False, "error": "invalid_hours"}
    invested = float(
        db.scalar(
            select(func.coalesce(func.sum(HourAllocation.hours), 0.0)).where(
                HourAllocation.product == product,
                HourAllocation.feature_id == feature_id,
                HourAllocation.version == version,
            )
        )
        or 0
    )
    nxt = invested + remaining_hours
    if nxt > EXPECTED_HOURS_MAX + 1e-6:
        return 400, {"ok": False, "error": "invalid_hours"}
    mv.expected_hours = nxt
    return 200, {"ok": True, "expected_hours": mv.expected_hours}


def set_expected(
    db: Session,
    principal: Principal,
    *,
    product: str,
    feature_id: str,
    version: str,
    expected_hours: float,
) -> tuple[int, dict]:
    if not can_log_hours(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    if expected_hours < 0 or expected_hours > EXPECTED_HOURS_MAX + 1e-6:
        return 400, {"ok": False, "error": "invalid_hours"}
    mv = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == feature_id,
            ModuleVersion.semver == version,
        )
    )
    if not mv:
        return 404, {"ok": False, "error": "not_found"}
    if mv.lifecycle_band == "done":
        return 403, {"ok": False, "error": "settled"}
    close_elapsed_weeks(db)
    row = _expectation_row(db, principal.username, product, feature_id, version)
    val = round(float(expected_hours), 2)
    if val <= 0:
        if row:
            db.delete(row)
        _clamp_current_week_hours(db, principal.username, product, feature_id, version, 0)
        db.flush()
        return 200, {"ok": True, "expected_hours": None}
    if row:
        row.expected_hours = val
    else:
        db.add(
            HourExpectation(
                username=principal.username,
                product=product,
                feature_id=feature_id,
                version=version,
                expected_hours=val,
            )
        )
    _clamp_current_week_hours(db, principal.username, product, feature_id, version, val)
    db.flush()
    return 200, {"ok": True, "expected_hours": val}


def save_week(
    db: Session,
    principal: Principal,
    *,
    product: str,
    week: str,
    allocations: list[dict],
    remaining: list[dict] | None,
    hard_cap: float,
) -> tuple[int, dict]:
    if not can_log_hours(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    for a in allocations:
        code, out = upsert_hours(
            db,
            principal,
            product=product,
            week=week,
            feature_id=a.get("feature_id") or "",
            version=a.get("version") or "v1.0",
            hours=float(a.get("hours") or 0),
            hard_cap=hard_cap,
        )
        if code != 200:
            return code, out
    db.flush()
    for row in remaining or []:
        code, out = set_remaining(
            db,
            principal,
            product=product,
            feature_id=row.get("feature_id") or "",
            version=row.get("version") or "v1.0",
            remaining_hours=row.get("remaining_hours"),
        )
        if code != 200:
            return code, out
    total = week_total(db, principal.username, week)
    return 200, {"ok": True, "week": week, "my_total": total}


def list_history(
    db: Session, crypto: ContentCrypto, principal: Principal, product: str, hard_cap: float = 40.0
) -> dict:
    close_elapsed_weeks(db)
    current = iso_week_id()
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}

    def title_of(fid: str) -> str:
        m = mods.get(fid)
        if not m or not can_browse_module(principal, module_view(m)):
            return fid
        if m.title_blob:
            return crypto.decrypt_text(m.title_blob, aad=f"module.title:{product}:{m.id}")
        return m.id

    buckets: dict[str, dict[str, dict]] = {}
    for r in db.scalars(select(HourAllocation).where(HourAllocation.product == product)):
        h = float(r.hours or 0)
        if h <= 0:
            continue
        users = buckets.setdefault(r.week, {})
        u = users.setdefault(r.username, {"hours": 0.0, "feats": {}})
        u["hours"] += h
        key = (r.feature_id, r.version)
        u["feats"][key] = u["feats"].get(key, 0.0) + h

    labels = map_db(db)
    items = []
    for week in sorted(buckets, reverse=True):
        by_user = []
        for user, u in buckets[week].items():
            feats = [
                {
                    "id": fid,
                    "title": title_of(fid),
                    "version": ver,
                    "hours": round(hrs, 2),
                }
                for (fid, ver), hrs in u["feats"].items()
            ]
            feats.sort(key=lambda x: (-x["hours"], x["title"]))
            by_user.append(
                {
                    "user": user,
                    "name": labels.get(user, user),
                    "hours": round(u["hours"], 2),
                    "features": feats,
                }
            )
        by_user.sort(key=lambda x: (-x["hours"], x["user"]))
        try:
            start, end = week_bounds(week)
            span = {"start": start.isoformat(), "end": end.isoformat()}
        except (ValueError, TypeError):
            span = {}
        items.append(
            {
                "week": week,
                "open": week == current,
                "total": round(sum(x["hours"] for x in by_user), 2),
                "by_user": by_user,
                **span,
            }
        )
    return {"ok": True, "week": current, "cap": hard_cap, "items": items}


def upsert_hours(
    db: Session,
    principal: Principal,
    *,
    product: str,
    week: str,
    feature_id: str,
    version: str,
    hours: float,
    hard_cap: float,
) -> tuple[int, dict]:
    if not can_log_hours(principal, product):
        return 403, {"ok": False, "error": "forbidden"}
    if hours < 0:
        return 400, {"ok": False, "error": "invalid_hours"}
    close_elapsed_weeks(db)
    mv = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == feature_id,
            ModuleVersion.semver == version,
        )
    )
    invested = db.scalar(
        select(func.coalesce(func.sum(HourAllocation.hours), 0.0)).where(
            HourAllocation.product == product,
            HourAllocation.feature_id == feature_id,
            HourAllocation.version == version,
        )
    ) or 0
    if mv and mv.lifecycle_band == "done":
        return 403, {"ok": False, "error": "settled"}
    expected = personal_expected(db, principal.username, product, feature_id, version)
    if hours > expected + 1e-6:
        return 400, {"ok": False, "error": "exceeds_expected", "expected_hours": expected}
    others = week_total(db, principal.username, week)
    row = db.scalar(
        select(HourAllocation)
        .where(
            HourAllocation.week == week,
            HourAllocation.username == principal.username,
            HourAllocation.product == product,
            HourAllocation.feature_id == feature_id,
            HourAllocation.version == version,
        )
        .with_for_update()
    )
    others -= float(row.hours) if row else 0.0
    if others + hours > hard_cap + 1e-6:
        return 400, {"ok": False, "error": "hard_cap", "total": others, "cap": hard_cap}
    if row:
        if hours == 0:
            db.delete(row)
        else:
            row.hours = hours
    elif hours > 0:
        db.add(
            HourAllocation(
                week=week,
                username=principal.username,
                product=product,
                feature_id=feature_id,
                version=version,
                hours=hours,
            )
        )
    return 200, {"ok": True, "week": week, "my_total": others + hours}


def _hours_on_pairs(
    db: Session, product: str, pairs: list[tuple[str, str]], week: str
) -> tuple[float, float, dict[str, float], dict[str, float]]:
    pairset = set(pairs)
    fids = [fid for fid, _ in pairs] or ["__none__"]
    user_exp: dict[str, float] = {}
    for r in db.scalars(
        select(HourExpectation).where(
            HourExpectation.product == product,
            HourExpectation.feature_id.in_(fids),
        )
    ):
        if (r.feature_id, r.version) not in pairset:
            continue
        val = float(r.expected_hours or 0)
        if val <= 0:
            continue
        user_exp[r.username] = user_exp.get(r.username, 0.0) + val
    invested = 0.0
    week_invested = 0.0
    by_user_map: dict[str, float] = {}
    for r in db.scalars(
        select(HourAllocation).where(
            HourAllocation.product == product,
            HourAllocation.feature_id.in_(fids),
        )
    ):
        if (r.feature_id, r.version) not in pairset:
            continue
        h = float(r.hours or 0)
        if r.week == week:
            week_invested += h
            by_user_map[r.username] = by_user_map.get(r.username, 0.0) + h
        elif week_is_closed(r.week):
            invested += h
    return round(invested, 2), round(week_invested, 2), user_exp, by_user_map


def feature_hours(
    db: Session,
    product: str,
    feature_id: str,
    version: str | None = None,
    principal: Principal | None = None,
    week: str | None = None,
    crypto: ContentCrypto | None = None,
) -> dict:
    close_elapsed_weeks(db)
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    boards = {b.rel_path: b.doc or {} for b in db.scalars(select(Board).where(Board.product == product))}
    ver = version or ((mods[feature_id].current_version if mods.get(feature_id) else None) or "v1.0")
    week = week or iso_week_id()
    invested, week_invested, user_exp, by_user_map = _hours_on_pairs(db, product, [(feature_id, ver)], week)
    expected = round(sum(user_exp.values()), 2) if user_exp else None
    labels = map_db(db)
    by_user = [
        {
            "user": u,
            "name": labels.get(u, u),
            "hours": float(by_user_map.get(u, 0) or 0),
            "expected_hours": user_exp.get(u),
        }
        for u in sorted(set(by_user_map) | set(user_exp))
        if (by_user_map.get(u, 0) or 0) > 0 or (user_exp.get(u) or 0) > 0
    ]
    mv = db.scalar(
        select(ModuleVersion).where(
            ModuleVersion.product == product,
            ModuleVersion.module_id == feature_id,
            ModuleVersion.semver == ver,
        )
    )
    band = mv.lifecycle_band if mv else None
    desc_ids = _nested_descendant_ids(feature_id, mods, boards)
    hours_mode = "nested_aggregate" if desc_ids else "direct"
    latest = (mods[feature_id].current_version if mods.get(feature_id) else None) or "v1.0"
    settled = (band or "").lower() == "done" or ver != latest
    if settled:
        expected = None
        by_user = [
            {**u, "expected_hours": None}
            for u in by_user
            if (u.get("hours") or 0) > 0
        ]
    by_child: list[dict] = []
    if desc_ids:
        if crypto is None:
            from app.settings import get_settings

            crypto = ContentCrypto(get_settings().topo_content_key)
        child_vers = {
            (r.module_id, r.semver): r.lifecycle_band
            for r in db.scalars(
                select(ModuleVersion).where(
                    ModuleVersion.product == product,
                    ModuleVersion.module_id.in_(desc_ids),
                )
            )
        }
        for cid in desc_ids:
            cm = mods.get(cid)
            if not cm or cm.kind in HOURS_CHILD_SKIP_KINDS:
                continue
            if principal and not can_browse_module(principal, module_view(cm)):
                continue
            cver = (cm.current_version if cm else None) or "v1.0"
            _c_inv, c_week, c_exp_map, _c_users = _hours_on_pairs(
                db, product, [(cid, cver)], week
            )
            if (c_week or 0) <= 0:
                continue
            title = cid
            if cm.title_blob:
                title = crypto.decrypt_text(cm.title_blob, aad=f"module.title:{product}:{cm.id}")
            c_exp = round(sum(c_exp_map.values()), 2) if c_exp_map else None
            c_band = child_vers.get((cid, cver))
            if (c_band or "").lower() == "done":
                c_exp = None
            by_child.append(
                {
                    "id": cid,
                    "title": title,
                    "hours": c_week,
                    "expected_hours": c_exp,
                    "invested": _c_inv,
                    "band": c_band,
                }
            )
        by_child.sort(key=lambda x: x["title"])
    kid_sum = sum(float(c.get("hours") or 0) for c in by_child)
    invested_total = round(invested + week_invested + kid_sum, 2)
    my_week = by_user_map.get(principal.username, 0) if principal else sum(by_user_map.values())
    return {
        "feature_id": feature_id,
        "version": version,
        "invested": invested,
        "invested_total": invested_total,
        "expected_hours": expected,
        "remaining_hours": expected,
        "band": band,
        "settled": settled,
        "hours_mode": hours_mode,
        "week": week,
        "my_week_hours": my_week,
        "week_invested": week_invested,
        "by_user": by_user,
        "by_child": by_child,
        "can_register": bool(
            principal
            and can_log_hours(principal, product)
            and band != "done"
            and not settled
        ),
    }


def hours_current_ratio(week_hours: float, remaining: float) -> float:
    w = max(0.0, float(week_hours or 0))
    r = float(remaining or 0)
    if r <= 0:
        return 1.0 if w > 0 else 0.0
    return max(0.0, min(1.0, w / r))


def hours_progress_for_groups(db: Session, product: str, groups: dict[str, list[str]]) -> dict[str, dict[str, float]]:
    close_elapsed_weeks(db)
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    boards = {b.rel_path: b.doc or {} for b in db.scalars(select(Board).where(Board.product == product))}
    group_pairs: dict[str, set[tuple[str, str]]] = {}
    all_pairs: set[tuple[str, str]] = set()
    for key, fids in groups.items():
        pairs: set[tuple[str, str]] = set()
        for fid in fids:
            ver = ((mods[fid].current_version if mods.get(fid) else None) or "v1.0")
            pairs.add((fid, ver))
            for kid in _nested_descendant_ids(fid, mods, boards):
                kver = ((mods[kid].current_version if mods.get(kid) else None) or "v1.0")
                pairs.add((kid, kver))
        group_pairs[key] = pairs
        all_pairs |= pairs
    if not all_pairs:
        return {k: {"ratio": 0.0, "week": 0.0} for k in groups}
    fids = list({fid for fid, _ in all_pairs})
    week = iso_week_id()
    week_h: dict[tuple[str, str], float] = {}
    rem_h: dict[tuple[str, str], float] = {}
    for r in db.scalars(
        select(HourAllocation).where(HourAllocation.product == product, HourAllocation.feature_id.in_(fids), HourAllocation.week == week)
    ):
        key = (r.feature_id, r.version)
        if key not in all_pairs:
            continue
        week_h[key] = week_h.get(key, 0.0) + float(r.hours or 0)
    for r in db.scalars(select(HourExpectation).where(HourExpectation.product == product, HourExpectation.feature_id.in_(fids))):
        key = (r.feature_id, r.version)
        if key not in all_pairs:
            continue
        rem_h[key] = rem_h.get(key, 0.0) + float(r.expected_hours or 0)
    out: dict[str, dict[str, float]] = {}
    for gid, pairs in group_pairs.items():
        w = sum(week_h.get(p, 0.0) for p in pairs)
        r = sum(rem_h.get(p, 0.0) for p in pairs)
        out[gid] = {"ratio": hours_current_ratio(w, r), "week": round(w, 2)}
    return out


def nested_aggregate(db: Session, product: str, child_ids: list[str]) -> dict:
    invested = float(
        db.scalar(
            select(func.coalesce(func.sum(HourAllocation.hours), 0.0)).where(
                HourAllocation.product == product,
                HourAllocation.feature_id.in_(child_ids or ["__none__"]),
            )
        )
        or 0
    )
    expected = float(
        db.scalar(
            select(func.coalesce(func.sum(ModuleVersion.expected_hours), 0.0)).where(
                ModuleVersion.product == product,
                ModuleVersion.module_id.in_(child_ids or ["__none__"]),
            )
        )
        or 0
    )
    return {
        "invested": invested,
        "expected_hours": expected,
        "hours_mode": "nested_aggregate",
    }


def cover_child_map(db: Session, product: str) -> dict[str, tuple[str, ...]]:
    children: dict[str, list[str]] = {}
    boards = {
        b.rel_path: b.doc or {}
        for b in db.scalars(select(Board).where(Board.product == product))
    }
    mods = list(db.scalars(select(Module).where(Module.product == product)))

    def add(parent: str, kids: list[str]) -> None:
        parent = (parent or "").strip()
        if not parent:
            return
        bucket = children.setdefault(parent, [])
        for k in kids:
            kid = (k or "").strip()
            if kid and kid not in bucket:
                bucket.append(kid)

    for m in mods:
        if m.parent_id:
            add(m.parent_id, [m.id])
        if m.nested_board:
            doc = boards.get(m.nested_board) or {}
            add(m.id, [n.get("id") or "" for n in (doc.get("nodes") or []) if isinstance(n, dict)])
    for doc in boards.values():
        if not isinstance(doc, dict):
            continue
        for g in doc.get("groups") or []:
            if isinstance(g, dict) and g.get("id"):
                add(g["id"], [x for x in (g.get("members") or []) if isinstance(x, str)])
        for n in doc.get("nodes") or []:
            if not isinstance(n, dict) or not n.get("id") or not n.get("nested_board"):
                continue
            nested = boards.get(n["nested_board"]) or {}
            add(n["id"], [x.get("id") or "" for x in (nested.get("nodes") or []) if isinstance(x, dict)])
    return {k: tuple(v) for k, v in children.items()}


def expand_domain_grant_ids(db: Session, product: str, domain_id: str) -> set[str]:
    from app.services.topology import domain_boards

    rel = next((d["rel_path"] for d in domain_boards(product) if d["id"] == domain_id), None)
    if not rel:
        return set()
    mods = list(db.scalars(select(Module).where(Module.product == product)))
    by_board: dict[str, list[Module]] = {}
    for m in mods:
        by_board.setdefault(m.board_rel or "", []).append(m)
    out: set[str] = set()
    stack = [rel]
    seen: set[str] = set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for m in by_board.get(cur, []):
            out.add(m.id)
            if m.nested_board:
                stack.append(m.nested_board)
    return out


def expand_module_grant_ids(db: Session, product: str, seeds: set[str]) -> set[str]:
    cmap = cover_child_map(db, product)
    out = {s for s in seeds if s}
    stack = list(out)
    while stack:
        mid = stack.pop()
        for kid in cmap.get(mid) or ():
            if kid not in out:
                out.add(kid)
                stack.append(kid)
    return out


def cover_ancestor_map(child_map: dict[str, tuple[str, ...]]) -> dict[str, frozenset[str]]:
    parent_of: dict[str, list[str]] = {}
    for p, kids in child_map.items():
        for c in kids:
            parent_of.setdefault(c, []).append(p)
    anc: dict[str, frozenset[str]] = {}

    def walk(nid: str, stack: frozenset[str]) -> frozenset[str]:
        if nid in anc:
            return anc[nid]
        found: set[str] = set()
        for p in parent_of.get(nid, ()):
            if p in stack:
                continue
            found.add(p)
            found |= walk(p, stack | {p})
        anc[nid] = frozenset(found)
        return anc[nid]

    for nid in set(parent_of) | set(child_map):
        walk(nid, frozenset())
    return anc


def dedupe_cover_items(
    items: list[tuple[str, str]],
    anc_map: dict[str, frozenset[str]],
) -> list[tuple[str, str]]:
    cover_set = {(mid or "").strip() for mid, _ in items if (mid or "").strip()}
    redundant = {mid for mid in cover_set if (anc_map.get(mid) or frozenset()) & cover_set}
    return [(mid, ver) for mid, ver in items if (mid or "").strip() and mid not in redundant]


def _leaf_hours(
    db: Session,
    product: str,
    mid: str,
    ver: str | None,
    *,
    week: str | None = None,
    closed_only: bool = False,
) -> float:
    q = select(HourAllocation.week, HourAllocation.hours).where(
        HourAllocation.product == product,
        HourAllocation.feature_id == mid,
    )
    if ver:
        q = q.where(HourAllocation.version == ver)
    if week:
        q = q.where(HourAllocation.week == week)
    total = 0.0
    for wid, hours in db.execute(q):
        if closed_only and not week_is_closed(wid):
            continue
        total += float(hours or 0)
    return total


def cover_invested(
    db: Session,
    product: str,
    mid: str,
    ver: str | None,
    child_map: dict[str, tuple[str, ...]],
    *,
    week: str | None = None,
    closed_only: bool = False,
    memo: dict | None = None,
    pins: dict[str, str] | None = None,
) -> float:
    """Host own hours plus nested descendants. Nested hosts still count their own log.

    A descendant that has its own cover pin is read at that version. Otherwise it
    keeps the version inherited from the host.
    """
    memo = memo if memo is not None else {}
    pins = pins or {}
    pin_key = tuple(sorted(pins.items()))
    key = (mid, ver, week, closed_only, pin_key)
    if key in memo:
        return memo[key]
    total = _leaf_hours(db, product, mid, ver, week=week, closed_only=closed_only)
    for cid in child_map.get(mid) or ():
        cver = pins[cid] if cid in pins else ver
        total += cover_invested(
            db,
            product,
            cid,
            cver,
            child_map,
            week=week,
            closed_only=closed_only,
            memo=memo,
            pins=pins,
        )
    memo[key] = round(float(total or 0), 2)
    return memo[key]


def descendant_ids(mid: str, child_map: dict[str, tuple[str, ...]]) -> list[str]:
    kids = child_map.get(mid) or ()
    if not kids:
        return []
    out: list[str] = []
    for cid in kids:
        sub = descendant_ids(cid, child_map)
        if sub:
            out.extend(sub)
        else:
            out.append(cid)
    return out


def cover_scope_ids(mid: str, child_map: dict[str, tuple[str, ...]]) -> list[str]:
    """Cover pin plus every nested descendant (host own hours included)."""
    out: list[str] = []
    seen: set[str] = set()
    stack = [mid]
    while stack:
        nid = stack.pop()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(nid)
        stack.extend(reversed(child_map.get(nid) or ()))
    return out


def _rollup_versions(
    scope: list[tuple[str, str]],
    child_map: dict[str, tuple[str, ...]],
    pins: dict[str, str],
) -> list[tuple[str, str]]:
    """Each node in a rolled-up cover, at its own pin when the release has one."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for mid, ver in scope:
        for fid in cover_scope_ids(mid, child_map):
            fver = pins[fid] if fid in pins else ver
            pair = (fid, fver or "")
            if not pair[0] or pair in seen:
                continue
            seen.add(pair)
            out.append((fid, fver))
    return out


def changelog_hours(db: Session, product: str, pairs: list[tuple[str, str]], week: str | None = None) -> dict:
    """Product-page hours: changelog scope, nest rollup including host own hours."""
    close_elapsed_weeks(db)
    week = week or iso_week_id()
    child_map = cover_child_map(db, product)
    anc_map = cover_ancestor_map(child_map)
    pins = {mid: ver for mid, ver in pairs if mid}
    scope = dedupe_cover_items([(a, b) for a, b in pairs if a], anc_map)
    memo: dict = {}
    week_map = {
        mid: cover_invested(db, product, mid, ver, child_map, week=week, memo=memo, pins=pins)
        for mid, ver in scope
    }
    invested = round(
        sum(
            cover_invested(db, product, mid, ver, child_map, closed_only=True, memo=memo, pins=pins)
            for mid, ver in scope
        ),
        2,
    )
    active = [(mid, ver) for mid, ver in scope if week_map.get(mid, 0) > 0]
    feat_vers = _rollup_versions(scope, child_map, pins)
    pairset = set(feat_vers)
    fids = [fid for fid, _ in feat_vers] or ["__none__"]
    expected_total = 0.0
    has_expected = False
    by_user: dict[str, float] = {}
    cover_ids: list[str] = []
    for r in db.scalars(
        select(HourExpectation).where(
            HourExpectation.product == product,
            HourExpectation.feature_id.in_(fids),
        )
    ):
        if (r.feature_id, r.version) not in pairset:
            continue
        val = float(r.expected_hours or 0)
        if val <= 0:
            continue
        has_expected = True
        expected_total += val
    active_ids = {mid for mid, _ in active}
    for mid, _ver in scope:
        if mid not in active_ids:
            continue
        cover_ids.append(mid)
    rows = db.scalars(
        select(HourAllocation).where(
            HourAllocation.product == product,
            HourAllocation.week == week,
            HourAllocation.feature_id.in_(fids),
        )
    )
    for r in rows:
        if (r.feature_id, r.version) not in pairset or r.hours <= 0:
            continue
        by_user[r.username] = by_user.get(r.username, 0.0) + r.hours
    remaining = round(expected_total, 2) if has_expected else None
    week_invested = round(sum(week_map.get(mid, 0) for mid, _ in active), 2)
    labels = map_db(db)
    return {
        "invested": invested,
        "invested_total": invested,
        "expected_hours": round(expected_total, 2) if has_expected else None,
        "remaining_hours": remaining,
        "week": week,
        "week_invested": week_invested,
        "by_user": [
            {"user": u, "name": labels.get(u, u), "hours": round(h, 2)}
            for u, h in sorted(by_user.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "cover_ids": cover_ids,
        "changelog_cover_ids": [mid for mid, _ in scope],
        "hours_mode": "covers_aggregate",
        "week_map": week_map,
    }


def covers_aggregate(db: Session, product: str, pairs: list[tuple[str, str]]) -> dict:
    out = changelog_hours(db, product, pairs)
    out.pop("week_map", None)
    return out


def monday(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())


def snake_duration_sec(expected: float | None, invested: float) -> float | None:
    """Weekly invested/expected → bead loop seconds. None = no beads (0 invested)."""
    inv = max(0.0, float(invested or 0))
    if inv <= 0:
        return None
    try:
        exp = float(expected) if expected is not None else 0.0
    except (TypeError, ValueError):
        exp = 0.0
    if exp <= 0:
        exp = max(inv, 8.0)
    ratio = inv / exp
    dur = 8.0 - min(ratio, 2.0) * (8.0 - 1.4) / 2.0
    dur = max(1.4, min(8.0, dur)) / 3.0 / 1.7
    return round(dur, 2)


def _board_member_ids(doc: dict | None) -> list[str]:
    out: list[str] = []
    if not isinstance(doc, dict):
        return out
    for n in doc.get("nodes") or []:
        if isinstance(n, dict) and n.get("id"):
            out.append(str(n["id"]))
    for g in doc.get("groups") or []:
        if not isinstance(g, dict):
            continue
        for mid in g.get("members") or []:
            if isinstance(mid, str) and mid:
                out.append(mid)
    return out


def _nested_descendant_ids(
    parent_id: str,
    mods: dict[str, Module],
    boards: dict[str, dict],
) -> list[str]:
    parent = mods.get(parent_id)
    if not parent or not parent.nested_board:
        return []
    seen: set[str] = set()
    out: list[str] = []
    stack = [parent.nested_board]
    board_seen: set[str] = set()
    while stack:
        rel = stack.pop()
        if rel in board_seen:
            continue
        board_seen.add(rel)
        for nid in _board_member_ids(boards.get(rel)):
            if not nid or nid == parent_id or nid in seen:
                continue
            seen.add(nid)
            out.append(nid)
            child = mods.get(nid)
            if child and child.nested_board:
                stack.append(child.nested_board)
    return out


def _scale_hours(
    remaining: float | None,
    expected: float | None,
) -> float | None:
    return remaining if remaining is not None else expected


def snake_summary(
    db: Session,
    principal: Principal,
    product: str,
    week: str,
) -> dict:
    from app.services.releases import tag_bindings, tag_for_anchor

    close_elapsed_weeks(db)
    mods = {m.id: m for m in db.scalars(select(Module).where(Module.product == product))}
    vers = {
        (r.module_id, r.semver): r
        for r in db.scalars(select(ModuleVersion).where(ModuleVersion.product == product))
    }
    boards = {
        b.rel_path: b.doc or {}
        for b in db.scalars(select(Board).where(Board.product == product))
    }
    week_by_feat: dict[str, float] = {}
    week_by_pair: dict[tuple[str, str], float] = {}
    for r in db.scalars(
        select(HourAllocation).where(HourAllocation.product == product, HourAllocation.week == week)
    ):
        week_by_feat[r.feature_id] = week_by_feat.get(r.feature_id, 0.0) + r.hours
        key = (r.feature_id, r.version)
        week_by_pair[key] = week_by_pair.get(key, 0.0) + r.hours
    invested_pair: dict[tuple[str, str], float] = {}
    invested_feat: dict[str, float] = {}
    for fid, ver, total in db.execute(
        select(
            HourAllocation.feature_id,
            HourAllocation.version,
            func.coalesce(func.sum(HourAllocation.hours), 0.0),
        )
        .where(HourAllocation.product == product)
        .group_by(HourAllocation.feature_id, HourAllocation.version)
    ):
        invested_pair[(fid, ver)] = float(total)
        invested_feat[fid] = invested_feat.get(fid, 0.0) + float(total)
    bindings = tag_bindings(db, product)
    release_ids = {
        r.id for r in db.scalars(select(ProductRelease).where(ProductRelease.product == product))
    }
    covers_by_rel: dict[str, list[tuple[str, str]]] = {}
    if release_ids:
        for c in db.scalars(select(ReleaseCover).where(ReleaseCover.release_id.in_(release_ids))):
            covers_by_rel.setdefault(c.release_id, []).append((c.module_id, c.module_version))
    tags = {t.tag: t.release_id for t in db.scalars(select(ProductTag).where(ProductTag.product == product))}

    def leaf_week_remaining(fids: list[str], pairs: list[tuple[str, str]] | None = None) -> tuple[float, float | None, float | None]:
        if pairs is not None:
            inv = sum(week_by_pair.get(p, 0.0) for p in pairs)
            expected = 0.0
            remaining_acc = 0.0
            has_exp = False
            for mid, ver in pairs:
                mv = vers.get((mid, ver))
                eh = mv.expected_hours if mv else None
                tot = invested_pair.get((mid, ver), 0.0)
                if eh is not None:
                    has_exp = True
                    expected += float(eh)
                    remaining_acc += max(0.0, float(eh) - tot)
            return inv, (expected if has_exp else None), (round(remaining_acc, 2) if has_exp else None)
        expected = 0.0
        remaining_acc = 0.0
        has_exp = False
        for fid in fids:
            m = mods.get(fid)
            if not m:
                continue
            ver = m.current_version or "v1.0"
            mv = vers.get((fid, ver))
            eh = mv.expected_hours if mv else None
            tot = invested_pair.get((fid, ver), 0.0)
            if eh is not None:
                has_exp = True
                expected += float(eh)
                remaining_acc += max(0.0, float(eh) - tot)
        inv = sum(
            week_by_pair.get((fid, (mods[fid].current_version if mods.get(fid) else None) or "v1.0"), 0.0)
            for fid in fids
        )
        return inv, (expected if has_exp else None), (round(remaining_acc, 2) if has_exp else None)

    features: dict[str, dict] = {}
    for m in mods.values():
        if m.kind in ("product_hub", "metrics"):
            continue
        if not can_browse_module(principal, module_view(m)):
            continue
        fid = m.id
        remaining = None
        expected = None
        tag = tag_for_anchor(fid)
        prod_rid = None
        if tag:
            prod_rid = (bindings.get(tag) or {}).get("id") or tags.get(tag)
        elif fid in release_ids:
            prod_rid = fid
        if prod_rid:
            pairs = covers_by_rel.get(prod_rid) or []
            inv, expected, remaining = leaf_week_remaining([], pairs)
        else:
            nest_ids = _nested_descendant_ids(fid, mods, boards)
            if nest_ids:
                inv, expected, remaining = leaf_week_remaining([fid] + nest_ids)
            else:
                inv = week_by_pair.get((fid, m.current_version or "v1.0"), 0.0)
                ver = m.current_version or "v1.0"
                mv = vers.get((fid, ver))
                expected = mv.expected_hours if mv else None
                tot = invested_feat.get(fid, 0.0)
                remaining = None if expected is None else max(0.0, round(float(expected) - tot, 2))
        scale = _scale_hours(remaining, expected)
        features[fid] = {
            "invested": inv,
            "expected": expected,
            "remaining": remaining,
            "duration": snake_duration_sec(scale, inv),
        }
    return {"ok": True, "week": week, "product": product, "features": features}
