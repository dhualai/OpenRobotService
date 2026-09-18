"""Step1：部门主判 R2 + 部门审查。"""

from __future__ import annotations

from typing import List

from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import (
    dept_common_guardrails,
    dept_product_map,
    ticket_fields_block,
    ticket_type_guidance,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext


def _dept_blocks(departments: List[dict], with_examples: bool) -> str:
    blocks = []
    for dept in departments or []:
        name = dept.get("name") or ""
        if not name:
            continue
        profile = (dept.get("profile_text") or "").strip()
        if with_examples:
            examples = dept.get("examples") or []
            ex_lines = []
            for ex in examples[:3]:
                if isinstance(ex, dict):
                    ex_lines.append(f"  示例：{ex.get('title', '')} → {ex.get('dept', name)}")
            blocks.append(
                f"---\n部门：{name}\n{profile}\n" + ("\n".join(ex_lines) if ex_lines else "")
            )
        else:
            blocks.append(f"---\n部门：{name}\n{profile}")
    return "\n".join(blocks)


def build_r2(ticket: TicketContext, departments: List[dict], feedback: str = "") -> str:
    return (
        "你是工单部门路由专家。请判断工单最可能由哪个部门负责处理。\n"
        f"{ticket_type_guidance(ticket)}\n"
        f"{dept_product_map()}\n"
        f"{dept_common_guardrails()}\n"
        "请严格按下面四步判定，不要扫一眼标题就给分：\n"
        "1) 定尺子：按工单类型选「故障现象」或「产品/项目」；类型与正文不符则按正文选。\n"
        "2) 排除：先看各部门【不负责】，把明显无关的划掉。\n"
        "3) 命中：再看【负责】/【典型现象】或产品归属，确定主导部门。\n"
        "4) 交叉：只留一个主导（0.80+），次要给 0.3~0.5，禁止多个部门都挤在 0.6~0.7。\n"
        "confidence 刻度（0~1）：\n"
        "  - 0.80~1.0：明确负责（只有这一档才会触发部门硬收紧）\n"
        "  - 0.55~0.80：很可能负责（主要候选，有较强证据）\n"
        "  - 0.25~0.55：有一定关联（次要/交叉，请保留并给合理分数）\n"
        "  - 0~0.25：基本不相关（被【不负责】排除或明显无关）\n"
        "按 confidence 降序最多输出 3 个部门；无法判断则输出空数组，不要强行给分。\n"
        "若有审查反馈：仅作参考；与画像【不负责】冲突时以画像为准。\n\n"
        "【部门清单】\n"
        + _dept_blocks(departments, with_examples=True)
        + "\n\n"
        + ticket_fields_block(ticket)
        + (
            "\n【审查反馈（上一轮部门审查的意见，供你重新判定时参考，请审慎采纳）】"
            f"\n{feedback}\n"
            if feedback
            else ""
        )
        + "\n输出 JSON（不要其它文字）：\n"
        '{"departments":[{"name":"清单中的部门名","confidence":0.0,"reason":"工单证据+负责或不负责要点"}]}'
    )


def build_audit(ticket: TicketContext, departments: List[dict], suggested_dept: str) -> str:
    return (
        "你是工单部门派发审查员。系统已把工单初步判给某个部门，请你复核这个判断是否正确。\n"
        "请基于工单内容与各部门职责画像（**负责什么/不负责什么**）独立判断，"
        "不要被原判部门带偏。\n"
        f"{ticket_type_guidance(ticket)}\n"
        f"{dept_product_map()}\n"
        f"{dept_common_guardrails()}\n\n"
        "【全部部门画像】\n" + _dept_blocks(departments, with_examples=False) + "\n\n"
        + ticket_fields_block(ticket)
        + f"\n【系统初步判定部门】\n{suggested_dept or '未确定'}\n\n"
        "请复核并输出 JSON（不要其它文字）：\n"
        '{"ok": true, "correct_dept": "<ok=false时从清单原样复制正确部门，ok=true可为空>", '
        '"confidence": 0.0, "reason": "工单证据+负责或不负责要点"}'
    )
