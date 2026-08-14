"""工单上下文的加载与判断（从 pipeline.py 拆分，独立成模块）

职责：
  - load_task_context: 从 tasks 表读取工单完整上下文（含提单 Agent 的 diagnosis JSON）
  - is_platform_ticket: 判断工单是否属于服务号平台自身问题（非 AGV/USP 调度）
  - build_query: 构件检索查询文本

纯数据/静态逻辑，不依赖 AiTaskAgent 实例状态。
"""

from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.schemas import TaskContext

logger = get_logger("TASK_AGENT")


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

