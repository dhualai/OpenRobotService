"""转派 / 重新派单指标。

不猜原因、不调 LLM。重新派单不写 kind=misassign，通道与弹窗错派分开。

同一张 operation_type=reassign 日志里分通道：
- signal：转派弹窗三个类型（派错了 / 阶段转派 / 其它）
- redispatch：让 AI 再派一次（提单人、处理人、管理员都能点）
- unlabeled：继续处理改人、旧转派，没有 kind
- skipped：未标转派审核点跳过

审核为「算不准确」后计入不准确率，并进入派单学习（压原处理人 ×0.7）。
测试期要人工标「算不准确 / 测试不算」才进率；测试不算不学。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy import bindparam, text

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

SIGNAL_KINDS = ("misassign", "stage", "other")
REDISPATCH_VERDICTS = ("inaccurate", "skipped")
CHANNELS = ("signal", "redispatch", "unlabeled", "skipped")
WEEKLY_KEEP = 16  # 趋势图最多保留最近多少周


def _as_dict(raw) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _norm_kind(raw) -> str:
    k = (raw or "").strip().lower()
    return k if k in SIGNAL_KINDS else ""


def is_redispatch(detail: dict, description: str = "") -> bool:
    d = detail or {}
    if (d.get("channel") or "") == "redispatch":
        return True
    if str(d.get("preferred_assignee") or "").strip():
        return True
    desc = description or ""
    return "重新派单" in desc and not str(d.get("new_assignee") or "").strip()


def redispatch_verdict(detail: dict) -> str:
    v = str((detail or {}).get("redispatch_verdict") or "").strip().lower()
    return v if v in REDISPATCH_VERDICTS else ""


def correction_pair(detail: dict, description: str = "") -> tuple | None:
    """可学习的纠错对：(原处理人 A, 接手/倾向人 B, 原因)。B 可空，仍压 A。"""
    d = detail or {}
    if _norm_kind(d.get("kind")) == "misassign":
        a = str(d.get("from_assignee") or "").strip()
        b = str(d.get("new_assignee") or "").strip()
        if a and a != b:
            return a, b, str(d.get("reason") or "")[:500]
        return None
    if is_redispatch(d, description) and redispatch_verdict(d) == "inaccurate":
        a = str(d.get("from_assignee") or d.get("prev_assignee") or "").strip()
        b = str(d.get("preferred_assignee") or d.get("new_assignee") or "").strip()
        if a and a != b:
            return a, b, str(d.get("remark") or d.get("reason") or "")[:500]
        return None
    return None


def event_channel(detail: dict, description: str = "") -> str:
    """重新派单始终单独通道；弹窗 kind 是信号；其余未标注 / 跳过。"""
    if is_redispatch(detail, description):
        return "redispatch"
    if _norm_kind((detail or {}).get("kind")):
        return "signal"
    if (detail or {}).get("kind_source") == "skipped":
        return "skipped"
    return "unlabeled"


def load_correction_pairs(ticket_ids) -> list:
    """相似单 ticket_id 反查可学习纠错对：[{from_id, to_id, reason}, ...]。"""
    ids = []
    for raw in ticket_ids or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return []
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    engine = _get_engine()
    with engine.connect() as db:
        rows = db.execute(
            text(
                "SELECT task_id, detail, description FROM task_operation_logs "
                "WHERE operation_type = 'reassign' AND task_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).mappings().all()
    out = []
    for r in rows:
        pair = correction_pair(_as_dict(r.get("detail")), r.get("description") or "")
        if not pair:
            continue
        a, b, reason = pair
        out.append({"from_id": a, "to_id": b, "reason": reason})
    return out


def aggregate_events(
    events: Iterable[dict],
    *,
    ai_assign_total: int,
    ai_assign_tickets: int,
) -> dict:
    """弹窗错派率只拿有 kind 的转派。重新派单另计，审核为不准确后并入「不准确」。"""
    by_kind = {k: 0 for k in SIGNAL_KINDS}
    by_channel = {c: 0 for c in CHANNELS}
    by_redispatch = {"pending": 0, "inaccurate": 0, "skipped": 0}
    misassign_tickets = set()
    signal_tickets = set()
    inaccurate_tickets = set()
    redispatch_tickets = set()
    for ev in events:
        detail = ev.get("detail") or {}
        ch = ev.get("channel") or event_channel(detail, ev.get("description") or "")
        if ch not in by_channel:
            ch = "unlabeled"
        by_channel[ch] += 1
        tid = ev.get("task_id")
        if ch == "redispatch":
            verdict = ev.get("redispatch_verdict") or redispatch_verdict(detail)
            if verdict not in REDISPATCH_VERDICTS:
                verdict = "pending"
            by_redispatch[verdict] += 1
            if tid is not None:
                redispatch_tickets.add(tid)
                if verdict == "inaccurate":
                    inaccurate_tickets.add(tid)
            continue
        if ch != "signal":
            continue
        kind = _norm_kind(ev.get("kind") or detail.get("kind"))
        if not kind:
            continue
        by_kind[kind] += 1
        if tid is not None:
            signal_tickets.add(tid)
            if kind == "misassign":
                misassign_tickets.add(tid)
                inaccurate_tickets.add(tid)
    signal_total = by_channel["signal"]
    misassign = by_kind["misassign"]
    redisp_inacc = by_redispatch["inaccurate"]
    redisp_reviewed = redisp_inacc + by_redispatch["skipped"]
    inaccurate_events = misassign + redisp_inacc
    return {
        "signal_total": signal_total,
        "signal_tickets": len(signal_tickets),
        "redispatch_total": by_channel["redispatch"],
        "redispatch_tickets": len(redispatch_tickets),
        "redispatch_pending": by_redispatch["pending"],
        "redispatch_inaccurate": redisp_inacc,
        "redispatch_skipped": by_redispatch["skipped"],
        "unlabeled_total": by_channel["unlabeled"],
        "skipped_total": by_channel["skipped"],
        "reassign_log_total": sum(by_channel.values()),
        "ai_assign_total": int(ai_assign_total or 0),
        "ai_assign_tickets": int(ai_assign_tickets or 0),
        "by_kind": by_kind,
        "misassign_events": misassign,
        "misassign_tickets": len(misassign_tickets),
        "inaccurate_events": inaccurate_events,
        "inaccurate_tickets": len(inaccurate_tickets),
        "misassign_rate_of_signal": round(misassign / signal_total, 4) if signal_total else None,
        "misassign_rate_of_ai_assign": (
            round(misassign / ai_assign_total, 4) if ai_assign_total else None
        ),
        "ticket_misassign_rate_of_ai": (
            round(len(misassign_tickets) / ai_assign_tickets, 4) if ai_assign_tickets else None
        ),
        "redispatch_inaccurate_rate_of_reviewed": (
            round(redisp_inacc / redisp_reviewed, 4) if redisp_reviewed else None
        ),
        "inaccurate_rate_of_ai_assign": (
            round(inaccurate_events / ai_assign_total, 4) if ai_assign_total else None
        ),
        "ticket_inaccurate_rate_of_ai": (
            round(len(inaccurate_tickets) / ai_assign_tickets, 4) if ai_assign_tickets else None
        ),
        # 兼容上一版前端字段名
        "reassign_total": signal_total,
        "reassign_tickets": len(signal_tickets),
        "classified": signal_total,
        "misassign_rate_of_reassign": round(misassign / signal_total, 4) if signal_total else None,
        "by_source": {"user": signal_total, "heuristic": 0, "llm": 0, "unknown": by_channel["unlabeled"]},
    }


def _parse_created_at(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso_week_meta(dt: datetime) -> Tuple[str, str, str]:
    """返回 (week_key, label, week_start_iso)。周一为一周起点。"""
    monday = dt.date() - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    iso = monday.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    label = f"{monday.month}/{monday.day}–{sunday.month}/{sunday.day}"
    return week_key, label, monday.isoformat()


def build_ticket_lists(events: Iterable[dict]) -> Dict[str, List[dict]]:
    """指标可下钻：错派 / 重派不准确 / 合并不准确 / 有类型转派 对应工单清单。

    同一工单多次命中只留最新一条（events 已按时间倒序时自然取到）。
    """
    buckets = {
        "misassign": {},
        "redispatch_inaccurate": {},
        "inaccurate": {},
        "signal": {},
    }
    for ev in events:
        tid = ev.get("task_id")
        if tid is None:
            continue
        tid = int(tid)
        ch = ev.get("channel") or ""
        kind = _norm_kind(ev.get("kind") or (ev.get("detail") or {}).get("kind"))
        verdict = ev.get("redispatch_verdict") or redispatch_verdict(ev.get("detail") or {})
        row = {
            "task_id": tid,
            "title": ev.get("title") or "",
            "kind": kind or "",
            "channel": ch,
            "created_at": ev.get("created_at") or "",
            "reason": (ev.get("reason") or "")[:200],
        }
        if ch == "signal" and kind:
            buckets["signal"].setdefault(tid, row)
            if kind == "misassign":
                buckets["misassign"].setdefault(tid, {**row, "tag": "派错了"})
                buckets["inaccurate"].setdefault(tid, {**row, "tag": "派错了"})
        elif ch == "redispatch" and verdict == "inaccurate":
            tagged = {**row, "tag": "重派不准确", "kind": "inaccurate"}
            buckets["redispatch_inaccurate"].setdefault(tid, tagged)
            buckets["inaccurate"].setdefault(tid, tagged)

    def _sorted(d: dict) -> List[dict]:
        return sorted(d.values(), key=lambda x: str(x.get("created_at") or ""), reverse=True)

    return {k: _sorted(v) for k, v in buckets.items()}


def build_weekly_metrics(
    events: List[dict],
    ai_rows: List[dict],
    *,
    keep: int = WEEKLY_KEEP,
) -> List[dict]:
    """按自然周（周一～周日）汇总与总览同口径的指标，供趋势图。"""
    week_events: Dict[str, List[dict]] = defaultdict(list)
    week_meta: Dict[str, Tuple[str, str]] = {}
    for ev in events:
        dt = _parse_created_at(ev.get("created_at"))
        if not dt:
            continue
        key, label, start = _iso_week_meta(dt)
        week_meta[key] = (label, start)
        week_events[key].append(ev)

    week_ai: Dict[str, List[dict]] = defaultdict(list)
    for row in ai_rows:
        dt = _parse_created_at(row.get("created_at"))
        if not dt:
            continue
        key, label, start = _iso_week_meta(dt)
        week_meta[key] = (label, start)
        week_ai[key].append(row)

    keys = sorted(week_meta.keys(), key=lambda k: week_meta[k][1])
    if keep > 0:
        keys = keys[-keep:]
    out = []
    for key in keys:
        label, start = week_meta[key]
        ai_list = week_ai.get(key) or []
        ai_total = len(ai_list)
        ai_tickets = len({int(r["task_id"]) for r in ai_list if r.get("task_id") is not None})
        metrics = aggregate_events(
            week_events.get(key) or [],
            ai_assign_total=ai_total,
            ai_assign_tickets=ai_tickets,
        )
        out.append({
            "week": key,
            "label": label,
            "week_start": start,
            "metrics": metrics,
        })
    return out


def _load_rows() -> Tuple[List[dict], List[dict]]:
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    engine = _get_engine()
    with engine.connect() as db:
        logs = db.execute(text(
            "SELECT l.id, l.task_id, l.detail, l.description, l.created_at, "
            "l.operator, l.operator_name, t.title "
            "FROM task_operation_logs l "
            "JOIN tasks t ON t.id = l.task_id "
            "WHERE l.operation_type = 'reassign' "
            "ORDER BY l.created_at DESC"
        )).mappings().all()
        ai_logs = db.execute(text(
            "SELECT task_id, created_at FROM task_operation_logs "
            "WHERE operation_type = 'ai_assign' "
            "ORDER BY created_at DESC"
        )).mappings().all()

    events = []
    for r in logs:
        detail = _as_dict(r.get("detail"))
        created = r.get("created_at")
        channel = event_channel(detail, r.get("description") or "")
        events.append({
            "id": int(r["id"]),
            "task_id": int(r["task_id"]),
            "title": r.get("title") or "",
            "kind": _norm_kind(detail.get("kind")),
            "channel": channel,
            "redispatch_verdict": redispatch_verdict(detail),
            "reason": (detail.get("reason") or detail.get("remark") or "")[:500],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
            "description": r.get("description") or "",
            "operator": r.get("operator") or "",
            "operator_name": r.get("operator_name") or r.get("operator") or "",
            "detail": detail,
        })
    ai_rows = []
    for r in ai_logs:
        created = r.get("created_at")
        ai_rows.append({
            "task_id": int(r["task_id"]) if r.get("task_id") is not None else None,
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
        })
    return events, ai_rows


def _sample_events(events: List[dict], limit: int = 40) -> List[dict]:
    mis = [e for e in events if e.get("channel") == "signal" and e.get("kind") == "misassign"]
    unlabeled = [e for e in events if e.get("channel") == "unlabeled"]
    rest = [e for e in events if e not in mis and e not in unlabeled]
    out = (mis + unlabeled + rest)[:limit]
    return [{
        "id": e["id"],
        "task_id": e["task_id"],
        "title": e.get("title") or "",
        "kind": e.get("kind") or "",
        "source": "user" if e.get("channel") == "signal" else e.get("channel"),
        "channel": e.get("channel"),
        "confidence": 1.0 if e.get("channel") == "signal" else 0.0,
        "reason": e.get("reason") or "",
        "created_at": e.get("created_at") or "",
    } for e in out]


def _name_by_id() -> dict:
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import load_engineers
        return {e.id: e.name for e in (load_engineers() or []) if e.id}
    except Exception as e:
        logger.warning(f"[转派统计] 读工程师姓名失败: {e}")
        return {}


def _ts_key(v) -> str:
    s = str(v or "").replace(" ", "T")
    return s[:19]


def parse_reason_comment(content: str) -> str:
    """从工单评论抽出转派原因。旧数据原因必填，只写在评论里，日志 detail 没有。"""
    text = (content or "").strip()
    for mark in ("重新指派原因：", "重新指派原因:", "转派原因：", "转派原因:"):
        idx = text.find(mark)
        if idx >= 0:
            return text[idx + len(mark):].strip()[:500]
    return ""


def _apply_comment_reasons(hops: List[dict], comments: List[dict]) -> None:
    """按时间把原因评论贴到还没有 reason 的 hop 上。"""
    pending = [h for h in hops if not str(h.get("reason") or "").strip()]
    for c in comments:
        reason = str(c.get("reason") or "").strip()
        if not reason or not pending:
            continue
        cts = _ts_key(c.get("created_at"))
        target = None
        for h in pending:
            hop_ts = _ts_key(h.get("created_at"))
            if hop_ts and cts and cts < hop_ts:
                continue
            target = h
        if target is None:
            target = pending[0]
        target["reason"] = reason[:500]
        pending.remove(target)


def _load_reason_comments(task_ids: List[int]) -> dict:
    ids = []
    for raw in task_ids or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    engine = _get_engine()
    with engine.connect() as db:
        rows = db.execute(
            text(
                "SELECT task_id, content, created_at FROM task_comments "
                "WHERE task_id IN :ids AND content LIKE :pat "
                "ORDER BY created_at ASC"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids, "pat": "%重新指派原因%"},
        ).mappings().all()
    out: dict = {}
    for r in rows:
        reason = parse_reason_comment(r.get("content") or "")
        if not reason:
            continue
        created = r.get("created_at")
        out.setdefault(int(r["task_id"]), []).append({
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
            "reason": reason,
        })
    return out


def _attach_reasons_from_comments(groups: List[dict]) -> None:
    comments_map = _load_reason_comments([g.get("task_id") for g in groups])
    for g in groups:
        _apply_comment_reasons(g.get("hops") or [], comments_map.get(g.get("task_id")) or [])


def _hop_assignees(detail: dict) -> Tuple[str, str]:
    d = detail or {}
    from_id = str(d.get("from_assignee") or "").strip()
    to_id = str(d.get("new_assignee") or d.get("preferred_assignee") or "").strip()
    return from_id, to_id


def _unlabeled_groups(events: List[dict], names: dict, limit_tickets: int = 80) -> List[dict]:
    """有未标转派的工单整链列出，不按工单折叠成最后一次。"""
    by_ticket: dict = {}
    for e in events:
        tid = e.get("task_id")
        if tid is None:
            continue
        by_ticket.setdefault(tid, []).append(e)

    ticket_order = []
    seen = set()
    for e in events:
        if e.get("channel") != "unlabeled":
            continue
        tid = e.get("task_id")
        if tid in seen:
            continue
        seen.add(tid)
        ticket_order.append(tid)
        if len(ticket_order) >= limit_tickets:
            break

    groups = []
    for tid in ticket_order:
        hops_raw = sorted(by_ticket.get(tid) or [], key=lambda x: x.get("created_at") or "")
        hops = []
        prev_to = ""
        for e in hops_raw:
            from_id, to_id = _hop_assignees(e.get("detail") or {})
            if not from_id and prev_to:
                from_id = prev_to
            hops.append({
                "id": e["id"],
                "created_at": e.get("created_at") or "",
                "from_id": from_id,
                "from_name": names.get(from_id) or from_id or "—",
                "to_id": to_id,
                "to_name": names.get(to_id) or to_id or "—",
                "reason": e.get("reason") or "",
                "description": e.get("description") or "",
                "kind": e.get("kind") or "",
                "channel": e.get("channel") or "",
                "reviewable": e.get("channel") == "unlabeled",
            })
            if to_id:
                prev_to = to_id
        if not hops:
            continue
        groups.append({
            "task_id": tid,
            "title": hops_raw[0].get("title") or "",
            "hops": hops,
        })
    return groups


def _unlabeled_items(events: List[dict], names: dict, limit: int = 100) -> List[dict]:
    """兼容旧前端：未标 hop 扁平列表。同一张单多次未标转派会全部返回。"""
    items = []
    for g in _unlabeled_groups(events, names, limit_tickets=limit):
        for h in g["hops"]:
            if not h.get("reviewable"):
                continue
            items.append({
                "id": h["id"],
                "task_id": g["task_id"],
                "title": g.get("title") or "",
                "reason": h.get("reason") or "",
                "description": h.get("description") or "",
                "created_at": h.get("created_at") or "",
                "from_id": h.get("from_id") or "",
                "from_name": h.get("from_name") or "—",
                "to_id": h.get("to_id") or "",
                "to_name": h.get("to_name") or "—",
            })
    return items


def _redispatch_items(events: List[dict], names: dict, limit: int = 100) -> List[dict]:
    """待审核的重新派单：还没点过算不准确 / 测试不算。"""
    items = []
    for e in events:
        if e.get("channel") != "redispatch":
            continue
        if redispatch_verdict(e.get("detail") or {}) or e.get("redispatch_verdict"):
            continue
        detail = e.get("detail") or {}
        pref = str(detail.get("preferred_assignee") or "").strip()
        items.append({
            "id": e["id"],
            "task_id": e["task_id"],
            "title": e.get("title") or "",
            "reason": e.get("reason") or "",
            "description": e.get("description") or "",
            "created_at": e.get("created_at") or "",
            "operator_name": e.get("operator_name") or e.get("operator") or "—",
            "preferred_id": pref,
            "preferred_name": names.get(pref) or pref or "—",
        })
        if len(items) >= limit:
            break
    return items


def _prev_assignee_before(db, task_id: int, created_at) -> str:
    """审核旧重新派单时补原处理人：取该日志之前最近一轮派单结果。"""
    params = {"tid": int(task_id)}
    sql = (
        "SELECT assigned_id FROM task_dispatch_log WHERE task_id = :tid "
        "AND assigned_id IS NOT NULL AND assigned_id != '' "
    )
    if created_at is not None:
        sql += "AND created_at < :ts "
        params["ts"] = created_at
    sql += "ORDER BY dispatch_round DESC LIMIT 1"
    row = db.execute(text(sql), params).mappings().first()
    return str((row or {}).get("assigned_id") or "").strip()


def review_reassign(log_id: int, kind: str) -> dict:
    """审核未标转派或重新派单。弹窗金标不覆盖；重新派单不写成派错了。"""
    kind = (kind or "").strip().lower()
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    engine = _get_engine()
    with engine.begin() as db:
        row = db.execute(text(
            "SELECT task_id, detail, description, created_at FROM task_operation_logs "
            "WHERE id = :id AND operation_type = 'reassign'"
        ), {"id": int(log_id)}).mappings().first()
        if not row:
            raise ValueError("找不到这条转派记录")
        detail = _as_dict(row.get("detail"))
        channel = event_channel(detail, row.get("description") or "")
        if channel == "redispatch":
            if kind not in REDISPATCH_VERDICTS:
                raise ValueError("重新派单只能标 算不准确 / 测试不算")
            detail["channel"] = "redispatch"
            detail["redispatch_verdict"] = kind
            detail["kind_source"] = "review"
            detail.pop("kind", None)
            if kind == "inaccurate":
                if not str(detail.get("from_assignee") or "").strip():
                    prev = _prev_assignee_before(
                        db, int(row["task_id"]), row.get("created_at"),
                    )
                    if prev:
                        detail["from_assignee"] = prev
                detail["learn_at"] = datetime.now(timezone.utc).isoformat()
        else:
            if kind not in SIGNAL_KINDS and kind != "skipped":
                raise ValueError("类型只能是 派错了 / 阶段转派 / 其它 / 跳过")
            existing = _norm_kind(detail.get("kind"))
            if existing and (detail.get("kind_source") or "") != "review":
                raise ValueError("这条已经是弹窗点选的类型，不能改")
            if kind == "skipped":
                detail.pop("kind", None)
                detail["kind_source"] = "skipped"
            else:
                detail["kind"] = kind
                detail["kind_source"] = "review"
        db.execute(text(
            "UPDATE task_operation_logs SET detail = CAST(:detail AS JSON) WHERE id = :id"
        ), {"id": int(log_id), "detail": json.dumps(detail, ensure_ascii=False)})
    return summarize_reassign_stats()


def summarize_reassign_stats() -> dict:
    events, ai_rows = _load_rows()
    ai_total = len(ai_rows)
    ai_tickets = len({r["task_id"] for r in ai_rows if r.get("task_id") is not None})
    metrics = aggregate_events(events, ai_assign_total=ai_total, ai_assign_tickets=ai_tickets)
    names = _name_by_id()
    groups = _unlabeled_groups(events, names)
    try:
        _attach_reasons_from_comments(groups)
    except Exception as e:
        logger.warning(f"[转派统计] 读取转派原因评论失败: {e}")
    items = []
    for g in groups:
        for h in g.get("hops") or []:
            if not h.get("reviewable"):
                continue
            items.append({
                "id": h["id"],
                "task_id": g["task_id"],
                "title": g.get("title") or "",
                "reason": h.get("reason") or "",
                "description": h.get("description") or "",
                "created_at": h.get("created_at") or "",
                "from_id": h.get("from_id") or "",
                "from_name": h.get("from_name") or "—",
                "to_id": h.get("to_id") or "",
                "to_name": h.get("to_name") or "—",
            })
    return {
        "metrics": metrics,
        "unlabeled": metrics.get("unlabeled_total") or 0,
        "llm": {"called": 0, "failed": 0, "pending": 0},
        "persisted": 0,
        "samples": _sample_events(events),
        "ticket_lists": build_ticket_lists(events),
        "weekly": build_weekly_metrics(events, ai_rows),
        "unlabeled_items": items,
        "unlabeled_groups": groups,
        "redispatch_items": _redispatch_items(events, names),
        "note": (
            "转派弹窗三个类型单独计错派率。"
            "重新派单（提单人/处理人/管理员让 AI 再派）测试期要人工审核："
            "算不准确计入不准确率并进入派单学习（压原处理人 ×0.7），测试不算则跳过。"
            "点开指标可看对应工单；下方按周看趋势。"
        ),
    }


async def classify_reassign_stats(*, use_llm: bool = False, persist: bool = False, force: bool = False) -> dict:
    """保留旧入口；分类已改为只读固定信号，不再调 LLM。"""
    return summarize_reassign_stats()
