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

# AI 评论账号（0902 人工审核实锤：U老师自己的总结不能当处理人解法依据，
# 12/240 张驳回直接因此——提炼输入一律剔除；与 backend task.py ai_names 对齐）
_AI_AUTHORS = {"U老师", "小U", "AI助手"}

# 内部开发项目不沉淀（0903 生产实锤：重跑 151 张卡里 102 张来自本项目自身的
# bug/feature 单，全是「一次性修复记录」，对未来现场诊断问答零复用价值）。
# 项目是结构化字段（客观事实），与 0902 否决「按类型硬过滤」的理由不冲突
# （类型是上游 LLM 判的会标错）。将来有新内部项目加进元组即可。
_EXCLUDE_PROJECTS = ("摇人吧服务号",)

# 模板标题的管理流单不做候选：0902【项目申请】；0903 #518/#525 实锤追加
# 新公司/新部门录入审核（固定工作流机械生成、解法只有一句审核结论，零诊断
# 价值）。与【项目申请】同族——结构化的标题事实，不是意图关键词判断。
_EXCLUDE_TITLE_PATTERNS = ("【项目申请】", "新公司录入审核", "新部门录入审核")


def load_candidates(limit: int, offset: int = 0, include_skipped: bool = False,
                    task_id: int | None = None) -> list[dict]:
    """待沉淀工单：closed/resolved ∧ ¬solution_indexed ∧ (rs已填 ∨ 有人类评论)。

    0902 人工审核规则（240 张实锤）：
    - 【项目申请】类标题一律不沉淀
    - 只有 AI 评论不算有素材（AI 自己的总结不是处理人解法）
    - BUG/FEATURE 不在此过滤：类型是上游 LLM 判的会标错（27 张 approved 里
      6 张本身是 BUG/FEATURE），交给 distiller 按内容软判断

    include_skipped=True 时连之前判定空卡（status=empty）的也重试
    （评论后来补了的场景）。
    task_id 指定时按 id 单张强制捞回，无视全部条件（空卡误杀复检通道，
    与 review_resolutions --export-skipped 配套）。
    """
    from ai.core.database import SessionLocal
    from sqlalchemy import text, bindparam
    db = SessionLocal()
    try:
        if task_id is not None:
            rows = db.execute(text(
                "SELECT t.id, t.title, t.description, t.assigned_to, t.metadata_info, "
                "       t.project_name, t.task_type "
                "FROM tasks t WHERE t.id = :tid"),
                {"tid": task_id}).mappings().all()
            return [dict(r) for r in rows]
        sql = text(
            "SELECT t.id, t.title, t.description, t.assigned_to, t.metadata_info, "
            "       t.project_name, t.task_type "
            "FROM tasks t "
            "WHERE t.status IN ('resolved','closed') "
            # IS NOT TRUE 而非 NOT ... = TRUE：键缺失时 JSON_EXTRACT 返回 NULL，
            # 三值逻辑下 NOT(NULL=TRUE) 仍是 NULL，会把所有未标记的行全过滤掉
            "AND JSON_EXTRACT(t.metadata_info, '$.solution_indexed') IS NOT TRUE "
            + ("" if include_skipped else
               " AND JSON_EXTRACT(t.metadata_info, '$.solution_index_status') IS NULL ")
            + "".join(f"AND t.title NOT LIKE '%{p}%' "
                      for p in _EXCLUDE_TITLE_PATTERNS)
            + "AND (t.project_name IS NULL OR t.project_name NOT IN :excl) "
            "AND ("
            "  JSON_UNQUOTE(JSON_EXTRACT(t.metadata_info, '$.resolution_summary')) != '' "
            "  OR EXISTS (SELECT 1 FROM task_comments c WHERE c.task_id = t.id "
            "             AND c.created_by NOT IN ('U老师','小U','AI助手'))) "
            "ORDER BY t.updated_at DESC "
            f"LIMIT :lim OFFSET :off"
        ).bindparams(bindparam("excl", expanding=True))
        rows = db.execute(sql, {"lim": limit, "off": offset,
                                "excl": list(_EXCLUDE_PROJECTS)}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def load_comments_text(task_id: int) -> tuple[str, str]:
    """人类评论聚合（时间正序，截断）+ 最后一位人类评论者（解决人线索）。

    AI 评论（U老师等）整条剔除：它是提问侧的自动分析，不是处理人给出的
    解法——0902 审核实锤多张卡因「把 U老师 的话当解法总结」被驳回。
    """
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
        if created_by in _AI_AUTHORS:
            continue
        name = display_name or created_by
        line = f"{name}：{(content or '').strip()[:_COMMENT_EACH_LIMIT]}"
        if total + len(line) > _COMMENTS_TOTAL_LIMIT:
            lines.append("（后续评论已截断）")
            break
        lines.append(line)
        total += len(line)
        last_human = name
    return "\n".join(lines), last_human


def _resolver_from_oplog(task_id: int) -> str:
    """点「解决」的操作人（工单平台操作日志 → resolved 的 STATUS_CHANGE）。

    0903 用户纠正：真解决人=接单点解决的人（提单→接单→点解决→提单人关闭）。
    assigned_to 可能流转多手；旧回退「评论区最后发言者」会把关闭前留言的
    提单人当解决人。结束工单必须由接单人填解决方式，这条日志的 operator
    语义精确。operation_type 列 ci collation 不区分大小写（SQLEnum 存
    name/value 均可命中）；to_status 是前端传值原样存，加 LOWER 保险。
    """
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        row = db.execute(text(
            "SELECT l.operator_name, l.operator FROM task_operation_logs l "
            "WHERE l.task_id = :tid AND l.operation_type = 'status_change' "
            "AND LOWER(l.to_status) = 'resolved' "
            "ORDER BY l.id DESC LIMIT 1"), {"tid": task_id}).fetchone()
        if not row:
            return ""
        name, operator = row[0], row[1]
        if name and name.strip():
            return name.strip()
        if operator:
            r2 = db.execute(text(
                "SELECT name FROM users WHERE username = :u LIMIT 1"),
                {"u": operator}).fetchone()
            return (r2[0] if r2 and r2[0] else operator)
        return ""
    finally:
        db.close()


def resolver_name(assigned_to: str, last_human: str) -> str:
    """解决人：assigned_to 映射用户显示名，未命中回退评论里最后发言的人。

    服务号 AI 提单的 assigned_to 可能是微信侧用户标识（openid 形状），
    users 表查不到——此时评论线索（last_human）比原样输出一串 id 有用。
    """
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
        return last_human or assigned_to
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
    ticket_type = getattr(row.get("task_type"), "value", row.get("task_type")) or ""
    card = await distill_solution(
        title=row.get("title") or "",
        description=row.get("description") or "",
        resolution_summary=meta.get("resolution_summary") or "",
        comments_text=comments_text,
        task_type=ticket_type,
    )
    if card is None:
        if not dry_run:
            mark_indexed(tid, "empty")
        logger.info(f"[sink] #{tid} 空卡跳过（无知识可提）")
        return "empty"
    resolver = (_resolver_from_oplog(tid)
                or resolver_name(row.get("assigned_to") or "", last_human))
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
            # 工单类型（problem/bug/feature/support/other）：审核「是否值得沉淀」
            # 的参考信号（如 feature 功能类通常无排查价值）。getattr 防 enum 对象
            "ticket_type": ticket_type,
        },
    )
    if not ok:
        return "failed"
    mark_indexed(tid, "indexed")
    return "indexed"
