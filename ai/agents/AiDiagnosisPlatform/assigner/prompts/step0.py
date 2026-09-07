"""Step0：弱信号指定人 + 同名抉择。"""

from __future__ import annotations

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import ticket_fields_block
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext


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
        "\n"
        f"{ticket_fields_block(ticket)}\n"
        "只关注中文人名，忽略”U老师””小U””系统””admin”等非人名。\n"
        "输出 JSON：{“has_preference”: true/false, “preferred_name”: “姓名”}\n"
        "has_preference=false 时 preferred_name 填 null。"
    )


def build_collision(ticket: TicketContext, cand_list: str) -> str:
    return (
        "用户已指定处理人，但工单系统中存在多个同名/近似名候选人。"
        "请结合工单内容在下列候选人中选定**一位**。\n"
        f"{ticket_fields_block(ticket)}\n"
        f"候选列表（每人格式为 姓名:xx ID:yy，selected_id 必须原样复制「ID:」后的 id）：\n{cand_list}\n\n"
        "输出 JSON：{\"selected_id\": \"候选 id\", \"reason\": \"简述选择理由\"}；"
        "若实在无法区分则输出 {\"can_determine\": false}。"
    )
