"""提单工具（submit_ticket）——阶段 1 提单工具化核心。

参考 Pi Agent 的 AgentTool 模式：
- 工具 schema（TOOL_SCHEMA）给 LLM 看，约束输入结构
- 执行器是纯函数（execute_submit_ticket），无 LLM 参与，可单测
- 返回值双通道：content（回填给 LLM 看的文本）+ details（结构化，UI/日志用）

流程（工具循环中）：
  LLM 判断用户要提单 → 调 submit_ticket(ticket_type, problem_summary, collected_fields)
  → 执行器按 required_fields 判缺：
      缺字段 → {content: "还缺：xx、yy", details: {status: collecting, missing}}
                LLM 收到后自己组织追问（不再有服务端抢话）
      字段齐 → 生成 draft → {content: "草稿已生成", details: {status: draft_ready, draft}}
                循环终止 → 发 review 事件弹窗（前端无感知）

和旧状态机的区别：
- 字段清单由 LLM 在工具参数里声明（collected_fields），不是服务端 decide 决定
- 「判缺」是工具执行的结果，不是服务端拦截
- 没有 ticket_collecting / required_fields 三态 / backfill 幻觉
"""
from typing import Any, Dict, List, Optional


# ── 给 LLM 看的工具 schema（OpenAI function-calling 格式，DeepSeek 兼容）──
TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_ticket",
        "description": (
            "用户表达提单诉求（转工单/提单/派单/找工程师处理）时调用。\n"
            "调用前先思考：工程师接单后还需要哪些关键信息？把它们声明在 required_fields。\n"
            "调用后如果工具返回「还缺哪些信息」，**不要立刻再次调用**——直接以纯文本"
            "一句话向用户追问缺的那一项（不要复述已记录的信息），用户补充后再次调用；\n"
            "全部提供后工具返回草稿，提单流程结束。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_type": {
                    "type": "string",
                    "enum": ["problem", "bug", "feature", "support", "other"],
                    "description": "工单类型：problem=报障/bug=缺陷/feature=需求/support=咨询/other",
                },
                "problem_summary": {
                    "type": "string",
                    "description": "问题一句话概述（用户提单诉求对应的具体问题）",
                },
                "required_fields": {
                    "type": "object",
                    "description": (
                        "工程师接单后还需要的关键信息，**必须包含至少 1 个字段**（禁止空对象）。\n"
                        "key 为英文标识，value 为中文标签。\n"
                        "只列对话里确实还没提到的信息；已经说过的、能推出的、项目名称都不列。\n"
                        "即使用户的问题已经很清楚，也要从对话里找出至少 1 个对工程师有用的补充信息。"
                    ),
                    "additionalProperties": {"type": "string"},
                    "minProperties": 1,
                },
                "collected_fields": {
                    "type": "object",
                    "description": (
                        "用户已经提供的字段值（key 与 required_fields 对应，value 为用户原话）。\n"
                        "例如 {\"device_info\": \"自动门1号\"}。用户没提供的字段不要写。"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "project_choice": {
                    "type": "string",
                    "description": (
                        "仅当系统提示中给出了「用户名下项目」列表、且用户在对话中明确提到"
                        "要给其中某个项目提单时，把该项目名称从列表里**原样照抄**到这里。\n"
                        "没提到项目、没有列表、或对不上列表某一项时，一律省略本参数。\n"
                        "禁止填列表之外的值，禁止猜测，禁止追问用户项目名称。"
                    ),
                },
                "requested_assignee": {
                    "type": "string",
                    "description": "用户指名处理人（「提给张三」「交给张三」），没有则省略",
                },
            },
            "required": ["ticket_type", "problem_summary", "required_fields"],
        },
    },
}


# ── 补充轮 schema：草稿已存在，用户在补充/修改信息 ──
# 首次提单（TOOL_SCHEMA）时已经把所需信息问齐才生成的草稿，补充轮通常不会再
# 冒出新的必需项。若沿用 TOOL_SCHEMA 的 minProperties:1 强制声明，LLM 会被迫
# 在没有真正缺项时随手编一个字段，污染 required_fields（历史 bug：补充「派单给
# 贾爽」时随手声明一个未收集的新字段，导致确认提交时又被拦下）。这里去掉该
# 约束——required_fields 变成可选，只有真的出现全新缺口才声明。
TOOL_SCHEMA_SUPPLEMENT: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_ticket",
        "description": (
            "工单草稿已生成，用户在给草稿补充/修改信息（如「提给张三」「补充一下XX」）"
            "时调用，把新内容放进 collected_fields 即可。\n"
            "**通常不需要声明 required_fields**——首次提单时已经问齐所需信息才生成"
            "的草稿，补充轮一般没有新的必需缺口。只有确实发现一个此前完全没问过、"
            "工程师必须要的信息时，才在 required_fields 里声明这一个新字段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticket_type": {
                    "type": "string",
                    "enum": ["problem", "bug", "feature", "support", "other"],
                    "description": "工单类型：problem=报障/bug=缺陷/feature=需求/support=咨询/other",
                },
                "problem_summary": {
                    "type": "string",
                    "description": "问题一句话概述（用户提单诉求对应的具体问题）",
                },
                "required_fields": {
                    "type": "object",
                    "description": (
                        "通常不用传。只有真的出现一个从未问过的新必需项时才声明这一个"
                        "字段，key 为英文标识，value 为中文标签。"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "collected_fields": {
                    "type": "object",
                    "description": (
                        "本轮用户新补充的字段值（key 为英文标识，value 为用户原话）。\n"
                        "例如 {\"requested_assignee\": \"张三\"}。只写本轮新增的，不用复述之前已收集的。"
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "project_choice": {
                    "type": "string",
                    "description": (
                        "仅当系统提示中给出了「用户名下项目」列表、且用户提到要给其中某个"
                        "项目提单时，把该项目名称从列表里**原样照抄**到这里。\n"
                        "没提到项目、没有列表、或对不上列表某一项时，一律省略本参数。\n"
                        "禁止填列表之外的值，禁止猜测，禁止追问用户项目名称。"
                    ),
                },
                "requested_assignee": {
                    "type": "string",
                    "description": "用户指名处理人（「提给张三」「交给张三」），没有则省略",
                },
            },
            "required": ["ticket_type", "problem_summary"],
        },
    },
}


# ── 执行结果结构 ──
def make_collecting_result(missing: List[str]) -> Dict[str, Any]:
    """字段缺失：告诉 LLM 还缺什么，让它继续追问。

    防重复：直接要求只问缺的那一项、不复述已确认信息。LLM 上一轮输出已复述过
    已收集内容 + 追问，工具返回若不加约束，下一轮 LLM 常再次完整复述（重复的根源）。
    """
    missing_text = "、".join(missing)
    return {
        "content": (
            f"工单信息不足，还缺：{missing_text}。"
            "请直接只问还缺的一项（自然语气一句话），不要再复述/重复你已经说过的"
            "已确认信息或解释。用户补充后再调用。"
        ),
        "details": {
            "status": "collecting",
            "missing": missing,
        },
        "terminate": False,
    }


def make_draft_result(draft: Dict[str, Any]) -> Dict[str, Any]:
    """字段齐全：草稿已生成，循环终止。"""
    return {
        "content": "工单草稿已生成。",
        "details": {
            "status": "draft_ready",
            "draft": draft,
        },
        "terminate": True,
    }


# ── 字段校验 ──
# LLM 在 required_fields 里声明「还需要哪些信息」，执行器只校验声明 vs 已收集。
# 与旧 decide 的职责同源（都是 LLM 判断缺什么），但不再有服务端清单锁定/三态/
# 近义词 key 问题：声明和收集在同一轮工具参数里，key 由 LLM 自己保持一致。


def execute_submit_ticket(
    params: Dict[str, Any],
    make_draft: Optional[Any] = None,
) -> Dict[str, Any]:
    """submit_ticket 工具执行器（纯函数）。

    params: LLM 调用参数 {ticket_type, problem_summary, required_fields,
                          collected_fields, requested_assignee}。
      调用方（pipeline._ticket_tool_loop_branch）在补充轮会把跨轮累计的
      state.collected_info 一并合并进 collected_fields 再传进来，所以这里
      始终只需要「声明 vs 已收集」的单轮比对，不用关心是否补充轮——
      已经在之前轮次收集过的字段，合并后天然视为已收集。
    make_draft: 草稿生成函数（可选，测试时可注入 mock；默认生成最小草稿）。

    返回：{content, details, terminate}，见 make_collecting_result/make_draft_result。
    """
    ticket_type = (params.get("ticket_type") or "other").strip()
    if ticket_type not in ("problem", "bug", "feature", "support", "other"):
        ticket_type = "other"
    required = params.get("required_fields") or {}
    collected = params.get("collected_fields") or {}
    if not isinstance(required, dict):
        required = {}
    if not isinstance(collected, dict):
        collected = {}

    # 声明 vs 已收集：声明的 key 没在 collected 里给值 → 缺。
    missing: List[str] = []
    for field_key, label in required.items():
        _v = (collected.get(field_key) or "").strip()
        if not _v or _v in ("无", "没有", "不知道", "不清楚"):
            # 「无/没有」也算有效回答（用户明确说没有），视为已收集
            if _v in ("无", "没有", "不知道", "不清楚"):
                continue
            missing.append(str(label) if label else str(field_key))
    if missing:
        return make_collecting_result(missing)

    # 声明全部满足：生成草稿
    draft = None
    if make_draft is not None:
        draft = make_draft(params)
    else:
        draft = {
            "type": ticket_type,
            "title": (params.get("problem_summary") or "工单")[:20],
            "description": params.get("problem_summary", ""),
            "priority": "中",
            "project": "",
            "project_id": "",
            "special_notes": "",
        }
    return make_draft_result(draft)
