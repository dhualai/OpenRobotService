"""Step3·画像召回：对照职责卡片打分，不看历史单。"""

from __future__ import annotations

from typing import List

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    engineer_brief_lines,
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

    lines = [
        intro,
        ticket_type_person_guidance(ticket).rstrip(),
        "工单写的是现象或需求，不是职责原文。先看懂本单要解决什么，再对照各人卡片判断谁能接。",
        "职责文案可以为空；责任模块是选人的主依据。有职责文案时作补充，无职责文案时只依据责任模块，"
        "禁止因此压低分数，也禁止臆造未写出的职责。",
        "不要用过往工单经验（相似工单和问题簇另有两路），也不要求卡片字面等于标题。",
        "故障码、车型若出现，只帮助理解现象，不单独作为选人硬条件。",
        person_anti_hallucination(),
        "【匹配度刻度】按档给分，拉开差距，避免多数人挤在 0.8 附近：",
        "  - 0.90～1.00：责任模块与本单主问题直接对口，是首选接单人",
        "  - 0.75～0.89：能独立接手，但不是最贴的主责",
        "  - 0.55～0.74：相关，可作备选或协助",
        "  - 0.40～0.54：仅沾边；把握不足时宁可不选入 rankings",
        "  - 低于 0.40：不要写入 rankings",
        "每位入选的人都必须写 reason：用姓名，一句话，点出工单里哪句现象/需求，"
        "以及据此判断该人哪条责任模块（或职责）能接。",
        "",
        ticket_fields_block(ticket).rstrip(),
        "",
        "【候选工程师】",
    ]
    for e in engineers:
        lines.extend(engineer_brief_lines(e))

    lines.extend([
        "",
        pick_rule,
        "每位候选都标注了「姓名:」和「ID:」，评估时必须看姓名，不要只盯 id。"
        "输出 JSON。engineer_id 精确复制「ID:」后面的 users.id；engineer_name 复制「姓名:」。"
        "reason 必填，禁止空。confidence 填 0~1，并符合上方刻度。",
        '{"rankings":[{"engineer_id":"<精确复制 ID:>","engineer_name":"<精确复制 姓名:>","confidence":0.85,"reason":"要每周导出，属产品需求，张三责任模块含摇人吧产品"},...]}',
    ])
    return "\n".join(lines)
