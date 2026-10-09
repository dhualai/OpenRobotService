"""R2 与审查共用的工单字段、类型尺子、产品归属、护栏。"""

from __future__ import annotations

from typing import List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    COLLECTED_TO_TICKET,
    TicketContext,
    collected_value,
    dispatch_hint_text,
)

# 已映射到工单栏的不再重复；指名/项目不进工单段。
_SKIP_COLLECTED_KEYS = frozenset(COLLECTED_TO_TICKET) | {
    "requested_assignee",
    "project",
    "project_id",
    "ticket_type",
}
_COLLECTED_LABELS = {
    "occurrence_time": "故障时间",
    "frequency": "出现频率",
    "page_module": "页面模块",
}

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
        "【多义现象·证据优先级】（「车不动 / 走不了 / 无路径」等，按硬证据收束，勿凭表面字眼）：\n"
        "  1) 调度/锁区侧硬证据优先 → 智能规划研究院：\n"
        "     NO_SOLUTION、路径无解、路径规划失败/超时、锁区、TRAFFIC_LOCK、blocked_edges、\n"
        "     路径拒收、DPP、不在当前所有地图层、拓扑边被封。\n"
        "  2) 车端软件硬证据 → 智能移动研究院：定位丢失、SLAM、雷达异常、控制器报错、车上通信断。\n"
        "  3) 硬件硬证据 → 机器人事业部：电机/电池/轮子/货叉等硬件故障码或可拧部件损坏。\n"
        "  只有笼统「车不动」且无上述证据时，再综合画像，交叉单仍只留一个主导。\n"
        "需求/咨询：产品/项目与部门画像一起对照，归管理该产品且职责对口的部门。\n"
        "交叉单只留一个主导：用户最痛、证据最硬的那一头给 0.80+，另一头 0.3~0.5。"
    )


def dept_common_guardrails() -> str:
    """假设不可信、表面字眼、部门名原样抄、reason 要证据。"""
    return (
        "Agent假设仅供参考，不是答案；与描述或【不负责】冲突时，以描述和画像为准。\n"
        "界面或描述里的状态「路径规划中」是调度 USP 的任务状态，部门是智能规划研究院。"
        "描述中的「AI排查方向」是推测，不能据此把部门改到车端软件或硬件。\n"
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
    summary = (ticket.diagnosis_problem_summary or "").strip()
    title = (ticket.title or "").strip()
    desc = ticket.problem_description or ""
    if summary and summary != title and summary not in desc:
        extra.append(f"诊断摘要：{summary[:200]}")
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
    if ticket.preferred_response:
        extra.append(f"期望响应：{ticket.preferred_response}")
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
    leftover = 0
    info = ticket.diagnosis_collected_info if isinstance(ticket.diagnosis_collected_info, dict) else {}
    for key, raw in info.items():
        name = str(key).strip()
        if leftover >= 8 or name in _SKIP_COLLECTED_KEYS:
            continue
        value = collected_value(raw)
        if not value:
            continue
        extra.append(f"{_COLLECTED_LABELS.get(name, name)}：{value[:120]}")
        leftover += 1
    if ruled_out:
        extra.append(f"Agent已排除：{ruled_out}")
    hint = dispatch_hint_text(getattr(ticket, "dispatch_hint", None))
    if hint:
        extra.append(hint)
    extra_text = ("\n".join(extra) + "\n") if extra else ""

    step_name = (getattr(ticket, "curr_step_name", None) or "").strip()
    step_line = f"当前阶段：{step_name}\n" if step_name else ""

    return (
        "【工单】\n"
        f"工单类型：{ticket_type_key(ticket)}\n"
        + step_line
        + f"标题：{ticket.title or ''}\n"
        f"描述：{ticket.problem_description or ''}\n"
        + extra_text
        + f"项目：{ticket.project_name or '无'}\n"
        + f"Agent假设：{hypotheses or '无'}\n"
    )


def ticket_type_person_guidance(ticket: TicketContext) -> str:
    """Step3 画像召回：与 Step1 同一套工单类型尺子，落到「选人」而不是「选部门」。"""
    key = ticket_type_key(ticket)
    current = {
        "problem": "本单是报障(problem)，按【故障现象】对照谁的责任模块能解决",
        "bug": "本单是缺陷(bug)，按【故障现象】对照谁的责任模块能解决",
        "feature": "本单是需求(feature)，先按下方【仅需求单·产品/研发分流】判断阶段，再对照产品经理或对口研发",
        "support": "本单是咨询(support)，按【咨询涉及的产品/项目】对照产品负责人或功能负责人",
        "other": "本单类型是其它或未填，现象和产品都看，对得上责任模块的人优先",
    }.get(key, "本单类型未填，现象和产品都看，对得上责任模块的人优先")
    return (
        "先看工单类型，再用对应尺子选人（不要把所有工单都当故障）：\n"
        "  - 报障(problem)、缺陷(bug)：看【故障现象】，对照谁的责任模块能解决这类故障\n"
        "  - 需求(feature)：看【工单涉及的产品/项目】，并按【仅需求单·产品/研发分流】选人\n"
        "  - 咨询(support)：看【咨询涉及的产品/项目】，对照产品负责人或对应功能的负责人\n"
        "  - 其它(other)或未填：现象和产品都看，对得上的人优先\n"
        f"  → {current}\n"
        "若【工单类型】与标题/描述明显不符（例如标成需求但正文是报障），以正文为准重选尺子。"
    )


# 需求单 task_steps 模板。前四步还在和产品对齐，后四步已经进入交付。
_FEATURE_STAGE_SIDE = {
    "需求澄清": "产品经理",
    "评审": "产品经理",
    "排期": "产品经理",
    "设计": "产品经理",
    "开发": "对口研发",
    "测试": "对口研发",
    "验收": "对口研发",
    "发布": "对口研发",
}


def feature_role_routing_guidance(ticket: Optional[TicketContext] = None) -> str:
    """需求单专属：产品澄清 vs 已对齐可实施（prompt 判断，非关键词硬规则）。

    报障/缺陷/明显非需求时模型应忽略本段。Step3 画像与 Step6 仲裁共用。
    提单记下的当前阶段只辅助，正文与重派备注仍然优先。
    """
    text = (
        "【仅需求单·产品/研发分流】（仅当判定本单是需求时适用；"
        "报障/缺陷/运维故障忽略本段，仍按现象对口研发）\n"
        "先理解正文与重派备注，判断需求处于哪个阶段，再选人——靠语义理解，"
        "不要只靠「需求」「产品」「研发」等字面硬套：\n"
        "  1) 待产品澄清 / 看不清：要不要做、做成什么样、缺方案或验收、"
        "仍像在和产品讨论范围 → 优先派职责卡片上的产品经理/产品负责人"
        "（责任模块或职责含产品、产品设计、产品经理等），"
        "不要猜一个功能研发硬派。\n"
        "  2) 产品已对齐、可实施：已与产品讨论过或方案/验收清楚，"
        "落到具体模块实现 → 派对口研发/功能负责人，不要再甩回产品「重新讨论」。\n"
        "  3) 阶段仍模糊、名单里产品与研发都像能接 → 默认倾向产品经理做分流澄清。\n"
        "有 [倾向接单人] / 用户明确点名时，仍优先尊重用户选择（与公共铁律一致）。"
    )
    name = (getattr(ticket, "curr_step_name", None) or "").strip() if ticket else ""
    side = _FEATURE_STAGE_SIDE.get(name)
    if not side:
        return text
    return (
        text
        + f"\n提单人记下的当前阶段是「{name}」，辅助偏向{side}。"
        "需求澄清、评审、排期、设计辅助偏向产品经理；"
        "开发、测试、验收、发布辅助偏向对口研发。"
        "阶段与正文相反时，仍以正文的 1) 2) 为准，不要只按阶段名硬派。"
    )


def person_anti_hallucination() -> str:
    """看人画像时的反幻觉：可以推断谁能接，但不能编造其职责。"""
    return (
        "【反幻觉】可以推断「这类故障/需求谁能接」，"
        "但依据必须落在该人卡片上已写出的责任模块、负责内容、职责上；"
        "禁止编造、脑补、补全其未写明的负责内容；"
        "禁止把别人的模块或职责安到此人头上。"
        "Agent假设仅供参考；与标题/描述冲突时，以正文现象为准，不要按假设改派。"
    )


def _scope_lookup(product: str, fname: str, keywords_map: dict, anchors_map: dict):
    key = f"{product}-{fname}"
    kws = keywords_map.get(key) or keywords_map.get(fname) or []
    anc = anchors_map.get(key) or anchors_map.get(fname) or ""
    return kws, anc


def format_function_scope(fname: str, keywords=None, anchor: str = "") -> str:
    """功能名 + 树上仍保存的关键词 / 一句话说明。与功能名重复的不写。"""
    name = (fname or "").strip()
    seen = {name}
    extras = []
    for raw in keywords or []:
        kw = str(raw).strip()
        if kw and kw not in seen:
            seen.add(kw)
            extras.append(kw)
    anc = (anchor or "").strip()
    if anc and anc not in seen:
        extras.append(anc)
    if not extras:
        return name
    return f"{name}（{'；'.join(extras)}）"


def responsible_content_for(eng, keywords_map=None, anchors_map=None, max_chars: int = 400) -> str:
    """把责任树 keywords / anchor 接到该人负责的功能后面。

    不再作为独立召回支路；只丰富职责卡片上的「负责内容」。
    """
    if keywords_map is None or anchors_map is None:
        from ai.agents.AiDiagnosisPlatform.assigner.settings import current_scope_maps
        live_kws, live_anc = current_scope_maps()
        if keywords_map is None:
            keywords_map = live_kws
        if anchors_map is None:
            anchors_map = live_anc
    parts = []
    for product in (eng.responsibility_modules or {}):
        for fname in eng.function_names_for_product(product):
            kws, anc = _scope_lookup(product, fname, keywords_map or {}, anchors_map or {})
            piece = format_function_scope(fname, kws, anc)
            if piece == (fname or "").strip():
                continue
            parts.append(piece)
    text = "；".join(p for p in parts if p)
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def engineer_brief_lines(
    eng,
    duty_max: int = 200,
    scope_max: int = 400,
    keywords_map=None,
    anchors_map=None,
) -> List[str]:
    """L1（及需要看人画像的 Step）共用的工程师卡片。"""
    from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import llm_person_label

    bits = [f"职级:L{eng.job_level}"]
    if eng.department:
        bits.append(f"部门:{eng.department}")
    if eng.company:
        bits.append(f"公司:{eng.company}")
    duty = (eng.duty_text or "").strip()
    scope = responsible_content_for(
        eng,
        keywords_map=keywords_map,
        anchors_map=anchors_map,
        max_chars=scope_max,
    )
    lines = [
        f"- {llm_person_label(eng=eng)}",
        "  " + " ".join(bits),
        f"  责任模块:{eng.modules_display() or '无'}",
    ]
    if scope:
        lines.append(f"  负责内容:{scope}")
    lines.append(f"  职责:{duty[:duty_max] if duty else '无'}")
    return lines
