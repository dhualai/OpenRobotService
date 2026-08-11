"""LLM 综合决策层：先判断工单技术归属（前端/后端/算法...），再结合精排分数选人"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)


class LlmDecision:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    async def adecide(self, ticket, engineers, recall_result, ranked_scores):
        prompt = self._build_prompt(ticket, engineers, recall_result, ranked_scores)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=400, temperature=0.3)
            return self._parse(response, engineers)
        except Exception:
            return None

    def _build_prompt(self, ticket, engineers, recall_result, ranked_scores):
        lines = [
            "你是派单决策专家。请先判断工单的技术归属（前端/后端/算法等）与业务模块，",
            "再结合精排分数与候选人画像，推荐最合适的人。",
            "",
            "【第一维度：技术归属（判断问题属于哪个技术层）】",
            "- 前端/界面类：页面、UI、显示、展示、时区显示、标题显示、交互、列表、表单、样式、渲染",
            "- 后端/接口类：接口、服务端逻辑、数据存储、数据库、MQTT 通信、任务下发、业务逻辑处理",
            "- 算法类：路径规划、调度算法、地图生成、定位、避障、AI 模型、强化学习",
            "- 其他：产品/需求、数据分析、运维部署等",
            "注意：涉及「页面/显示/时区/标题展示」等表现层问题时，应归类为前端，除非描述明确指向后端数据或逻辑层。",
            "",
            "【第二维度：候选人负责的产品与模块】",
            "候选人 responsibility_modules = {产品: [该产品下此人负责的模块列表]}，",
            "例如张俊磊 {'摇人吧服务号': ['前端','我要摇人']} 表示他负责「摇人吧服务号」产品下的「前端」「我要摇人」两个模块。",
            "模块名（如 前端/后端/我要摇人/系统任务/后台管理/算法 等）都是此人负责的功能模块，",
            "它们是平级的模块清单，不代表「前端问题找前端、后端问题找后端」这种技术分层。",
            "判断工单归属应看工单内容本身（页面/显示类 → 界面相关模块；接口/数据类 → 服务端相关模块），",
            "再匹配候选人负责的模块中是否有相关项，而非按模块名硬套前端/后端。",
            "选人时：①工单涉及的产品/模块尽量匹配候选人负责的模块；②在匹配者中优先排名靠前的。",
            "",
            "【第三维度：工单类型（决定由谁承接）】",
            "先判断工单是：需求/产品建议、故障、咨询、还是其他。",
            "- 需求/产品建议类（含「建议新增」「需要支持」「希望增加」「需求」等）：应优先派给「产品设计/产品经理」类的候选人，",
            "  由其进行需求梳理与排期，而不是直接派给具体的开发/后端。",
            "- 故障/缺陷/修复类：派给对应技术的开发（前端问题→负责该产品前端模块的人；后端→负责服务端模块的人）。",
            "- 咨询/排查类：派给问题域最相关、总分最高者。",
            "候选人若负责「产品设计」模块，即为该产品的产品经理；名单可能有多名 PM，须按工单所属产品区分。",
            "",
            "【候选人排名（已含职级折扣；#1 为总分最高，默认应优先考虑）】",
        ]

        emap = {e.id: e for e in engineers}
        for rank, (eid, d) in enumerate(list(ranked_scores.items())[:5], 1):
            eng = emap.get(eid)
            if not eng:
                continue
            prod_parts = []
            for p, mods in eng.responsibility_modules.items():
                prod_parts.append(f"[{p}]{','.join(mods)}" if mods else f"[{p}]")
            duty = (eng.duty_text or "")[:100]
            dep = f"({eng.department})" if eng.department else ""
            lines.append(
                f"#{rank} ID:{eng.id} | L{eng.job_level} | {dep} "
                f"|{'|'.join(prod_parts)}"
            )
            lines.append(
                f"   分数: 总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} 语义={d.get('semantic_score',0):.2f} "
                f"历史={d.get('history_score',0):.2f}"
            )
            if duty:
                lines.append(f"   职责: {duty}")

        lines.extend([
            "",
            "【工单】",
            f"标题: {ticket.title or '无'}",
            f"描述: {ticket.problem_description}",
        ])
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.fault_code:
            lines.append(f"故障码: {ticket.fault_code}")

        lines.extend([
            "",
            "【选人规则】",
            "1. 先判断 ticket_category（需求/故障/咨询/其他）与 problem_domain，以及工单涉及的产品。",
            "2. 需求/产品建议类：优先找负责「产品设计」模块、且归属产品与工单一致的候选人（产品经理）。",
            "3. 故障/缺陷类：按工单涉及的模块（页面→界面类；逻辑->服务端类）匹配候选人负责的模块。",
            "4. 在匹配范围内，优先总分高者（#1 默认优先）；仅当 #1 产品/模块明显不匹配时才选下一个更相关者，并在 reasoning 说明。",
            "5. 若工单涉及「摇人吧服务号」产品：优先派给对应功能的「模块总负责人」候选人。",
            "   候选人 responsibility_modules 或 duty_text 中带「总负责人」标记（如「我要摇人总负责人」「系统任务总负责人」「后台管理总负责人」），",
            "   即该界面/功能的模块负责人，应优先承接对应模块的工单；",
            "   常见对应：我要摇人→张俊磊、系统任务→张文星、后台管理→罗昊（按其模块中的总负责人标记为准）。",
            "",
            "输出 JSON。engineer_id 必须是候选人列表中该人选对应的完整 username（以 wechat_ 开头，如 wechat_oD5oY3xxx），必须保留 wechat_ 前缀、精确复制，不要去掉前缀或填姓名。",
            '{"ticket_category":"需求", "problem_domain":"产品", "product":"摇人吧服务号", "engineer_id":"wechat_oD5oY3RN...", "confidence_score":0.85, "reasoning":"理由(说明类型/产品/模块/总负责人判断)", "decision_type":"auto"}',
            "decision_type: auto(>=0.8) / recommend(0.5-0.8) / fallback(<0.5)",
        ])
        return "\n".join(lines)

    def _parse(self, response, engineers):
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
        eid = data.get("engineer_id", "").strip()
        eng = next((e for e in engineers if e.id == eid), None)
        if not eng:
            return None
        dt = data.get("decision_type", "fallback").strip().lower()
        # ticket_category / problem_domain / product 为审计字段：纳入 reasoning 便于排查
        cat = data.get("ticket_category", "")
        dom = data.get("problem_domain", "")
        prod = data.get("product", "")
        reason = data.get("reasoning", "").strip()
        audit = "/".join(filter(None, [cat, dom, prod]))
        if audit and reason:
            reason = f"[{audit}] {reason}"
        return AssignmentResult(
            engineer_id=eng.id, engineer_name=eng.name,
            confidence_score=round(float(data.get("confidence_score", 0.0)), 4),
            reasoning=reason,
            decision_type=dt if dt in ("auto", "recommend", "fallback") else "fallback",
        )
