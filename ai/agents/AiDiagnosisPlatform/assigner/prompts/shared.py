"""R2 与审查共用的工单字段、类型尺子、产品归属、护栏。"""

from __future__ import annotations

from typing import List

from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext

_CURRENT_RULER = {
    "problem": "本单是报障(problem)，按【故障现象】归到职责里负责该故障的部门",
    "bug": "本单是缺陷(bug)，按【故障现象】归到职责里负责该故障的部门",
    "feature": "本单是需求(feature)，无故障现象，按【工单涉及的产品/项目】归到管理该产品的部门",
    "support": "本单是咨询(support)，按【咨询涉及的产品/项目】归到管理该产品的部门",
    "other": "本单类型是其它或未填，现象和产品都看，对得上的归谁",
}


def ticket_type_key(ticket: TicketContext) -> str:
    return (ticket.ticket_type or "other").strip().lower()


def ticket_type_guidance(ticket: TicketContext) -> str:
    """写进 R2 / 审查：两把尺子必须说清楚。"""
    key = ticket_type_key(ticket)
    current = _CURRENT_RULER.get(key, _CURRENT_RULER["other"])
    return (
        "先看工单类型，再用对应尺子判部门（不要把所有工单都当故障）：\n"
        "  - 报障(problem)、缺陷(bug)：看【故障现象】，归到职责里负责该故障的部门\n"
        "  - 需求(feature)、咨询(support)：看【工单涉及的产品/项目】，归到管理该产品的部门\n"
        "  - 其它(other)或未填：现象和产品都看，对得上的归谁\n"
        f"  → {current}\n"
        "若【工单类型】与标题/描述明显不符（例如标成需求但正文是报障），以正文为准重选尺子。"
    )


def dept_product_map() -> str:
    """部门之间的产品层关系。不塞进某一条画像。"""
    return (
        "【产品归属】三个产品分属不同部门，和各部门【负责】【不负责】放在一起对照，不要先单独认层再判部门：\n"
        "  - 车端硬件（车体/电池/电机/轮子/货叉等能拧的、能换的）→ 机器人事业部\n"
        "  - 车端软件（车上跑的：定位/SLAM、雷达、控制器、车载通信）→ 智能移动研究院\n"
        "  - 调度USP（地面侧：任务下发/阻塞、路径规划、锁区、地图路网）→ 智能规划研究院\n"
        "  - 摇人吧服务号（小程序/后台/AI，不是车上软件）→ 智能规划研究院\n"
        "报障/缺陷：工单现象、产品归属、部门画像一起看，互相收束。\n"
        "  「车不动」可能对上硬件、车端软件或调度，用【负责】【不负责】和工单证据一起判断，不要只认一层。\n"
        "  不要因为发生在车上就判硬件，也不要因为提到「调度/定位」就越过画像边界。\n"
        "需求/咨询：产品/项目与部门画像一起对照，归管理该产品且职责对口的部门。\n"
        "交叉单只留一个主导：用户最痛、证据最硬的那一头给 0.80+，另一头 0.3~0.5。"
    )


def dept_common_guardrails() -> str:
    """假设不可信、表面字眼、部门名原样抄、reason 要证据。"""
    return (
        "Agent假设仅供参考，不是答案；与描述或【不负责】冲突时，以描述和画像为准。\n"
        "标题/描述里的表面字眼（定位、电池、调度等）不等于该部门；"
        "先核对【不负责】，排除「看似A实则B」。\n"
        "部门 name 必须从清单里「部门：」后的名称原样复制，禁止自造或简称。\n"
        "reason 必须同时写：工单里的哪句证据 + 所依据的【负责】或【不负责】要点。"
    )


def ticket_fields_block(ticket: TicketContext) -> str:
    """各 Step 共用的【工单】段落。提单 Agent 基础字段只在这里拼。

    倾向接单人 / 原不满意接单人是 Step2 打在人身上的标签，不写进工单段。
    """
    hypotheses = ""
    if ticket.diagnosis_hypotheses:
        hypotheses = "；".join(ticket.diagnosis_hypotheses[:5])
    ruled_out = ""
    if ticket.diagnosis_ruled_out:
        ruled_out = "；".join(ticket.diagnosis_ruled_out[:5])

    extra: List[str] = []
    if ticket.priority:
        extra.append(f"优先级：{ticket.priority}")
    if ticket.location:
        extra.append(f"现场位置：{ticket.location}")
    if ticket.special_notes:
        extra.append(f"特殊说明：{ticket.special_notes}")
    if ticket.scenario or ticket.expected_effect:
        extra.append(f"需求场景：{ticket.scenario or '无'}")
        extra.append(f"预期效果：{ticket.expected_effect or '无'}")
    if ticket.support_type:
        extra.append(f"支持类型：{ticket.support_type}")
    if ticket.severity or ticket.version:
        extra.append(f"严重程度：{ticket.severity or '无'}")
        extra.append(f"版本：{ticket.version or '无'}")
    if ticket.steps_to_reproduce:
        extra.append(f"复现步骤：{ticket.steps_to_reproduce}")
    if ticket.expected_result or ticket.actual_result:
        extra.append(f"预期结果：{ticket.expected_result or '无'}")
        extra.append(f"实际结果：{ticket.actual_result or '无'}")
    if (ticket.fault_code or "").strip():
        extra.append(f"故障码：{ticket.fault_code}")
    if (ticket.robot_type or "").strip():
        extra.append(f"车型：{ticket.robot_type}")
    if ruled_out:
        extra.append(f"Agent已排除：{ruled_out}")
    extra_text = ("\n".join(extra) + "\n") if extra else ""

    return (
        "【工单】\n"
        f"工单类型：{ticket_type_key(ticket)}\n"
        f"标题：{ticket.title or ''}\n"
        f"描述：{ticket.problem_description or ''}\n"
        + extra_text
        + f"项目：{ticket.project_name or '无'}\n"
        + f"Agent假设：{hypotheses or '无'}\n"
    )


def person_anti_hallucination() -> str:
    """看人画像时的反幻觉：可以推断谁能接，但不能编造其职责。"""
    return (
        "【反幻觉】可以推断「这类故障/需求谁能接」，"
        "但依据必须落在该人卡片上已写出的责任模块、职责上；"
        "禁止编造、脑补、补全其未写明的负责内容；"
        "禁止把别人的模块或职责安到此人头上。"
    )


def engineer_brief_lines(eng, duty_max: int = 200) -> List[str]:
    """L1（及需要看人画像的 Step）共用的工程师卡片。"""
    from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import llm_person_label

    bits = [f"职级:L{eng.job_level}"]
    if eng.department:
        bits.append(f"部门:{eng.department}")
    if eng.company:
        bits.append(f"公司:{eng.company}")
    duty = (eng.duty_text or "").strip()
    return [
        f"- {llm_person_label(eng=eng)}",
        "  " + " ".join(bits),
        f"  责任模块:{eng.modules_display() or '无'}",
        f"  职责:{duty[:duty_max] if duty else '无'}",
    ]
