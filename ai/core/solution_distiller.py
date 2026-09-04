# -*- coding: utf-8 -*-
"""工单知识提炼器：评论串 + resolution_summary → 结构化知识卡。

背景（0828 用户实锤）：resolution_summary 很多人填得敷衍（「已解决」「重启
好了」），评论区的处理过程才是主要信息源——两者结合过一次 LLM 提炼，产出
「一句话问题 / 根因 / 解决步骤 / 关键实体」，再入 Qdrant 供检索。

0901 移入 ai/core（共享核心）：知识沉淀服务跨平台（数据来自工单平台、
检索服务诊断平台），不属于任何单个 agent 的私有 services。

设计约束：
- 纯函数（字符串进、dict 出），DB 组装与调用方解耦，可单测
- 质量闸门：solution_steps 为空或敷衍话术 → 返回 None（无处理人解法，
  调用方跳过并标记，不进集合不污染）
- 提炼卡是给检索用的知识，不是工单归档：失败的尝试不进解决步骤，
  账号密码和闲聊不进卡片
- 0902 人工审核 240 张实锤的规则：测试单不沉淀、无处理人解法不沉淀、
  功能/缺陷类默认无复用价值（含配置/版本/排查类知识除外）、问题/根因/
  步骤三字段禁止互相掺杂
"""
import json
import os
import re
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("KNOWLEDGE_SINK")

# 0903 用户拍板：distiller 用 deepseek-v4-pro。判定边界（跳过/提炼的软判断）
# 是 flash 摇摆重灾区（0902 实锤同 prompt 同输入两次结果不同，temperature=0
# 也不确定），backfill/worker 低频调用，稳定性优先于成本。独立客户端，
# 不动全局单例。换回/换别的模型改 SOLUTION_DISTILL_MODEL。
_DISTILL_MODEL = os.getenv("SOLUTION_DISTILL_MODEL", "deepseek-v4-pro")
_distill_llm = None


async def _get_distill_llm():
    global _distill_llm
    if _distill_llm is None:
        from ai.core.llm import LLMClient, LLMProvider
        backend = (os.getenv("LLM_BACKEND") or "deepseek").strip().lower()
        provider = (LLMProvider.RELAY if backend == "relay"
                    else LLMProvider.OPENAI if backend == "openai"
                    else LLMProvider.DEEPSEEK)
        _distill_llm = LLMClient(provider=provider, model=_DISTILL_MODEL)
    return _distill_llm

_DISTILL_SYSTEM = (
    "你是知识库编辑，负责把已解决工单的处理记录提炼成可检索的知识卡。\n"
    "知识卡的用途：给现场设备/调度系统的诊断问答做检索——未来有客户或"
    "工程师遇到同类问题时，靠这张卡找到排查方向。判定每张卡值不值得留，"
    "始终以「对现场问题的诊断问答有没有用」为标尺。\n"
    "背景事实：这些工单由一个 AI 客服平台（服务号+工单系统）受理，平台"
    "自身不是被诊断的对象——工单里出现的界面、跳转、显示、按钮、服务号、"
    "升级重启等字眼指向平台自身时，那是受理系统的事，不是现场知识。\n"
    "输入是工单的标题、类型（参考信号）、问题描述、工程师填写的解决方式"
    "（可能敷衍，仅供参考），以及处理人的评论记录（AI 评论已剔除）。\n\n"
    "# 提炼规则\n"
    "1. 解决方式是处理人结案时写的结论：写得具体时以它为准；敷衍时"
    "（只写「已解决」类），以评论里工程师实际做的、最后生效的处理为准\n"
    "2. 咨询/确认类工单（问某功能如何配置、是否支持、如何工作）：专家给出"
    "的答案就是知识——solution_steps 写答案结论和所需配置/操作，「无需任何"
    "操作、系统自动完成」也是有效结论，照写；root_cause 写原理性原因"
    "（为什么会这样），没有就留空\n"
    "3. 解决步骤只写最终有效的做法（按顺序分号连接）；中途失败的尝试不写——"
    "除非「排除了XX」本身就是关键诊断线索（此时并入 root_cause）\n"
    "4. 根因写清「什么坏了/什么原因导致现象」，一两句话；问题确实没查清就"
    "解决的（重启就好），root_cause 如实写「未定位，重启恢复」\n"
    "5. 三个字段各司其职，禁止互相掺杂：\n"
    "   - problem_summary 只写故障现象或疑问本身，一句话、简洁，脱离本工单"
    "语境也能看懂（含设备类型、错误码等关键实体，但不写「车辆报错」这类"
    "笼统说法）；🔴 禁止把原因、排查结论、解决办法写进问题；咨询类工单的"
    "问题就是问题本身（形如「如何配置某参数」「某功能是否支持某行为」），"
    "不要写成背景陈述\n"
    "   - root_cause 只写原因，不写处理动作\n"
    "   - solution_steps 只写处理动作，不重复原因\n"
    "6. fault_code / robot_type：评论或描述里明确出现才提取，没有就留空字符串，"
    "禁止猜测\n"
    "7. 🔴 账号、密码、IP、密钥、内部人员闲聊一律不进卡片\n"
    "8. 🔴 以下情况除 problem_summary 外全部留空（系统会跳过不沉淀）：\n"
    "   a. 工单实际没有解决（用户放弃、误报、重复单关闭、只是转交无人处理）\n"
    "   b. 处理人没有给出真实解法——解决方式是无信息量话术（已解决/处理了"
    "类）或缺失，评论里也没有人给出有效处理；简短但有明确归因或结论的"
    "（如版本归因）不算敷衍；禁止根据问题描述推测解法或强行凑内容\n"
    "   c. 测试/验证类工单：标题、问题描述、结案总结或评论里任何位置出现"
    "本单是测试、验证流程、试用、提交测试工单的信号，一律按测试单跳过——"
    "哪怕其他内容看着像真实故障；注意：项目名/地点里带的「测试」字样不算"
    "信号，只看对人、对本单的定性（0903 #336 实锤：信号藏在问题描述里，"
    "不能只盯结案和评论）\n"
    "   d. 类型为缺陷(bug)/功能需求(feature)，且解法只是修复/开发产品自身"
    "（修改产品代码或页面，已完成）——自问：其他项目或客户遇到同样问题时，"
    "照这条解法能自己处理吗？不能（只能等开发改码）就留空跳过；解法含使用者"
    "可复用成分（配置方法、版本升级、参数设置、操作流程、排查思路、根因定位）"
    "时照常提炼——类型只是参考信号，以实际内容为准；明确归因到版本的（旧版本"
    "问题、新版本已修复）算可复用，steps 写升级到已修复版本\n"
    "   e. 问题的对象是受理平台自身（工单系统/服务号/页面），不是现场的"
    "设备、车辆、调度、业务系统：界面显示异常、页面功能缺陷、跳转报错、"
    "时间显示不对、升级重启期间的临时不可见、询问平台某按钮入口在哪——"
    "解法是修改平台前端/函数代码、等待平台升级完成、或指引去平台内某"
    "入口操作的，一律属于此类，全部留空跳过\n"
    "   f. 类型为功能需求(feature)或工单内容是提需求、讨论需求的：解法"
    "只是进度或决策终态（需求已排期、转产品评估、确认无此需求、由后续"
    "版本开发实现、与产品经理讨论），不含使用者当下可执行的排查或操作"
    "→ 跳过。注意区分：设备/系统使用类的咨询（某功能是否自动完成、是否"
    "支持某行为、需不需要手动干预）是有效知识——前提是答案落在使用者可"
    "依赖的能力结论或替代做法上，照常提炼；若答案的落点是需求去向（当前"
    "不支持、已立需求、待排期开发）且没给替代做法，按本条跳过（0903 "
    "#461 实锤）\n"
    "   🔴 以上跳过规则只用于「明确属于」的情形；介于两者之间拿不准时，"
    "倾向正常提炼出卡（后续有人工审核兜底，误跳过会直接丢知识）\n"
    "   🔴🔴 出卡前最后自检，逐条过，任一命中即整卡跳过——跳过规则的"
    "优先级高于一切提炼价值：跳过只是不入知识库，工单本身仍留在系统里"
    "可查，不会丢知识（0903 生产实锤 #638/#612/#521：规则 e/f 已明写仍"
    "出卡，靠这最后一道机械核对兜住）：\n"
    "   ① 这张卡未来会被谁检索到？只有现场设备/调度侧的人遇到同类问题"
    "时会查；只在「平台自身出毛病/等平台恢复/需求做不做」的场景下才有"
    "用的，回规则 e/f\n"
    "   ② 逐字看你写的 solution_steps，它的实质是否为以下任一：进度或"
    "决策终态（已排期/转评估/确认无需求/由开发实现/当前不支持已立需求）、"
    "等待平台升级或重启完成、教用户在受理平台的界面里找某按钮或入口、"
    "一句流程审核结论（「审核通过」式）？是 → 回规则 e/f\n"
    "   ③ 复查一遍全部素材：标题/问题描述/结案总结/评论里出现过对本单"
    "的测试定性吗（项目名里的「测试」字样不算）？有 → 回规则 c\n\n"
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
    task_type: str = "",
) -> Optional[dict]:
    """提炼一张工单的知识卡。

    Args:
        title / description: 工单标题与问题描述
        resolution_summary: 工程师结束工单时填的解决方式（可能敷衍）
        comments_text: 评论记录聚合文本（调用方已按时间排序、截断、剔除 AI
            评论；格式建议「张三：内容」每行一条）
        llm: LLM 客户端（缺省懒加载 get_llm_client）
        task_type: 工单类型（BUG/FEATURE/PROBLEM/SUPPORT/OTHER，参考信号：
            功能/缺陷类大概率无复用价值，但以实际内容为准）

    Returns:
        {"problem_summary", "root_cause", "solution_steps", "fault_code",
         "robot_type"}，无知识可提返回 None
    """
    user_prompt = (
        f"# 工单\n标题：{_clean(title, 120)}\n"
        f"类型：{task_type or '（未标）'}\n"
        f"问题描述：{_clean(description, 600)}\n"
        f"解决方式（工程师填写）：{_clean(resolution_summary, 300) or '（未填）'}\n\n"
        f"# 处理评论（按时间正序）\n{comments_text or '（无评论）'}"
    )
    if llm is None:
        llm = await _get_distill_llm()
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
    # 质量闸门：处理人没给出真实解法（步骤为空 / 敷衍话术）→ 无知识可提。
    # 0902 人工审核实锤：无解决步骤的卡（#604）和敷衍卡都不该入库
    if not card["solution_steps"] or re.fullmatch(
            r"(已|已经)?(解决|处理|完成|关闭|重启)(了|好了)?",
            card["solution_steps"]):
        logger.info(f"[distill] 无处理人解法，跳过: title={title[:40]!r}")
        return None
    return card
