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


def redispatch_metric_kind(
    detail: dict,
    description: str = "",
    *,
    channel: str = "",
) -> str:
    """方案 A：重派进指标的类别。

    - skipped：测试不算，剔除
    - preferred_twice：倾向人×2 确认直派，不进不准确、不学习
    - inaccurate：默认进不准确（含自动落库 / 人工标不准确）；非 ×2、非测试不算
    - pending：仅兼容极旧数据（无倾向人、无自动标记时）
    """
    d = detail or {}
    ch = (channel or "").strip() or event_channel(d, description)
    if ch != "redispatch" and not is_redispatch(d, description):
        return ""
    if redispatch_verdict(d) == "skipped":
        return "skipped"
    if d.get("preferred_twice_confirm") is True or d.get("preferred_twice_confirm") == 1:
        return "preferred_twice"
    if redispatch_verdict(d) == "inaccurate":
        return "inaccurate"
    # 方案 A：未标测试不算且非 ×2 → 默认算不准确（含历史 pending）
    if str(d.get("preferred_assignee") or "").strip() or d.get("kind_source") == "scheme_a_auto":
        return "inaccurate"
    return "pending"


def correction_pair(detail: dict, description: str = "") -> tuple | None:
    """可学习的纠错对：(原处理人 A, 接手/倾向人 B, 原因)。B 可空，仍压 A。"""
    d = detail or {}
    if _norm_kind(d.get("kind")) == "misassign":
        a = str(d.get("from_assignee") or "").strip()
        b = str(d.get("new_assignee") or "").strip()
        if a and a != b:
            return a, b, str(d.get("reason") or "")[:500]
        return None
    if is_redispatch(d, description):
        # 方案 A：测试不算、倾向人×2 不学；其余重派默认可学
        if redispatch_metric_kind(d, description, channel="redispatch") != "inaccurate":
            return None
        a = str(d.get("from_assignee") or d.get("prev_assignee") or "").strip()
        b = str(d.get("preferred_assignee") or d.get("new_assignee") or "").strip()
        if a and a != b:
            return a, b, str(d.get("remark") or d.get("reason") or "")[:500]
        if a:
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
    """弹窗错派率只拿有 kind 的转派。重派按方案 A：默认不准确，×2/测试不算除外。"""
    by_kind = {k: 0 for k in SIGNAL_KINDS}
    by_channel = {c: 0 for c in CHANNELS}
    by_redispatch = {"pending": 0, "inaccurate": 0, "skipped": 0, "preferred_twice": 0}
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
            metric = ev.get("redispatch_metric") or redispatch_metric_kind(
                detail, ev.get("description") or "", channel=ch,
            )
            if metric == "skipped":
                by_redispatch["skipped"] += 1
            elif metric == "preferred_twice":
                by_redispatch["preferred_twice"] += 1
            elif metric == "inaccurate":
                by_redispatch["inaccurate"] += 1
                if tid is not None:
                    inaccurate_tickets.add(tid)
            else:
                by_redispatch["pending"] += 1
            if tid is not None:
                redispatch_tickets.add(tid)
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
        "redispatch_preferred_twice": by_redispatch["preferred_twice"],
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
        elif ch == "redispatch":
            metric = ev.get("redispatch_metric") or redispatch_metric_kind(
                ev.get("detail") or {}, ev.get("description") or "", channel=ch,
            )
            if metric == "inaccurate":
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


def _rate(n: int, d: int) -> Optional[float]:
    if not d:
        return None
    return round(n / d, 4)


def classify_dispatch_branch(
    *,
    matched_pref,
    preferred_id: str = "",
    assigned_id: str = "",
    reasoning: str = "",
    profile=None,
) -> str:
    """派单日志分支：step0 / preferred_twice / 空（普通 AI）。"""
    text = reasoning or ""
    if "连续两次" in text:
        return "preferred_twice"
    if not matched_pref:
        return ""
    pref = str(preferred_id or "").strip()
    assigned = str(assigned_id or "").strip()
    if not pref:
        return ""
    if assigned and pref != assigned:
        return ""
    prof = profile if isinstance(profile, dict) else {}
    if prof.get("specified_name") or prof.get("specified_multi"):
        return "step0"
    if "提单人指定" in text or "指定处理人" in text:
        return "step0"
    return ""


def _ticket_row(task_id: int, title: str, created_at: str, tag: str = "") -> dict:
    return {
        "task_id": int(task_id),
        "title": title or "",
        "created_at": created_at or "",
        "tag": tag or "",
    }


def build_dispatch_funnel(
    tickets: List[dict],
    ai_rows: List[dict],
    events: List[dict],
    ticket_flags: Dict[int, dict],
) -> dict:
    """按工单创建范围构建双漏斗（按单｜按次）。

    tickets: [{task_id, title, created_at}]
    ticket_flags: task_id -> {step0: bool, preferred_twice: bool, preferred_twice_attempts: int}
    倾向人×2：仅曝光、不从 AI 分母扣除。
    按次顶层含未走 AI 的建单指派（每张计 1 次），再扣从未走过 AI，避免与「走过 AI」同宽。
    """
    created_ids = []
    titles: Dict[int, str] = {}
    created_at_map: Dict[int, str] = {}
    for t in tickets:
        tid = t.get("task_id")
        if tid is None:
            continue
        tid = int(tid)
        created_ids.append(tid)
        titles[tid] = t.get("title") or ""
        created_at_map[tid] = t.get("created_at") or ""

    created_set = set(created_ids)
    flags = ticket_flags or {}

    step0_ids = {tid for tid in created_set if (flags.get(tid) or {}).get("step0")}
    preferred_ids = {
        tid for tid in created_set if (flags.get(tid) or {}).get("preferred_twice")
    }
    ai_by_ticket: Dict[int, int] = defaultdict(int)
    for row in ai_rows:
        tid = row.get("task_id")
        if tid is None:
            continue
        tid = int(tid)
        if tid in created_set:
            ai_by_ticket[tid] += 1

    has_ai = set(ai_by_ticket.keys())
    never_ai_ids = created_set - has_ai
    # 漏斗顺序：先扣从未 AI → 再扣 Step0 → AI 池；倾向人×2 仅曝光不扣
    after_never_ai_ids = has_ai  # 走过至少一次 ai_assign（含 Step0）
    step0_in_ai = step0_ids & has_ai
    ai_pool_ids = after_never_ai_ids - step0_ids

    mis_only: set = set()
    red_only: set = set()
    both: set = set()
    mis_tickets: set = set()
    red_tickets: set = set()
    mis_events = 0
    red_events = 0

    for ev in events:
        tid = ev.get("task_id")
        if tid is None:
            continue
        tid = int(tid)
        if tid not in ai_pool_ids:
            continue
        ch = ev.get("channel") or event_channel(ev.get("detail") or {}, ev.get("description") or "")
        kind = _norm_kind(ev.get("kind") or (ev.get("detail") or {}).get("kind"))
        verdict = ev.get("redispatch_verdict") or redispatch_verdict(ev.get("detail") or {})
        if ch == "signal" and kind == "misassign":
            mis_events += 1
            mis_tickets.add(tid)
        elif ch == "redispatch":
            metric = ev.get("redispatch_metric") or redispatch_metric_kind(
                ev.get("detail") or {}, ev.get("description") or "", channel=ch,
            )
            if metric == "inaccurate":
                red_events += 1
                red_tickets.add(tid)

    for tid in ai_pool_ids:
        has_m = tid in mis_tickets
        has_r = tid in red_tickets
        if has_m and has_r:
            both.add(tid)
        elif has_m:
            mis_only.add(tid)
        elif has_r:
            red_only.add(tid)

    union_tickets = mis_only | red_only | both
    # 按次：本周创建且非 Step0 的单上的全部 ai_assign
    attempt_on_has_ai = sum(ai_by_ticket[tid] for tid in after_never_ai_ids)
    attempt_step0 = sum(ai_by_ticket[tid] for tid in step0_in_ai)
    attempt_total = sum(ai_by_ticket[tid] for tid in ai_pool_ids)
    preferred_attempts = sum(
        int((flags.get(tid) or {}).get("preferred_twice_attempts") or 0)
        for tid in ai_pool_ids
    )
    attempt_union = mis_events + red_events
    # 从未走 AI 的单仍有建单指派：每张计 1 次非 AI 派单，否则按次漏斗顶层与「走过 AI」同宽、扣除无意义
    never_ai_attempts = len(never_ai_ids)
    attempt_created = attempt_on_has_ai + never_ai_attempts

    ticket_mis = len(mis_only) + len(both)
    ticket_red = len(red_only) + len(both)
    ai_pool_n = len(ai_pool_ids)
    created_n = len(created_set)
    after_never_ai_n = len(after_never_ai_ids)
    after_step0_n = ai_pool_n  # 扣完从未 AI + Step0 后的剩余 = AI 池

    def _list(ids: set, tag: str) -> List[dict]:
        rows = [
            _ticket_row(tid, titles.get(tid, ""), created_at_map.get(tid, ""), tag)
            for tid in ids
        ]
        return sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)

    return {
        "created_total": created_n,
        "after_never_ai": after_never_ai_n,
        "after_step0": after_step0_n,
        "ai_pool_tickets": ai_pool_n,
        "drops": {
            # 扣除顺序：从未 AI → Step0；倾向人×2 仅曝光
            "never_ai": {
                "count": len(never_ai_ids),
                "attempts": never_ai_attempts,
                "status": "deduct",
                "order": 1,
                "label": "从未走过 AI",
                "tickets": _list(never_ai_ids, "从未 AI"),
            },
            "step0": {
                "count": len(step0_in_ai),
                "status": "deduct",
                "order": 2,
                "label": "Step0 命中",
                "tickets": _list(step0_in_ai, "Step0 命中"),
            },
            "preferred_twice": {
                "count": len(preferred_ids & created_set),
                "attempts": preferred_attempts,
                "status": "expose",
                "order": 3,
                "label": "倾向人连续两次直派",
                "tickets": _list(preferred_ids & created_set, "倾向人×2（仅曝光）"),
            },
        },
        "ticket_funnel": {
            "created": created_n,
            "after_never_ai": after_never_ai_n,
            "after_step0": after_step0_n,
            "ai_pool": ai_pool_n,
            "misassign_only": len(mis_only),
            "redispatch_only": len(red_only),
            "both": len(both),
            "union": len(union_tickets),
        },
        "attempt_funnel": {
            "created_attempts": attempt_created,
            "never_ai_attempts": never_ai_attempts,
            "ai_assign_total": attempt_total,
            "after_never_ai_attempts": attempt_on_has_ai,
            "step0_attempts": attempt_step0,
            "preferred_twice_attempts": preferred_attempts,
            "denominator": attempt_total,
            "misassign_events": mis_events,
            "redispatch_inaccurate_events": red_events,
            "union_events": attempt_union,
        },
        "rates": {
            "ticket_misassign": _rate(ticket_mis, ai_pool_n),
            "ticket_redispatch": _rate(ticket_red, ai_pool_n),
            "ticket_both": _rate(len(both), ai_pool_n),
            "ticket_union": _rate(len(union_tickets), ai_pool_n),
            "attempt_misassign": _rate(mis_events, attempt_total),
            "attempt_redispatch": _rate(red_events, attempt_total),
            "attempt_union": _rate(attempt_union, attempt_total),
        },
        "ticket_lists": {
            "step0": _list(step0_in_ai, "Step0 命中"),
            "preferred_twice": _list(preferred_ids & created_set, "倾向人×2"),
            "never_ai": _list(never_ai_ids, "从未 AI"),
            "ai_pool": _list(ai_pool_ids, "AI 池"),
            "misassign_only": _list(mis_only, "仅派错了"),
            "redispatch_only": _list(red_only, "仅重派不准确"),
            "both": _list(both, "两者都有"),
            "union": _list(union_tickets, "错派并集"),
        },
    }


def _week_of(value) -> Optional[Tuple[str, str, str]]:
    dt = _parse_created_at(value)
    if not dt:
        return None
    return _iso_week_meta(dt)


def _attempts_by_dispatch_time(
    dispatch_rows: List[dict],
    never_ai_ids: set,
    events_in_week: List[dict],
    ticket_flags: Dict[int, dict],
    ever_ai: set,
) -> dict:
    """按次归到派单发生的那一周。

    ai_assign 用日志时间。没走过 AI 的建单指派没有 ai_assign，记在工单创建周，每张 1 次。
    Step0 命中的 ai_assign 从错派率分母扣除。错派事件按事件自己的时间归周，且只计走过 AI、非 Step0 的单。
    """
    flags = ticket_flags or {}
    ai_counts: Dict[int, int] = defaultdict(int)
    for row in dispatch_rows:
        tid = row.get("task_id")
        if tid is None:
            continue
        ai_counts[int(tid)] += 1

    after_never = sum(ai_counts.values())
    step0_attempts = sum(
        n for tid, n in ai_counts.items() if (flags.get(tid) or {}).get("step0")
    )
    denominator = after_never - step0_attempts
    preferred_attempts = sum(
        n for tid, n in ai_counts.items()
        if (flags.get(tid) or {}).get("preferred_twice") and not (flags.get(tid) or {}).get("step0")
    )
    never_ai_attempts = len(never_ai_ids)

    mis_events = 0
    red_events = 0
    for ev in events_in_week:
        tid = ev.get("task_id")
        if tid is None:
            continue
        tid = int(tid)
        if tid not in ever_ai or (flags.get(tid) or {}).get("step0"):
            continue
        ch = ev.get("channel") or event_channel(ev.get("detail") or {}, ev.get("description") or "")
        kind = _norm_kind(ev.get("kind") or (ev.get("detail") or {}).get("kind"))
        if ch == "signal" and kind == "misassign":
            mis_events += 1
        elif ch == "redispatch":
            metric = ev.get("redispatch_metric") or redispatch_metric_kind(
                ev.get("detail") or {}, ev.get("description") or "", channel=ch,
            )
            if metric == "inaccurate":
                red_events += 1

    union_events = mis_events + red_events
    return {
        "created_attempts": after_never + never_ai_attempts,
        "never_ai_attempts": never_ai_attempts,
        "ai_assign_total": denominator,
        "after_never_ai_attempts": after_never,
        "step0_attempts": step0_attempts,
        "preferred_twice_attempts": preferred_attempts,
        "denominator": denominator,
        "misassign_events": mis_events,
        "redispatch_inaccurate_events": red_events,
        "union_events": union_events,
    }


def _apply_attempt_funnel(funnel: dict, attempt: dict) -> None:
    funnel["attempt_funnel"] = attempt
    den = int(attempt.get("denominator") or 0)
    rates = funnel.setdefault("rates", {})
    rates["attempt_misassign"] = _rate(int(attempt.get("misassign_events") or 0), den)
    rates["attempt_redispatch"] = _rate(int(attempt.get("redispatch_inaccurate_events") or 0), den)
    rates["attempt_union"] = _rate(int(attempt.get("union_events") or 0), den)
    drops = funnel.get("drops") or {}
    if drops.get("never_ai") is not None:
        drops["never_ai"]["attempts"] = int(attempt.get("never_ai_attempts") or 0)
    if drops.get("preferred_twice") is not None:
        drops["preferred_twice"]["attempts"] = int(attempt.get("preferred_twice_attempts") or 0)


def _sum_attempt_funnels(weekly: List[dict]) -> dict:
    keys = (
        "created_attempts",
        "never_ai_attempts",
        "ai_assign_total",
        "after_never_ai_attempts",
        "step0_attempts",
        "preferred_twice_attempts",
        "denominator",
        "misassign_events",
        "redispatch_inaccurate_events",
        "union_events",
    )
    acc = {k: 0 for k in keys}
    for row in weekly:
        attempt = ((row.get("funnel") or {}).get("attempt_funnel") or {})
        for k in keys:
            acc[k] += int(attempt.get(k) or 0)
    return acc


def build_funnel_weekly(
    tickets: List[dict],
    ai_rows: List[dict],
    events: List[dict],
    ticket_flags: Dict[int, dict],
    *,
    keep: int = WEEKLY_KEEP,
) -> List[dict]:
    """按单按工单创建周；按次按派单发生周。"""
    week_tickets: Dict[str, List[dict]] = defaultdict(list)
    week_ai_time: Dict[str, List[dict]] = defaultdict(list)
    week_ev_time: Dict[str, List[dict]] = defaultdict(list)
    week_meta: Dict[str, Tuple[str, str]] = {}

    def _touch(value) -> Optional[str]:
        meta = _week_of(value)
        if not meta:
            return None
        key, label, start = meta
        week_meta[key] = (label, start)
        return key

    for t in tickets:
        key = _touch(t.get("created_at"))
        if key:
            week_tickets[key].append(t)
    for row in ai_rows:
        key = _touch(row.get("created_at"))
        if key:
            week_ai_time[key].append(row)
    for ev in events:
        key = _touch(ev.get("created_at"))
        if key:
            week_ev_time[key].append(ev)

    ai_by_ticket: Dict[int, List[dict]] = defaultdict(list)
    for row in ai_rows:
        if row.get("task_id") is not None:
            ai_by_ticket[int(row["task_id"])].append(row)
    ev_by_ticket: Dict[int, List[dict]] = defaultdict(list)
    for ev in events:
        if ev.get("task_id") is not None:
            ev_by_ticket[int(ev["task_id"])].append(ev)
    ever_ai = set(ai_by_ticket)

    keys = sorted(week_meta.keys(), key=lambda k: week_meta[k][1])
    if keep > 0:
        keys = keys[-keep:]
    out = []
    for key in keys:
        label, start = week_meta[key]
        created = week_tickets.get(key) or []
        created_ids = {int(t["task_id"]) for t in created if t.get("task_id") is not None}
        ai_for_created = [r for tid in created_ids for r in ai_by_ticket.get(tid, [])]
        ev_for_created = [e for tid in created_ids for e in ev_by_ticket.get(tid, [])]
        funnel = build_dispatch_funnel(created, ai_for_created, ev_for_created, ticket_flags)
        never_ai_ids = {tid for tid in created_ids if tid not in ever_ai}
        attempt = _attempts_by_dispatch_time(
            week_ai_time.get(key) or [],
            never_ai_ids,
            week_ev_time.get(key) or [],
            ticket_flags,
            ever_ai,
        )
        _apply_attempt_funnel(funnel, attempt)
        out.append({
            "week": key,
            "label": label,
            "week_start": start,
            "funnel": funnel,
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


def _funnel_cutoff() -> datetime:
    """漏斗只看最近 WEEKLY_KEEP 个自然周（按创建时间）。"""
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    monday = today - timedelta(days=today.weekday())
    start = monday - timedelta(weeks=max(WEEKLY_KEEP - 1, 0))
    return datetime(start.year, start.month, start.day)


def _load_funnel_inputs() -> Tuple[List[dict], Dict[int, dict]]:
    """工单创建列表 + 每张单的 Step0 / 倾向人×2 标记（来自 task_dispatch_log）。"""
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    cutoff = _funnel_cutoff()
    engine = _get_engine()
    with engine.connect() as db:
        task_rows = db.execute(text(
            "SELECT id, title, created_at FROM tasks "
            "WHERE created_at >= :cutoff ORDER BY created_at DESC"
        ), {"cutoff": cutoff}).mappings().all()
        task_ids = [int(r["id"]) for r in task_rows]
        dispatch_rows = []
        if task_ids:
            dispatch_rows = db.execute(
                text(
                    "SELECT task_id, dispatch_round, matched_pref, preferred_id, "
                    "assigned_id, reasoning, profile "
                    "FROM task_dispatch_log "
                    "WHERE task_id IN :ids "
                    "ORDER BY task_id ASC, dispatch_round ASC"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": task_ids},
            ).mappings().all()

    tickets = []
    for r in task_rows:
        created = r.get("created_at")
        tickets.append({
            "task_id": int(r["id"]),
            "title": r.get("title") or "",
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
        })

    by_task: Dict[int, List[dict]] = defaultdict(list)
    for r in dispatch_rows:
        by_task[int(r["task_id"])].append(r)

    flags: Dict[int, dict] = {}
    for tid, rows in by_task.items():
        step0 = False
        preferred_twice = False
        preferred_attempts = 0
        first = rows[0] if rows else None
        for i, r in enumerate(rows):
            branch = classify_dispatch_branch(
                matched_pref=bool(r.get("matched_pref")),
                preferred_id=str(r.get("preferred_id") or ""),
                assigned_id=str(r.get("assigned_id") or ""),
                reasoning=str(r.get("reasoning") or ""),
                profile=_as_dict(r.get("profile")),
            )
            if branch == "preferred_twice":
                preferred_twice = True
                preferred_attempts += 1
            if i == 0 and first is not None and branch == "step0":
                step0 = True
            # 首轮若未标 specified，但 matched_pref 且 reasoning 含指定 — classify 已覆盖
            if i == 0 and not step0 and first is not None:
                # 与 step0_blocks_redispatch 对齐的兜底：首轮 matched_pref 且非倾向人×2
                # 仅当 reasoning/profile 已判 step0；避免把普通倾向命中算进去
                pass
        flags[tid] = {
            "step0": step0,
            "preferred_twice": preferred_twice,
            "preferred_twice_attempts": preferred_attempts,
        }
    return tickets, flags


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
    """可打「测试不算」的重新派单：未 skipped；方案 A 下默认已算不准确，此处供剔除。"""
    items = []
    for e in events:
        if e.get("channel") != "redispatch":
            continue
        detail = e.get("detail") or {}
        metric = e.get("redispatch_metric") or redispatch_metric_kind(
            detail, e.get("description") or "", channel="redispatch",
        )
        if metric == "skipped":
            continue
        # 倾向人×2 不进不准确，一般不必出现在剔除队列；仍露出便于核对
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
            "metric_kind": metric,
            "preferred_twice_confirm": metric == "preferred_twice",
        })
        if len(items) >= limit:
            break
    return items


def _prev_preferred_before(db, task_id: int, created_at) -> str:
    """重派前最近一轮派单的 preferred_id（用于识别倾向人×2）。"""
    params = {"tid": int(task_id)}
    sql = "SELECT preferred_id FROM task_dispatch_log WHERE task_id = :tid "
    if created_at is not None:
        sql += "AND created_at < :ts "
        params["ts"] = created_at
    sql += "ORDER BY dispatch_round DESC LIMIT 1"
    row = db.execute(text(sql), params).mappings().first()
    return str((row or {}).get("preferred_id") or "").strip()


def annotate_redispatch_scheme_a(
    events: List[dict],
    prev_preferred: Dict[int, List[Tuple[Optional[datetime], str]]],
) -> None:
    """按方案 A 给重派事件打 preferred_twice_confirm / redispatch_metric（就地改）。

    prev_preferred[task_id] = 按时间排序的 [(dispatch_created_at, preferred_id), ...]
    """
    for ev in events:
        detail = ev.get("detail") or {}
        ch = ev.get("channel") or event_channel(detail, ev.get("description") or "")
        if ch != "redispatch":
            continue
        detail = dict(detail)
        pref = str(detail.get("preferred_assignee") or "").strip()
        ev_dt = _parse_created_at(ev.get("created_at"))
        prev = ""
        for d_at, d_pref in prev_preferred.get(int(ev["task_id"]), []) if ev.get("task_id") is not None else []:
            if ev_dt and d_at and d_at > ev_dt:
                break
            prev = d_pref or prev
        if "preferred_twice_confirm" not in detail:
            detail["preferred_twice_confirm"] = bool(pref and prev and pref == prev)
        metric = redispatch_metric_kind(
            detail, ev.get("description") or "", channel="redispatch",
        )
        ev["detail"] = detail
        ev["redispatch_metric"] = metric
        ev["redispatch_verdict"] = redispatch_verdict(detail)
        ev["preferred_twice_confirm"] = bool(detail.get("preferred_twice_confirm"))


def _load_dispatch_preferred_timeline(task_ids: List[int]) -> Dict[int, List[Tuple[Optional[datetime], str]]]:
    if not task_ids:
        return {}
    from ai.agents.AiDiagnosisPlatform.assigner.sync.history_indexer import _get_engine

    engine = _get_engine()
    with engine.connect() as db:
        rows = db.execute(
            text(
                "SELECT task_id, preferred_id, created_at FROM task_dispatch_log "
                "WHERE task_id IN :ids ORDER BY task_id ASC, dispatch_round ASC"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": task_ids},
        ).mappings().all()
    out: Dict[int, List[Tuple[Optional[datetime], str]]] = defaultdict(list)
    for r in rows:
        out[int(r["task_id"])].append((
            _parse_created_at(r.get("created_at")),
            str(r.get("preferred_id") or "").strip(),
        ))
    return out


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
            if kind == "skipped":
                detail.pop("learn_at", None)
            # 复核 ×2 标记（历史单可能缺字段）
            if "preferred_twice_confirm" not in detail:
                prev_pref = _prev_preferred_before(
                    db, int(row["task_id"]), row.get("created_at"),
                )
                pref = str(detail.get("preferred_assignee") or "").strip()
                detail["preferred_twice_confirm"] = bool(pref and prev_pref and pref == prev_pref)
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
    try:
        tids = sorted({int(e["task_id"]) for e in events if e.get("task_id") is not None})
        annotate_redispatch_scheme_a(events, _load_dispatch_preferred_timeline(tids))
    except Exception as e:
        logger.warning(f"[转派统计] 方案A标注重派失败: {e}", exc_info=True)
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

    funnel = None
    funnel_weekly: List[dict] = []
    try:
        tickets, ticket_flags = _load_funnel_inputs()
        funnel_weekly = build_funnel_weekly(tickets, ai_rows, events, ticket_flags)
        funnel = build_dispatch_funnel(tickets, ai_rows, events, ticket_flags)
        _apply_attempt_funnel(funnel, _sum_attempt_funnels(funnel_weekly))
    except Exception as e:
        logger.warning(f"[转派统计] 构建漏斗失败: {e}", exc_info=True)
        funnel = {"error": str(e)}

    return {
        "metrics": metrics,
        "unlabeled": metrics.get("unlabeled_total") or 0,
        "llm": {"called": 0, "failed": 0, "pending": 0},
        "persisted": 0,
        "samples": _sample_events(events),
        "ticket_lists": build_ticket_lists(events),
        "weekly": build_weekly_metrics(events, ai_rows),
        "funnel": funnel,
        "funnel_weekly": funnel_weekly,
        "unlabeled_items": items,
        "unlabeled_groups": groups,
        "redispatch_items": _redispatch_items(events, names),
        "note": (
            "按单按工单创建周。按次按派单发生周（ai_assign 日志时间；未走 AI 的建单指派记在创建周）。"
            "扣除：从未 AI → Step0；倾向人×2 仅曝光。"
            "重派按方案 A：默认算不准确并进学习；倾向人×2、测试不算除外。"
            "开发者模式重派列表主要用于打「测试不算」剔除。"
            "错派两分支（派错了 / 重派不准确）分开标，重合单独显示。"
        ),
    }


async def classify_reassign_stats(*, use_llm: bool = False, persist: bool = False, force: bool = False) -> dict:
    """保留旧入口；分类已改为只读固定信号，不再调 LLM。"""
    return summarize_reassign_stats()
