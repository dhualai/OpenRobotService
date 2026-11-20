"""Step0：弱信号指定人 + 同名抉择。"""

from __future__ import annotations

from typing import List, Union

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    engineer_brief_lines,
    ticket_fields_block,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def build_weak(ticket: TicketContext) -> str:
    return (
        "分析以下工单内容，判断提单人是否明确表达了”希望由谁处理”的意图。\n"
        "\n"
        "典型表达（不限于此）：\n"
        "- “这个给张三看一下” / “让李四处理” / “请王五帮忙看看”\n"
        "- “转给赵六” / “最好是钱七来搞” / “这个问题周八比较熟”\n"
        "- “找某某某” / “某某某有空吗” / “安排给某某某”\n"
        "- “需提给某某某” / “提给某某某” / “需要某某某看一下”\n"
        "- “这个某某某负责” / “某某某来搞” / “派给某某某”\n"
        "- “建议由某某某处理” / “建议让某某某跟进” / “建议请某某某看一下”\n"
        "- “某某某才是负责这个的” / “应该是某某某负责” / “是某某某负责的”\n"
        "（重新派单备注里也常出现以上说法）\n"
        "\n"
        "【关键区分】人名可能来自两处，只认后者：\n"
        "- ✗ 转述/引用**别的工单**里的人：那是被引用工单的诉求，不是本单要指定谁。\n"
        "  例：“工单 #870（…）用户要求重新派单给汪海波。”\n"
        "  → 人名属于 #870，本单只是复述，has_preference=false。\n"
        "- ✗ 仅叙述历史处理人，未要求本单再派给他：\n"
        "  例：“上次汪海波处理过类似问题” / “之前是汪海波跟的”。\n"
        "  → has_preference=false。\n"
        "- ✓ 本单提单人自己的指派意图：无论谁转达，只要是指向本单的就认。\n"
        "  例：“客服说让汪海波看看” / “要求派给汪海波” / “备注：再派给汪海波”\n"
        "  → has_preference=true, preferred_name=汪海波。\n"
        "- ✓ 重新派单备注里明确换人：\n"
        "  例：重派备注“再派给汪海波” / “请改派张三”。\n"
        "  → has_preference=true。\n"
        "判断要点：看这个人是“**本单**该由谁处理”，还是只在描述“**另一张单**当时指定了谁”或“历史上谁处理过”。\n"
        "若本单描述引用了其他工单（如出现“工单 #数字”“某单子里”），只在该引用范围内出现的人名不算本单指定人。\n"
        "\n"
        f"{ticket_fields_block(ticket)}\n"
        "只关注中文人名，忽略”U老师””小U””系统””admin”等非人名。\n"
        "输出 JSON：{“has_preference”: true/false, “preferred_name”: “姓名”}\n"
        "has_preference=false 时 preferred_name 填 null。"
    )


def build_collision(
    ticket: TicketContext,
    candidates: Union[str, List[EngineerProfile]],
) -> str:
    """同名抉择。优先传工程师列表（含职责卡片）；兼容旧的候选人字符串。"""
    if isinstance(candidates, str):
        cand_block = candidates
    else:
        lines: List[str] = []
        for eng in candidates or []:
            lines.extend(engineer_brief_lines(eng, duty_max=120, scope_max=200))
        cand_block = "\n".join(lines) if lines else "（无候选人）"
    return (
        "用户已指定处理人，但工单系统中存在多个同名/近似名候选人。"
        "请结合工单内容与各人职责卡片选定**一位**。\n"
        f"{ticket_fields_block(ticket)}\n"
        "【同名候选人】（每人含 姓名: / ID: / 部门 / 责任模块 / 职责；"
        "selected_id 必须原样复制「ID:」后的 id）\n"
        f"{cand_block}\n\n"
        "优先选责任模块/职责与本单现象更对口的人；"
        "卡片都对不上或无法区分时输出 {\"can_determine\": false}。\n"
        "输出 JSON：{\"selected_id\": \"候选 id\", \"reason\": \"简述选择理由\"}；"
        "若实在无法区分则输出 {\"can_determine\": false}。"
    )
