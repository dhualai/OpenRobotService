# -*- coding: utf-8 -*-
"""知识沉淀共享内核：DB 组装 → LLM 提炼 → Qdrant 入库 → 标记回写。

被两方复用（0828 知识沉淀 worker 计划）：
- ai/tools/backfill_resolutions.py（存量批量回填，CLI）
- ai/core/knowledge_worker.py 的 run_knowledge_worker（增量）

0901 移入 ai/core（共享核心）：知识沉淀服务跨平台（数据来自工单平台、
检索服务诊断平台），不属于任何单个 agent 的私有 services。

DB 访问走 ai.core.database（原生 SQL，自举模式——不依赖 backend ORM，
与 history_indexer 同一纪律；连哪个库由 DATABASE_URL 决定）。
"""
import json
import time

from ai.core.logging import get_logger

logger = get_logger("KNOWLEDGE_SINK")

_COMMENT_EACH_LIMIT = 200     # 单条评论截断
_COMMENTS_TOTAL_LIMIT = 2000  # 评论聚合总截断


def load_candidates(limit: int, offset: int = 0, include_skipped: bool = False) -> list[dict]:
    """待沉淀工单：closed/resolved ∧ ¬solution_indexed ∧ (rs已填 ∨ 有评论)。

    include_skipped=True 时连之前判定空卡（status=empty）的也重试
    （评论后来补了的场景）。
    """
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        sql = (
            "SELECT t.id, t.title, t.description, t.assigned_to, t.metadata_info, "
            "       t.project_name "
            "FROM tasks t "
            "WHERE t.status IN ('resolved','closed') "
            # IS NOT TRUE 而非 NOT ... = TRUE：键缺失时 JSON_EXTRACT 返回 NULL，
            # 三值逻辑下 NOT(NULL=TRUE) 仍是 NULL，会把所有未标记的行全过滤掉
            "AND JSON_EXTRACT(t.metadata_info, '$.solution_indexed') IS NOT TRUE "
            + ("" if include_skipped else
               " AND JSON_EXTRACT(t.metadata_info, '$.solution_index_status') IS NULL ")
            + "AND ("
            "  JSON_UNQUOTE(JSON_EXTRACT(t.metadata_info, '$.resolution_summary')) != '' "
            "  OR EXISTS (SELECT 1 FROM task_comments c WHERE c.task_id = t.id)) "
            "ORDER BY t.updated_at DESC "
            f"LIMIT :lim OFFSET :off"
        )
        rows = db.execute(text(sql), {"lim": limit, "off": offset}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def load_comments_text(task_id: int) -> tuple[str, str]:
    """评论聚合（时间正序，截断）+ 最后一位人类评论者（解决人线索）。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT c.content, c.created_by, u.name "
            "FROM task_comments c LEFT JOIN users u ON u.username = c.created_by "
            "WHERE c.task_id = :tid ORDER BY c.created_at ASC"
        ), {"tid": task_id}).fetchall()
    finally:
        db.close()
    lines, total, last_human = [], 0, ""
    for content, created_by, display_name in rows:
        name = display_name or created_by
        line = f"{name}：{(content or '').strip()[:_COMMENT_EACH_LIMIT]}"
        if total + len(line) > _COMMENTS_TOTAL_LIMIT:
            lines.append("（后续评论已截断）")
            break
        lines.append(line)
        total += len(line)
        if created_by != "U老师":
            last_human = name
    return "\n".join(lines), last_human


def resolver_name(assigned_to: str, last_human: str) -> str:
    """解决人：assigned_to 映射用户显示名，未命中回退评论里最后发言的人。"""
    if assigned_to:
        from ai.core.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT name FROM users WHERE username = :u LIMIT 1"),
                {"u": assigned_to}).fetchone()
            if row and row[0]:
                return row[0]
        finally:
            db.close()
        return assigned_to
    return last_human or ""


def mark_indexed(task_id: int, status: str) -> None:
    """回写沉淀标记。status: indexed（已入库）/ empty（无知识可提，跳过）。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text(
            "UPDATE tasks SET metadata_info = JSON_MERGE_PATCH("
            "  COALESCE(metadata_info, JSON_OBJECT()), "
            "  JSON_OBJECT('solution_indexed', :flag, "
            "              'solution_indexed_at', :at, "
            "              'solution_index_status', :st)) "
            "WHERE id = :tid"),
            {"flag": status == "indexed", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
             "st": status, "tid": task_id})
        db.commit()
    finally:
        db.close()


async def process_ticket(row: dict, retriever, dry_run: bool = False) -> str:
    """处理单张工单：组装 → 提炼 → 入库 → 标记。

    Returns: indexed | empty | failed
    """
    from ai.core.solution_distiller import distill_solution
    tid = row["id"]
    meta = row.get("metadata_info") or {}
    if not isinstance(meta, dict):
        try:
            meta = json.loads(meta) if meta else {}
        except Exception:
            meta = {}
    comments_text, last_human = load_comments_text(tid)
    card = await distill_solution(
        title=row.get("title") or "",
        description=row.get("description") or "",
        resolution_summary=meta.get("resolution_summary") or "",
        comments_text=comments_text,
    )
    if card is None:
        if not dry_run:
            mark_indexed(tid, "empty")
        logger.info(f"[sink] #{tid} 空卡跳过（无知识可提）")
        return "empty"
    resolver = resolver_name(row.get("assigned_to") or "", last_human)
    logger.info(f"[sink] #{tid} 卡片: 问题={card['problem_summary'][:60]} | "
                f"根因={card['root_cause'][:40]} | 解决人={resolver}")
    if dry_run:
        return "indexed"
    ok = await retriever.index_task_resolution(
        task_id=str(tid),
        title=row.get("title") or f"工单 #{tid}",
        root_cause=card["root_cause"],
        solution_steps=card["solution_steps"],
        engineer_note=f"解决人: {resolver}" if resolver else "",
        fault_code=card["fault_code"] or meta.get("fault_code") or "",
        robot_type=card["robot_type"] or meta.get("robot_type") or "",
        problem_summary=card["problem_summary"],
        extra_payload={
            "description": (row.get("description") or "")[:1000],
            "comments_text": comments_text,
            "resolver": resolver,
            "project_name": row.get("project_name") or "",
        },
    )
    if not ok:
        return "failed"
    mark_indexed(tid, "indexed")
    return "indexed"
