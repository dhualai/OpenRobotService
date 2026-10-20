# -*- coding: utf-8 -*-
"""知识沉淀共享内核：DB 组装 → LLM 提炼 → Qdrant 入库 → 标记回写。

被两方复用（0828 知识沉淀 worker 计划）：
- ai/tools/backfill_resolutions.py（存量批量回填，CLI）
- ai/core/knowledge_worker.py 的 run_knowledge_worker（增量）

两条沉淀路，闸门不同：
- **closed 路**（load_candidates / process_ticket）：LLM 从评论提炼 → 写 pending 卡
  → 人工 review_resolutions 审核放行后才可检索。
- **archived 路**（load_archived_candidates / process_archived_ticket）：归档审核
  通过的单，直接用**审核过的归档报告**写卡，review_status=approved——归档审核
  本身就是那道人工闸门，不需要再走一次 pending。

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
    """待沉淀工单：**仅 closed**（已关闭，解决被提单人确认、内容终态）∧
    ¬solution_indexed ∧ (rs已填 ∨ 有人类评论)。

    0910 用户实锤：resolved（处理人点解决、提单人未确认关闭）不该沉淀——
    过早提炼会锁死旧总结（提单人驳回重解决后 worker 不再重沉，卡停在旧解上），
    且审核列表混入未关闭单。只收 closed 后，resolved 单等关闭后自动重进候选。

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
            # 只收 closed（0910 定调）：resolved 未关闭=解决未确认，内容可能变
            "WHERE t.status = 'closed' "
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
    ORS-1035：评论区图片的 VLM 描述（上传链路打标后随 attachments JSON 落库）
    拼进行尾——现场截图里的报错/现象从此进归档报告素材；无描述的历史图
    报张数占位（报告至少知道"此处有图，详见讨论区"，不硬造内容）。
    """
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT c.content, c.created_by, u.name, c.attachments "
            "FROM task_comments c LEFT JOIN users u ON u.username = c.created_by "
            "WHERE c.task_id = :tid ORDER BY c.created_at ASC"
        ), {"tid": task_id}).fetchall()
    finally:
        db.close()
    lines, total, last_human = [], 0, ""
    for content, created_by, display_name, attachments in rows:
        if created_by in _AI_AUTHORS:
            continue
        name = display_name or created_by
        line = f"{name}：{(content or '').strip()[:_COMMENT_EACH_LIMIT]}"
        img_parts = _comment_image_descs(attachments)
        if img_parts:
            line += f"（附图：{'；'.join(img_parts)}）"
        elif _count_comment_images(attachments):
            line += f"（附 {_count_comment_images(attachments)} 张图片，现场信息见讨论区原图）"
        if total + len(line) > _COMMENTS_TOTAL_LIMIT:
            lines.append("（后续评论已截断）")
            break
        lines.append(line)
        total += len(line)
        last_human = name
    return "\n".join(lines), last_human


def _comment_image_descs(attachments) -> list:
    """attachments JSON 里带 VLM 描述的图片 → [文件名：描述]（每评论 ≤3 张、
    每条截 120 字，与 summarize_flow 同口径）。"""
    out = []
    for a in attachments or []:
        if not isinstance(a, dict):
            continue
        d = str(a.get("desc") or "").strip()
        if not d:
            continue
        name = str(a.get("filename") or a.get("path") or "图")
        out.append(f"{name[:24]}：{d[:120]}")
        if len(out) >= 3:
            break
    return out


def _count_comment_images(attachments) -> int:
    """attachments JSON 里的图片张数（按扩展名，兼容 str 与 {path,...} dict）。"""
    n = 0
    for a in attachments or []:
        p = a if isinstance(a, str) else (str((a or {}).get("path") or "") if isinstance(a, dict) else "")
        if p and p.lower().rsplit(".", 1)[-1] in {"jpg", "jpeg", "png", "bmp", "gif", "webp"}:
            n += 1
    return n


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


def _as_meta(raw) -> dict:
    """metadata_info 可能是 dict 或 JSON 字符串，统一成 dict。"""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _fmt_reviewed_at(value) -> str:
    """审核时间统一成 'YYYY-mm-dd HH:MM:SS'；缺省用当前时间（保证字段不空）。"""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else time.strftime("%Y-%m-%d %H:%M:%S")


async def process_ticket(row: dict, retriever, dry_run: bool = False,
                         archive_content: str | None = None) -> str:
    """处理单张工单：组装 → 提炼 → 入库 → 标记。

    Returns: indexed | empty | failed
    """
    from ai.core.solution_distiller import distill_solution
    tid = row["id"]
    meta = _as_meta(row.get("metadata_info"))
    comments_text, last_human = load_comments_text(tid)
    ticket_type = getattr(row.get("task_type"), "value", row.get("task_type")) or ""
    card = await distill_solution(
        title=row.get("title") or "",
        description=row.get("description") or "",
        resolution_summary=(archive_content or meta.get("resolution_summary") or ""),
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


# ── 归档路：归档审核通过 → 直接沉淀（素材是人写且审核过的归档报告） ──

# 已归档、报告审核通过、且尚未按归档报告入库的单；取该单最高版本的 approved 报告。
# 标记用独立的 archive_sink_status 而非 solution_indexed：归档单通常早在 closed
# 阶段就被标过 solution_indexed，共用同一标记会让它永远进不了这条队列。
_ARCHIVED_CANDIDATES_SQL = (
    "SELECT t.id, t.title, t.description, t.assigned_to, t.metadata_info, "
    "       t.project_name, t.task_type, "
    "       r.content AS report_content, r.reviewer AS report_reviewer, "
    "       r.version AS report_version, r.reviewed_at AS report_reviewed_at "
    "FROM tasks t "
    "JOIN archive_reports r "
    "  ON r.task_id = t.id AND r.submit_status = 'approved' "
    " AND r.version = (SELECT MAX(r2.version) FROM archive_reports r2 "
    "                  WHERE r2.task_id = t.id AND r2.submit_status = 'approved') "
    "WHERE t.status = 'archived' "
    "  AND JSON_EXTRACT(t.metadata_info, '$.archive_sink_status') IS NULL "
    "ORDER BY t.updated_at DESC "
    "LIMIT :lim OFFSET :off"
)


def load_archived_candidates(limit: int, offset: int = 0) -> list[dict]:
    """待沉淀的「已归档」工单：归档审核通过、报告尚未入库。

    与 load_candidates（closed 路）分开：归档单的素材是**人写且审核通过的
    归档报告**，不再让 LLM 从评论里提炼。
    """
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        rows = db.execute(text(_ARCHIVED_CANDIDATES_SQL),
                          {"lim": limit, "off": offset}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def mark_archived_sunk(task_id: int, status: str) -> None:
    """回写归档沉淀标记。status: indexed（已入库）/ empty（报告为空，跳过）。"""
    from ai.core.database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text(
            "UPDATE tasks SET metadata_info = JSON_MERGE_PATCH("
            "  COALESCE(metadata_info, JSON_OBJECT()), "
            "  JSON_OBJECT('archive_sink_status', :st, 'archive_sink_at', :at)) "
            "WHERE id = :tid"),
            {"st": status, "at": time.strftime("%Y-%m-%d %H:%M:%S"), "tid": task_id})
        db.commit()
    finally:
        db.close()


async def process_archived_ticket(row: dict, retriever, dry_run: bool = False) -> str:
    """已归档工单沉淀：归档审核通过 → 把该单的卡**放行**。

    与人工审核路（closed 提炼）的**向量文本逐字相同**，分两种情形：
    1. **已有卡**（closed 路提炼过；点 ID 按 task_id 确定性相同）→ 只改 payload：
       `review_status=approved` + `reviewed_by/at` + `solution_steps` 换成归档报告，
       **向量一个字不动**——检索侧与人工放行的卡完全不可区分。
    2. **没有卡**（未沉淀/被驳回删过/被排除）→ 用**同一个 LLM 提炼器**（素材=归档报告）
       生成卡，字段构造与 closed 路一致，再写 approved。

    Returns: indexed | empty | failed
    """
    from ai.core.retrieval import render_resolution_content

    tid = row["id"]
    report = (row.get("report_content") or "").strip()
    if not report:
        if not dry_run:
            mark_archived_sunk(tid, "empty")
        logger.info(f"[sink] #{tid} 归档报告为空，跳过")
        return "empty"

    meta = _as_meta(row.get("metadata_info"))
    reviewer = (row.get("report_reviewer") or "").strip()
    version = row.get("report_version")
    # 与人工审核放行的卡同键：review_resolutions --apply 会写 reviewed_by/at，
    # ticket_card_store 换集合搬运时按 reviewed_at 比新旧，缺了会被旧卡压住。
    reviewed_at_str = _fmt_reviewed_at(row.get("report_reviewed_at"))
    logger.info(f"[sink:archive] #{tid} 归档报告 v{version} "
                f"审核人={reviewer or '-'}，放行沉淀")
    if dry_run:
        return "indexed"

    # 1) 已有卡 → 只放行 + 换 solution，向量不重写
    existing = await retriever.fetch_task_resolution_payload(str(tid))
    if existing:
        patch = {
            "review_status": "approved",
            "reviewed_by": reviewer,
            "reviewed_at": reviewed_at_str,
            "solution_steps": report,
            # content 是答案正文的映射源，solution_steps 换了它必须跟着换；
            # 问题侧字段沿用原卡，向量文本与人工审核路保持逐字一致
            "content": render_resolution_content(
                existing.get("problem_summary") or "",
                existing.get("root_cause") or "",
                report,
                existing.get("fault_code") or "",
                existing.get("robot_type") or "",
            ),
            "source": "archive_review",
            "archive_report_version": version,
        }
        if not await retriever.patch_task_resolution_payload(str(tid), patch):
            return "failed"
        logger.info(f"[sink:archive] #{tid} 已有卡：只放行，向量未动")
        mark_archived_sunk(tid, "indexed")
        return "indexed"

    # 2) 没卡 → 用同一提炼器补建（构造与 closed 路逐字一致），直接 approved
    from ai.core.solution_distiller import distill_solution

    comments_text, last_human = load_comments_text(tid)
    ticket_type = getattr(row.get("task_type"), "value", row.get("task_type")) or ""
    card = await distill_solution(
        title=row.get("title") or "",
        description=row.get("description") or "",
        # 归档报告是这张单的解法（人写且审核过），拿它当提炼素材
        resolution_summary=report,
        comments_text=comments_text,
        task_type=ticket_type,
    )
    if card is None:
        mark_archived_sunk(tid, "empty")
        logger.info(f"[sink:archive] #{tid} 无卡且提炼为空卡，跳过")
        return "empty"
    resolver = (reviewer
                or _resolver_from_oplog(tid)
                or resolver_name(row.get("assigned_to") or "", last_human))
    logger.info(f"[sink:archive] #{tid} 无卡补建: 问题={card['problem_summary'][:60]} | "
                f"根因={card['root_cause'][:40]} | 解决人={resolver}")
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
            "project_name": row.get("project_name") or "",
            "resolver": resolver,
            "ticket_type": ticket_type,
            "reviewed_by": reviewer,
            "reviewed_at": reviewed_at_str,
            "source": "archive_review",
            "archive_report_version": version,
        },
        # 归档审核通过 = 人工闸门已过，直接 approved（无需再走 review_resolutions）
        review_status="approved",
    )
    if not ok:
        return "failed"
    mark_archived_sunk(tid, "indexed")
    return "indexed"
