"""工单上下文的加载与判断（从 pipeline.py 拆分，独立成模块）

职责：
  - load_task_context: 从 tasks 表读取工单完整上下文（含提单 Agent 的 diagnosis JSON）
  - is_platform_ticket: 判断工单是否属于服务号平台自身问题（非 AGV/USP 调度）
  - build_query: 构件检索查询文本
  - extract_referenced_task_ids / load_referenced_task_context: @# 跨工单引用（L2 注入）

纯数据/静态逻辑，不依赖 AiTaskAgent 实例状态。
"""

import re
from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.schemas import TaskContext

logger = get_logger("TASK_AGENT")


# @# 跨工单引用：匹配 "＠#/@" 后跟 1-8 位数字（工单编号）
# 形如 "@#44123"，用于在讨论区引用另一个工单的上下文。
_REF_PATTERN = re.compile(r"@#(\d{1,8})")
# @# 引用某工单时，最多注入该工单最近多少条讨论评论（非完整历史，仅供参考）
MAX_REF_DISCUSSION_ITEMS = 10

def extract_referenced_task_ids(text: str) -> list:
    """从一段文本（讨论 query）中提取被 @# 引用的工单编号列表（去重、保序）。

    例： "@#44123 你看下他之前那个 #44123 的日志" → ["44123", "44123"]
    注意：不在此过滤"是否已解决/是否存在"，由调用方决定；只负责解析语法。
    """
    if not text:
        return []
    return _REF_PATTERN.findall(text)


def load_referenced_task_context(task_id: str) -> TaskContext:
    """读取被 @# 引用工单的上下文（L2 注入深度：基本信息 + diagnosis）。

    复用 load_task_context 的逻辑（含提单 Agent 的 diagnosis JSON），
    与当前工单的读取完全同构，只是对象换成一个被引用的历史工单。
    """
    return load_task_context(str(task_id))


def format_referenced_tickets(ref_ids: list) -> str:
    """把引用的工单上下文组装成注入 prompt 的文本块（L2 注入深度）。

    内容来源（用 @# 引用一个历史工单时能拿到什么）：
      - 可靠落库：工单基本信息(title/description/status/车型/故障码)
        + 提单 Agent 交付的 diagnosis 摘要(problem_summary/hypotheses，存于 metadata_info.diagnosis)
        + 该工单的讨论区评论(load_discussion，discuss 内容已落库，含 U老师 分析)。
      - ★ 最有价值：最终解决方案（solution，单独字段，存于 metadata_info.diagnosis.solution）——
        工程师确认方案后写入，只有已提交方案的工单有；直接就是"怎么解决"的结论。
      - 不入库、拿不到：任务 Agent「帮我分析」生成的完整诊断报告
        （diagnose 即时生成不落库）；故不注入、也让 LLM 不要假装看到了它。

    若某个编号读不到（不存在/非 AI 源），保留一条可读占位（外层不因单条失败而中断）。
    """
    if not ref_ids:
        return ""
    blocks = []
    for rid in ref_ids:
        try:
            ctx = load_referenced_task_context(rid)
            # 讨论评论已落库（discuss 内容），是被引用工单另一份可靠的"排查进展"来源
            from ai.agents.AiTaskPlatform.contexts.comments import load_discussion as _load_discussion
            discussion = _load_discussion(str(rid), limit=MAX_REF_DISCUSSION_ITEMS)
        except Exception as e:
            logger.warning(f"[ticket_ref] 读取被引用工单 {rid} 失败: {e}")
            ctx = TaskContext(task_id=str(rid))
            discussion = ""
        if not (ctx.title or ctx.description or ctx.problem_summary) and not discussion and not ctx.solution:
            # 读不到内容 → 明确提示，避免 LLM 编造被引用工单内容
            blocks.append(
                f"- 工单 #{rid}: （未能读取到该工单的上下文，可能是历史工单或权限之外，请如实告知用户）"
            )
            continue
        diag = f"报告: {ctx.problem_summary}" if ctx.problem_summary else ""
        if ctx.hypotheses:
            diag += f"；推测: {' / '.join(ctx.hypotheses)}"
        parts = [
            f"- 工单 #{rid}《{ctx.title or '(无标题)'}》",
            f"  状态: {ctx.status or '未知'}；类型: {ctx.task_type or 'problem'}",
            f"  车型: {ctx.robot_type or '未知'}；故障码: {ctx.fault_code or '无'}",
        ]
        if ctx.description:
            parts.append(f"  描述: {ctx.description[:200]}")
        if diag:
            parts.append(f"  {diag}")
        # 最终解决方案：优先 diagnosis.solution（结构化方案）；
        # 其次工程师结束工单填写的 resolution_summary（真实生产字段）。
        sol_text = _format_solution(ctx.solution)
        if not sol_text and ctx.resolution_summary:
            sol_text = ctx.resolution_summary
        if sol_text:
            parts.append(f"  解决方式:\n{sol_text}")
        if ctx.ai_summary:
            parts.append(f"  AI 摘要: {ctx.ai_summary}")
        if discussion:
            parts.append(f"  该工单近期讨论（最近 {MAX_REF_DISCUSSION_ITEMS} 条，非完整历史）:\n{discussion}")
        blocks.append("\n".join(parts))
    return "## 引用的历史工单上下文（@# 引用，仅作参考，勿喧宾夺主）\n" + "\n".join(blocks)


def _format_solution(solution) -> str:
    """把 solution 字段（dict）格式化成可读文本，返回空串表示无解决方案。

    兼容两种形态：
      - dict：{root_cause_analysis, suggested_actions[], references[], confidence, needs_more_info, resolved_by_agent}
      - str：直接作为解决方式文本（少见，防御性兼容）
    """
    if isinstance(solution, str):
        return solution
    if not solution or not isinstance(solution, dict):
        return ""
    lines = []
    root = solution.get("root_cause_analysis") or solution.get("root_cause") or ""
    if root:
        lines.append(f"    根因: {root}")
    acts = solution.get("suggested_actions") or []
    # 兼容结构不同（可能是字符串或 dict 列表）
    if acts:
        act_lines = []
        for a in acts:
            if isinstance(a, str):
                act_lines.append(f"      - {a}")
            elif isinstance(a, dict) and a.get("action"):
                act_lines.append(f"      - {a['action']}")
        if act_lines:
            lines.append("    建议步骤:\n" + "\n".join(act_lines[:8]))
    conf = solution.get("confidence")
    if conf:
        lines.append(f"    置信度: {conf}")
    return "\n".join(lines)


def load_task_context(task_id: str) -> TaskContext:
    """从 tasks 表读取工单上下文（source='ai' 任务）。

    AI 专属字段（diagnosis/robot_type/fault_code 等）存于 metadata_info。
    priority 由适配层反向映射回中文，保持 LLM prompt 输入不变。
    """
    ctx = TaskContext(task_id=task_id)

    try:
        from ai.core.task_adapter import load_task_context_dict
        d = load_task_context_dict(task_id)
        if d:
            ctx.title = d.get("title", "")
            ctx.description = d.get("description", "")
            ctx.task_type = d.get("type", "problem") or "problem"
            ctx.priority = d.get("priority", "中") or "中"
            ctx.status = d.get("status", "pending") or "pending"
            ctx.source = d.get("source", "ai") or "ai"
            ctx.attachments = d.get("attachments") or []
            ctx.attachment_analysis = d.get("attachment_analysis") or {}
            ctx.robot_type = d.get("robot_type", "")
            ctx.fault_code = d.get("fault_code", "")
            ctx.location = d.get("location", "")

            # diagnosis JSON — 提单 Agent 的诊断结果（核心材料）
            ctx.problem_summary = d.get("problem_summary", "")
            ctx.hypotheses = d.get("hypotheses") or []
            ctx.ruled_out = d.get("ruled_out") or []
            ctx.collected_info = d.get("collected_info") or {}
            ctx.diagnosis_rounds = d.get("diagnosis_rounds", 0)

            # 最终解决方案（工程确认方案后写入 metadata_info.diagnosis.solution）
            # 只有已提交过方案的工单有；是"参考怎么解决"最有价值的内容。
            _diag = d.get("diagnosis") or {}
            if _diag.get("solution"):
                ctx.solution = _diag.get("solution")

            # 工程师结束工单时填写的解决方式（metadata_info.resolution_summary，纯字符串）：
            # 这是生产实际写入的"怎么解决"字段（@# 引用主要靠它），与 diagnosis.solution 并存。
            ctx.resolution_summary = d.get("resolution_summary") or ""
            # AI 讨论摘要（metadata_info.ai_summary），作为"怎么解决"的补充说明
            ctx.ai_summary = d.get("ai_summary") or ""
        else:
            logger.warning(f"Task {task_id} not found in database (load_task_context_dict returned empty)")
    except Exception as e:
        logger.warning(f"Failed to load task {task_id}: {e}")

    return ctx


# ── 服务号平台项目标记（问题描述里用【】括起的项目名，命中即视为平台工单）──
# 例如描述: 【摇人吧服务号提单】用户希望调整...
_PLATFORM_PROJECT_MARKERS = (
    "服务号",      # 覆盖 摇人吧服务号 / XXXX服务号提单
    "摇人吧",
)


def is_platform_ticket(context: TaskContext) -> bool:
    """判断工单是否属于服务号平台自身问题（非 AGV/USP 调度）。

    依据：工单问题描述/标题里用【】括起来的"所属项目"标注。
    项目标准名如「摇人吧服务号」，命中即视为服务号平台工单，无需关键词拆解正文。
    """
    text = " ".join(filter(None, [
        context.description or "",
        context.title or "",
    ]))
    # 只检查 【...】 括起的项目标注部分，避免正文无关内容误判
    for scope in _find_project_annotations(text):
        if any(marker in scope for marker in _PLATFORM_PROJECT_MARKERS):
            return True
    return False


def _find_project_annotations(text: str) -> list:
    """提取文本中所有【...】括起的内容（项目标注）。"""
    import re
    return re.findall(r"【([^】]+)】", text)


def build_query(context: TaskContext) -> str:
    """构建检索查询文本。"""
    parts = []
    if context.problem_summary:
        parts.append(context.problem_summary)
    elif context.description:
        parts.append(context.description)
    if context.hypotheses:
        parts.append(" ".join(context.hypotheses))
    if context.fault_code:
        parts.append(context.fault_code)
    if context.robot_type:
        parts.append(context.robot_type)
    return " ".join(parts) if parts else (context.description or "")


def build_task_ctx(context: TaskContext) -> dict:
    """组装日志子 Agent 的 task_ctx（disagnose/discuss 共用）。"""
    return {
        "title": context.title,
        "description": context.description,
        "problem_summary": context.problem_summary,
        "hypotheses": context.hypotheses,
        "ruled_out": context.ruled_out,
        "robot_type": context.robot_type,
        "fault_code": context.fault_code,
        "collected_info": context.collected_info,
    }


def build_img_ctx(context: TaskContext) -> dict:
    """组装图片分析的 img_ctx（diagnose/discuss 共用）。"""
    return {
        "title": context.title,
        "description": context.description,
        "problem_summary": context.problem_summary,
        "hypotheses": context.hypotheses,
        "fault_code": context.fault_code,
        "robot_type": context.robot_type,
    }

