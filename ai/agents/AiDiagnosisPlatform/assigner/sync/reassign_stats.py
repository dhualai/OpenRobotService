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
from datetime import datetime, timezone
from typing import Iterable, List, Tuple

from sqlalchemy import bindparam, text

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

SIGNAL_KINDS = ("misassign", "stage", "other")
REDISPATCH_VERDICTS = ("inaccurate", "skipped")
CHANNELS = ("signal", "redispatch", "unlabeled", "skipped")


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


def _load_rows() -> Tuple[List[dict], int, int]:
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
        ai_total = db.execute(text(
            "SELECT COUNT(*) AS n FROM task_operation_logs WHERE operation_type = 'ai_assign'"
        )).scalar() or 0
        ai_tickets = db.execute(text(
            "SELECT COUNT(DISTINCT task_id) AS n FROM task_operation_logs "
            "WHERE operation_type = 'ai_assign'"
        )).scalar() or 0

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
            "reason": (detail.get("reason") or detail.get("remark") or "")[:200],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created or ""),
            "description": r.get("description") or "",
            "operator": r.get("operator") or "",
            "operator_name": r.get("operator_name") or r.get("operator") or "",
            "detail": detail,
        })
    return events, int(ai_total), int(ai_tickets)


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


def _unlabeled_items(events: List[dict], names: dict, limit: int = 100) -> List[dict]:
    items = []
    for e in events:
        if e.get("channel") != "unlabeled":
            continue
        detail = e.get("detail") or {}
        from_id = str(detail.get("from_assignee") or "").strip()
        to_id = str(detail.get("new_assignee") or "").strip()
        items.append({
            "id": e["id"],
            "task_id": e["task_id"],
            "title": e.get("title") or "",
            "reason": e.get("reason") or "",
            "description": e.get("description") or "",
            "created_at": e.get("created_at") or "",
            "from_id": from_id,
            "from_name": names.get(from_id) or from_id or "—",
            "to_id": to_id,
            "to_name": names.get(to_id) or to_id or "—",
        })
        if len(items) >= limit:
            break
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
    events, ai_total, ai_tickets = _load_rows()
    metrics = aggregate_events(events, ai_assign_total=ai_total, ai_assign_tickets=ai_tickets)
    names = _name_by_id()
    return {
        "metrics": metrics,
        "unlabeled": metrics.get("unlabeled_total") or 0,
        "llm": {"called": 0, "failed": 0, "pending": 0},
        "persisted": 0,
        "samples": _sample_events(events),
        "unlabeled_items": _unlabeled_items(events, names),
        "redispatch_items": _redispatch_items(events, names),
        "note": (
            "转派弹窗三个类型单独计错派率。"
            "重新派单（提单人/处理人/管理员让 AI 再派）测试期要人工审核："
            "算不准确计入不准确率并进入派单学习（压原处理人 ×0.7），测试不算则跳过。"
        ),
    }


async def classify_reassign_stats(*, use_llm: bool = False, persist: bool = False, force: bool = False) -> dict:
    """保留旧入口；分类已改为只读固定信号，不再调 LLM。"""
    return summarize_reassign_stats()
