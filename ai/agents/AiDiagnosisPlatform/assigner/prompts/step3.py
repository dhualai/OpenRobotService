"""Step3·画像召回：对照职责卡片打分，不看历史单。"""

from __future__ import annotations

from typing import List

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    USP_DISPATCH_BRIEF,
    USP_STATIONS,
    engineer_brief_lines,
    feature_role_routing_guidance,
    person_anti_hallucination,
    ticket_fields_block,
    ticket_type_person_guidance,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def build_l1(
    ticket: TicketContext,
    engineers: List[EngineerProfile],
    top_min: int = 3,
    top_max: int = 6,
    keywords_map=None,
    anchors_map=None,
    product: str = "",
    station: str = "",
) -> str:
    """构造画像召回 prompt。

    top_min / top_max：提示模型按区间选人，不钉死人数；解析侧只按 top_max 封顶。
    """
    n = len(engineers)
    lo = max(1, min(int(top_min), n))
    hi = max(lo, min(int(top_max), n))
    all_flag = hi >= n

    if all_flag:
        intro = (
            "你是派单专家。请评估每一位候选工程师与工单的匹配度（0~1），"
            f"并为全部 {n} 位给出分数。"
        )
        pick_rule = "必须且只能从上面的候选工程师中评估全部候选人并给出分数。"
    else:
        intro = (
            "你是派单专家。请从下面的候选工程师中选出最符合的人，"
            f"人数落在 {lo}～{hi} 位：把握足可以靠上限，把握不够就少选，"
            "不要为凑人数硬选不相关的人。"
        )
        pick_rule = (
            f"必须且只能从上面的候选工程师中选出 {lo}～{hi} 位；"
            f"最多 {hi} 位，禁止超过。"
        )

    usp = (product or "").strip() == "调度USP"
    locked = (station or "").strip() if usp else ""
    station_lines = []
    if locked:
        station_lines.append(
            f"本单已经判定停在「{locked}」。本批只按这一站看责任模块盖不盖得住，"
            f"station 仍写「{locked}」，不要改站。"
        )
    elif usp:
        station_lines.append(
            "先判断这张单停在哪一站，再给候选人打分。"
            "station 只能是下面之一：" + "、".join(USP_STATIONS) + "。"
            "看不准就写最像的一站，不要写「调度问题」。"
        )

    lines = [
        intro,
        ticket_type_person_guidance(ticket).rstrip(),
        feature_role_routing_guidance(ticket).rstrip(),
        "工单写的是现象或需求，不是职责原文。先看懂本单要解决什么，再对照各人卡片判断谁能接。",
        "职责文案可以为空；责任模块是选人的主依据。负责内容里的关键词和一句话说明来自责任树，"
        "用来认口语/别称，不是另一路召回。有职责或负责内容时作补充，没有时只依据责任模块，"
        "禁止因此压低分数，也禁止臆造未写出的职责。",
        "不要用过往工单经验（相似工单和问题簇另有两路），也不要求卡片字面等于标题。",
        "故障码、车型若出现，只帮助理解现象，不单独作为选人硬条件。",
        *([USP_DISPATCH_BRIEF] if usp else []),
        *station_lines,
        "先看本批里有几个人能独立接这张单。"
        "几个人的责任模块或职责都能解决同一类问题（例如都做常规算法排查）时，他们是同一档能接的人。",
        "模块名更接近现象用词，不能单独把其中一位抬成首选。"
        "0.90～1.00 只留给本批里只有这一人能接、其他人接不了的情况。",
        "几个人都能独立解决时，这几位都落在 0.75～0.89："
        "可以按贴合程度在这一档内略分高低，最高不要到 0.90。"
        "只能协助、不能独立解决的人，仍落在 0.55～0.74。",
        "报障、缺陷只给责任模块能解决这条故障的人高分。"
        "产品设计、需求、定级、发版协调对不上故障现象时，"
        "不要因为可以分流或统筹给到 0.55 以上。"
        "这类人最多 0.40～0.54；把握不足就不要写入 rankings。"
        "需求单的产品分流不适用于报障。",
        "分批打分时也必须遵守同一刻度。"
        "前面批次里某人的高分，不能作为本批抬分的理由。"
        "不要因为本批人少就把仅沾边的人抬到 0.8+。",
        person_anti_hallucination(),
        "【匹配度刻度】按档给分，拉开差距，避免多数人挤在 0.8 附近：",
        "  - 0.90～1.00：本批只有这一人能接本单主问题，其他人接不了",
        "  - 0.75～0.89：能独立接手；本批有多人都能接时，他们都落在这一档",
        "  - 0.55～0.74：相关，可作备选或协助",
        "  - 0.40～0.54：仅沾边。报障上只做产品分流、模块对不上故障的人停在这里",
        "  - 低于 0.40：不要写入 rankings",
        "每位入选的人都必须写 reason：用姓名，一句话，点出工单里哪句现象/需求，"
        "以及据此判断该人哪条责任模块、负责内容或职责能接。",
        *(
            ["调度USP 的 reason 还要写出这张单停在哪一站。"]
            if usp else []
        ),
        "",
        ticket_fields_block(ticket).rstrip(),
        "",
        "【候选工程师】",
    ]
    for e in engineers:
        lines.extend(
            engineer_brief_lines(e, keywords_map=keywords_map, anchors_map=anchors_map)
        )

    if usp:
        output_example = (
            '{"station":"路径规划","rankings":[{"engineer_id":"<精确复制 ID:>",'
            '"engineer_name":"<精确复制 姓名:>","confidence":0.85,'
            '"reason":"界面停在规划路径中，属路径规划，张三责任模块含路径规划"},...]}'
        )
    else:
        output_example = (
            '{"rankings":[{"engineer_id":"<精确复制 ID:>","engineer_name":"<精确复制 姓名:>",'
            '"confidence":0.85,"reason":"要每周导出，属产品需求，张三责任模块含摇人吧产品"},...]}'
        )
    lines.extend([
        "",
        pick_rule,
        "每位候选都标注了「姓名:」和「ID:」，评估时必须看姓名，不要只盯 id。"
        "输出 JSON。engineer_id 精确复制「ID:」后面的 users.id；engineer_name 复制「姓名:」。"
        "reason 必填，禁止空。confidence 填 0~1，并符合上方刻度。",
        output_example,
    ])
    return "\n".join(lines)
