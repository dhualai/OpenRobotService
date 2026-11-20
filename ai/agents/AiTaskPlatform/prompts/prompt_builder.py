"""Prompt 组装工具（从 pipeline.py 拆分纯逻辑，不依赖 self）

职责：
  - build_user_prompt: 组装 USER_PROMPT_TEMPLATE（v2.x 遗留方案生成 prompt）

纯数据组装，不依赖 AiTaskAgent 实例状态。
"""

import json

from ai.agents.AiTaskPlatform.schemas import TaskContext
from ai.agents.AiTaskPlatform.prompts import USER_PROMPT_TEMPLATE


def build_user_prompt(context: TaskContext, retrieval: dict) -> str:
    """组装 USER_PROMPT_TEMPLATE（v2.x 遗留：方案生成）。"""
    # 故障信息行
    fault_parts = []
    if context.fault_code:
        fault_parts.append(f"故障码: {context.fault_code}")
    if context.robot_type:
        fault_parts.append(f"车型: {context.robot_type}")
    if context.location:
        fault_parts.append(f"位置: {context.location}")
    fault_info = "\n".join(fault_parts) if fault_parts else "（无特殊故障信息）"

    # 附件分析摘要
    att = retrieval.get("attachment_analysis", {})
    att_dict = att.model_dump() if hasattr(att, 'model_dump') else (att or {})
    if att_dict.get("has_logs") or att_dict.get("has_replay"):
        attachment_text = json.dumps(
            {k: v for k, v in att_dict.items() if v},
            ensure_ascii=False, indent=2
        )
    else:
        attachment_text = "（无附件或无可解析内容）"

    return USER_PROMPT_TEMPLATE.format(
        title=context.title,
        description=context.description,
        task_type=context.task_type,
        priority=context.priority,
        source=context.source or "unknown",
        problem_summary=context.problem_summary or "（提单 Agent 未提供）",
        hypotheses="、".join(context.hypotheses) if context.hypotheses else "（无）",
        ruled_out="、".join(context.ruled_out) if context.ruled_out else "（无）",
        collected_info=json.dumps(context.collected_info, ensure_ascii=False)
        if context.collected_info else "（无）",
        rounds=context.diagnosis_rounds,
        fault_info=fault_info,
        platform_reference=retrieval.get("platform_reference")
        or "（服务号平台参考文档检索未执行，非平台问题跳过）",
        troubleshooting_conclusions=retrieval.get("troubleshooting", "（排查树检索未执行）"),
        historical_solutions=retrieval.get("history", "（历史方案检索未执行）"),
        attachment_analysis=attachment_text,
    )
