# -*- coding: utf-8 -*-
"""工单知识提炼器：评论串 + resolution_summary → 结构化知识卡。

背景（0828 用户实锤）：resolution_summary 很多人填得敷衍（「已解决」「重启
好了」），评论区的处理过程才是主要信息源——两者结合过一次 LLM 提炼，产出
「一句话问题 / 根因 / 解决步骤 / 关键实体」，再入 Qdrant 供检索。

0901 移入 ai/core（共享核心）：知识沉淀服务跨平台（数据来自工单平台、
检索服务诊断平台），不属于任何单个 agent 的私有 services。

设计约束：
- 纯函数（字符串进、dict 出），DB 组装与调用方解耦，可单测
- 质量闸门：root_cause 和 solution_steps 都为空 → 返回 None（无知识可提，
  调用方跳过并标记，不进集合不污染）
- 提炼卡是给检索用的知识，不是工单归档：失败的尝试不进解决步骤，
  账号密码和闲聊不进卡片
"""
import json
import re
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("KNOWLEDGE_SINK")

_DISTILL_SYSTEM = (
    "你是知识库编辑，负责把已解决工单的处理记录提炼成可检索的知识卡。\n"
    "输入是一张工单的标题、问题描述、工程师填写的解决方式（可能敷衍，如"
    "「已解决」「重启好了」，仅供参考），以及处理过程的评论记录（主要信息源）。\n\n"
    "# 提炼规则\n"
    "1. solution（解决方式）+ comments（评论）结合判断真实解法：评论里工程师"
    "实际做了什么、最后哪一步生效，比敷衍的解决方式字段更可信\n"
    "2. 解决步骤只写最终有效的做法（按顺序分号连接）；中途失败的尝试不写——"
    "除非「排除了XX」本身就是关键诊断线索（此时并入 root_cause）\n"
    "3. 根因写清「什么坏了/什么原因导致现象」，一两句话；问题确实没查清就"
    "解决的（重启就好），root_cause 如实写「未定位，重启恢复」\n"
    "4. problem_summary 用一句话概括故障现象（脱离本工单语境也能看懂，"
    "如「潜伏式搬运车在充电桩报 E207 后无法继续任务」而不是「车辆报错」）\n"
    "5. fault_code / robot_type：评论或描述里明确出现才提取，没有就留空字符串，"
    "禁止猜测\n"
    "6. 🔴 账号、密码、IP、密钥、内部人员闲聊一律不进卡片\n"
    "7. 🔴 工单实际没有解决（用户放弃、误报、重复单关闭、只是转交无人处理）"
    "→ problem_summary 之外全部留空（系统会跳过不沉淀）\n\n"
    "# 输出（仅一个 JSON，无其他文字）\n"
    "```json\n"
    '{"problem_summary": "一句话问题", "root_cause": "根因", '
    '"solution_steps": "步骤1；步骤2", "fault_code": "", "robot_type": ""}\n'
    "```"
)


def _clean(s: str, limit: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


async def distill_solution(
    title: str,
    description: str,
    resolution_summary: str,
    comments_text: str,
    llm=None,
) -> Optional[dict]:
    """提炼一张工单的知识卡。

    Args:
        title / description: 工单标题与问题描述
        resolution_summary: 工程师结束工单时填的解决方式（可能敷衍）
        comments_text: 评论记录聚合文本（调用方已按时间排序、截断；
            格式建议「张三：内容」每行一条）
        llm: LLM 客户端（缺省懒加载 get_llm_client）

    Returns:
        {"problem_summary", "root_cause", "solution_steps", "fault_code",
         "robot_type"}，无知识可提返回 None
    """
    user_prompt = (
        f"# 工单\n标题：{_clean(title, 120)}\n"
        f"问题描述：{_clean(description, 600)}\n"
        f"解决方式（工程师填写）：{_clean(resolution_summary, 300) or '（未填）'}\n\n"
        f"# 处理评论（按时间正序）\n{comments_text or '（无评论）'}"
    )
    if llm is None:
        from ai.core.llm import get_llm_client
        llm = await get_llm_client()
    raw = await llm.complete(prompt=user_prompt, system_prompt=_DISTILL_SYSTEM,
                             max_tokens=800, temperature=0, thinking=False)
    try:
        m = re.search(r"\{[\s\S]*\}", raw or "")
        card = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        logger.warning(f"[distill] JSON 解析失败: raw={str(raw)[:120]!r}")
        return None
    card = {
        "problem_summary": _clean(card.get("problem_summary"), 200),
        "root_cause": _clean(card.get("root_cause"), 300),
        "solution_steps": _clean(card.get("solution_steps"), 600),
        "fault_code": _clean(card.get("fault_code"), 40),
        "robot_type": _clean(card.get("robot_type"), 40),
    }
    # 质量闸门：没有真实解法（根因与步骤都空）→ 无知识可提
    if not card["root_cause"] and not card["solution_steps"]:
        logger.info(f"[distill] 无知识可提，跳过: title={title[:40]!r}")
        return None
    # 敷衍检测：解决步骤只是「已解决/重启」类且无根因 → 同样跳过
    if (not card["root_cause"]
            and re.fullmatch(r"(已|已经)?(解决|处理|完成|关闭|重启)(了|好了)?",
                             card["solution_steps"] or "")):
        logger.info(f"[distill] 解法敷衍无信息量，跳过: title={title[:40]!r}")
        return None
    return card
