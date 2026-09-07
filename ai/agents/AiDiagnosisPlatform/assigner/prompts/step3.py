"""Step3 L1：纯 LLM 召回。"""

from __future__ import annotations

from typing import List

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    engineer_brief_lines,
    person_anti_hallucination,
    ticket_fields_block,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def build_l1(ticket: TicketContext, engineers: List[EngineerProfile], top_k: int = 5) -> str:
    all_flag = top_k >= len(engineers)
    intro = (
        "你是派单专家。请评估每一位候选工程师与工单的匹配度（0~1），"
        f"并为全部 {len(engineers)} 位给出分数。"
        if all_flag
        else (
            "你是派单专家。请从下面的候选工程师中，选出最符合的 "
            f"Top {top_k}（排名不分先后），并为每人给出匹配度（0~1）。"
        )
    )
    lines = [
        intro,
        "工单写的是现象或需求，不是职责原文。先看懂本单要解决什么，再对照各人卡片判断谁能接：",
        "  - 报障/缺陷：这类故障最可能由谁职责范围内的人解决；",
        "  - 需求/咨询：该派给产品经理，还是派给对应功能的负责人。",
        "不要用过往工单经验（相似工单和问题域另有两路），也不要求卡片字面等于标题。",
        person_anti_hallucination(),
        "每位入选的人都必须写 reason：用姓名，一句话，点出工单里哪句现象/需求，以及据此判断该人哪条模块或职责能接。",
        "",
        ticket_fields_block(ticket).rstrip(),
        "",
        "【候选工程师】",
    ]
    for e in engineers:
        lines.extend(engineer_brief_lines(e))

    lines.extend([
        "",
        (
            "必须且只能从上面的候选工程师中评估全部候选人并给出分数。"
            if all_flag
            else f"必须且只能从上面的候选工程师中选出 {top_k} 位。"
        ),
        "每位候选都标注了「姓名:」和「ID:」，评估时必须看姓名，不要只盯 id。"
        "输出 JSON。engineer_id 精确复制「ID:」后面的 users.id；engineer_name 复制「姓名:」。"
        "reason 必填，禁止空。confidence 填 0~1。",
        '{"rankings":[{"engineer_id":"<精确复制 ID:>","engineer_name":"<精确复制 姓名:>","confidence":0.85,"reason":"要每周导出，属产品需求，张三职责含摇人吧产品"},...]}',
    ])
    return "\n".join(lines)
