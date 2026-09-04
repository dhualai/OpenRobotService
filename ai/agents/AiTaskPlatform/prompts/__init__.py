"""prompts — 提示词域：诊断/讨论/摘要/方案等 prompt 模板与组装工具。"""
from ai.agents.AiTaskPlatform.prompts.prompts import (
    DIAGNOSE_SYSTEM_PROMPT,
    DIAGNOSE_USER_TEMPLATE,
    DISCUSS_SYSTEM_PROMPT,
    DISCUSS_USER_TEMPLATE,
    DISCUSS_LIGHT_SYSTEM_PROMPT,
    DISCUSS_LIGHT_USER_TEMPLATE,
    SUMMARIZE_SYSTEM_PROMPT,
    SUMMARIZE_FULL_TEMPLATE,
    SUMMARIZE_INCREMENTAL_TEMPLATE,
    TASK_AGENT_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    # 结束工单：问题解决方式总结（一句话）
    RESOLUTION_SYSTEM_PROMPT,
    RESOLUTION_FULL_TEMPLATE,
    RESOLUTION_INCREMENTAL_TEMPLATE,
    # U老师（摇人吧服务号项目）角色
    U_TEACHER_SYSTEM_PROMPT,
    U_TEACHER_DISCUSS_SYSTEM_PROMPT,
    U_TEACHER_SUMMARIZE_SYSTEM_PROMPT,
)
from ai.agents.AiTaskPlatform.prompts.prompt_builder import build_user_prompt

# 按工单是否服务号（摇人吧）场景，选择对应角色的 system prompt。
# is_plate_ticket=True → U老师（资深代码高手，不限 AGV/调度）
# is_plate_ticket=False → AGV/调度等领域专家
def select_system_prompt(is_plate_ticket: bool, module: str = "diagnose") -> str:
    if is_plate_ticket:
        return {
            "diagnose": U_TEACHER_SYSTEM_PROMPT,
            "discuss": U_TEACHER_DISCUSS_SYSTEM_PROMPT,
            "summarize": U_TEACHER_SUMMARIZE_SYSTEM_PROMPT,
        }.get(module, U_TEACHER_SYSTEM_PROMPT)
    return {
        "diagnose": DIAGNOSE_SYSTEM_PROMPT,
        "discuss": DISCUSS_SYSTEM_PROMPT,
        "summarize": SUMMARIZE_SYSTEM_PROMPT,
    }.get(module, DIAGNOSE_SYSTEM_PROMPT)


__all__ = [
    "DIAGNOSE_SYSTEM_PROMPT", "DIAGNOSE_USER_TEMPLATE",
    "DISCUSS_SYSTEM_PROMPT", "DISCUSS_USER_TEMPLATE",
    "DISCUSS_LIGHT_SYSTEM_PROMPT", "DISCUSS_LIGHT_USER_TEMPLATE",
    "SUMMARIZE_SYSTEM_PROMPT", "SUMMARIZE_FULL_TEMPLATE",
    "SUMMARIZE_INCREMENTAL_TEMPLATE", "TASK_AGENT_SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE", "build_user_prompt",
    "RESOLUTION_SYSTEM_PROMPT", "RESOLUTION_FULL_TEMPLATE",
    "RESOLUTION_INCREMENTAL_TEMPLATE",
    "U_TEACHER_SYSTEM_PROMPT", "U_TEACHER_DISCUSS_SYSTEM_PROMPT",
    "U_TEACHER_SUMMARIZE_SYSTEM_PROMPT", "select_system_prompt",
]

