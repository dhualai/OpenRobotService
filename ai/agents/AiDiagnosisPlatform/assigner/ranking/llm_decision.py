"""LLM 综合决策层：先判断工单技术归属（前端/后端/算法...），再结合精排分数选人"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)

from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

# 「摇人吧服务号」自身的项目标识。
# 只有工单项目归属此类项目时，问题的总负责人（模块总负责人）才按服务号内部
# 子界面/子功能区分；常规 AGV/AMR 项目（调度USP 等）不适用这套总负责人逻辑，
# 不应把"模块总负责人优先"这套 prompt 引入。
# 用较短串「摇人吧服务号」做包含匹配，可同时命中「摇人吧服务号」本身
# 与兜底项目「摇人吧服务号提单」（Leo_test）。
_YAORENBA_INTAKE_PROJECT_MARKERS = (
    "摇人吧服务号",
)


class LlmDecision:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    @staticmethod
    def _is_yaorenba_intake(ticket) -> bool:
        """工单是否归属「摇人吧服务号提单」项目。

        只有这种兜底项目下的工单，才应启用"服务号模块总负责人"派单规则：
        （我要摇人提单 / 系统任务 / 后台管理…各自总负责人不同）。
        其余项目直接返回 False，避免把服务号总负责人逻辑错误套用到常规项目上。
        """
        project = (getattr(ticket, "project_name", None) or "").strip()
        if not project:
            return False
        # 归一化：去掉可能的空格 / 全角空格后做精确即可（兜底项目名固定且唯一）
        norm = project.replace(" ", "").replace("\u3000", "")
        return any(marker.replace(" ", "") in norm for marker in _YAORENBA_INTAKE_PROJECT_MARKERS)

    async def adecide(self, ticket, engineers, recall_result, ranked_scores):
        """综合决策入口：
        - 若为摇人吧提单且检测到模块总负责人（duty_text/responsibility_modules 标记），优先返回该负责人；
        - 否则若数值排名差距足够大（top - second >= 配置阈值），直接选 top，LLM 不覆写；
        - 否则调用 LLM（原行为）。
        """
        # 先构造快速判断数据：排名列表（按 total_score 已排序）
        try:
            items = list(ranked_scores.items())
        except Exception:
            items = []

        top_eid = None
        second_eid = None
        top_score = 0.0
        second_score = 0.0
        if items:
            top_eid, top_meta = items[0]
            top_score = float(top_meta.get("total_score", 0.0))
            if len(items) > 1:
                second_eid, second_meta = items[1]
                second_score = float(second_meta.get("total_score", 0.0))

        # 1) Yaorenba 专属：优先模块总负责人（在 duty_text 或 responsibility_modules 中标注含 '总负责人'）
        try:
            force_owner = bool(self._config.yaorenba_force_module_owner)
        except Exception:
            force_owner = True

        if force_owner and self._is_yaorenba_intake(ticket):
            # 定义 UI 关键词，帮助匹配界面类问题
            ui_keywords = ("界面", "拖拽", "显示", "乱码", "交互", "页面", "iOS", "ios", "UI")
            desc = (getattr(ticket, "problem_description", "") or "").lower()
            is_ui = any(k.lower() in desc for k in ui_keywords)

            # 优先找明确标注为“总负责人”的候选（且其 responsibility_modules 中包含服务号相关 product）
            for eng in engineers:
                duty = (eng.duty_text or "") or ""
                duty_low = duty.lower()
                has_owner_mark = "总负责人" in duty_low or "总负责" in duty_low
                # 检查其负责产品是否包含摇人吧服务号
                prod_keys = [p for p in (eng.responsibility_modules or {}).keys()]
                prod_match = any("摇人吧服务号" in (p or "") for p in prod_keys)
                # 进一步：若是 UI 问题，优先找其模块包含 '我要摇人' / '服务号页面' / '前端' 的人
                mods = []
                for mlist in (eng.responsibility_modules or {}).values():
                    for m in (mlist or []):
                        mods.append((m or "").lower())
                mod_ui_hit = any(x in ("我要摇人", "服务号页面", "前端", "页面") for x in mods)

                if prod_match and (has_owner_mark or (is_ui and mod_ui_hit)):
                    # 选中该负责人，构造 AssignmentResult
                    score = ranked_scores.get(eng.id, {}).get("total_score", top_score)
                    return AssignmentResult(
                        engineer_id=eng.id, engineer_name=eng.name,
                        confidence_score=round(float(score), 4),
                        reasoning=f"Yaorenba module owner matched (duty_text/mods) or UI-module hit; bypassed LLM.",
                        decision_type="auto",
                    )

        # 2) 若排名差距足够大则直接采纳 top（避免 LLM 频繁覆写明显的数值优势）
        try:
            threshold = float(getattr(self._config, "llm_respect_ranking_threshold", 0.3))
        except Exception:
            threshold = 0.3

        if top_eid and (top_score - second_score) >= threshold:
            # 直接返回 top
            eng = next((e for e in engineers if e.id == top_eid), None)
            if eng:
                return AssignmentResult(
                    engineer_id=eng.id, engineer_name=eng.name,
                    confidence_score=round(float(top_score), 4),
                    reasoning=f"Selected by ranking margin: top({top_score:.4f}) - second({second_score:.4f}) >= threshold({threshold})",
                    decision_type="auto",
                )

        # 3) 回退到原有 LLM 流程
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
            "【第三维度：工单类型（你必须独立判断，它决定由谁承接）】",
            "上游提单 Agent 给了一个初步类型（见下方工单区的 ticket_type），仅供参考、可能判错；你必须基于工单内容独立复核出最终类型。",
            "五类定义与边界（务必区分清楚，尤其 support 与 feature）：",
            "- support 咨询：询问使用方法/操作指导/配置协助，「不会用/怎么用/如何操作/需要指导」等；不新增功能、也不报故障。",
            "- feature 需求：希望新增/增加功能、提产品建议，「建议新增/希望支持/能不能加/增加一个」等。",
            "- bug 缺陷：功能本该有但行为错误/异常，与预期不符。",
            "- problem 报障：现场异常、故障报修、设备/系统出问题。",
            "- other 其他：无法归入以上四类（闲聊/感谢/无关内容）。",
            "承接规则：",
            "- feature 需求类 → 派给该产品的产品经理（负责「产品设计」模块的候选人），由产品经理做需求梳理。",
            "- 其余四类（support/bug/problem/other）→ 一律按工单涉及的产品 + 模块匹配候选人画像，选总分最高者，不要按类型硬派。",
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
        if getattr(ticket, "ticket_type", None):
            lines.append(f"工单类型(提单Agent初步判断，仅供参考，需独立复核): {ticket.ticket_type}")
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.fault_code:
            lines.append(f"故障码: {ticket.fault_code}")

        lines.extend([
            "",
            "【选人规则】",
            "1. 先独立判断 ticket_category（support/feature/bug/problem/other）与 problem_domain，并识别工单涉及的产品与模块。",
            "2. feature 需求类：优先找负责「产品设计」模块、且归属产品与工单一致的候选人（该产品的产品经理）。",
            "3. 其余类型（support/bug/problem/other）：按工单涉及的模块匹配候选人负责的模块，选总分最高者（#1 默认优先）。",
            "4. 仅当 #1 的产品/模块明显不匹配时才选下一个更相关者，并在 reasoning 说明。",
            "5. 若你复核出的类型与上游初步类型不一致，在 reasoning 里说明理由（如「上游判 X，实为 Y」）。",
        ])

        # 「摇人吧服务号提单」项目专属：按服务号内部子界面/子功能区分总负责人。
        # 只有工单项目归属该兜底项目时才启用这条总负责人规则；常规 AGV/AMR 项目
        # （调度USP 等）不引入，避免把服务号的总负责人逻辑错误套用到其他项目上。
        if self._is_yaorenba_intake(ticket):
            lines.extend([
                "5.（仅本次工单项目＝「摇人吧服务号提单」适用）先判断问题落在服务号哪个环节，再派给对应子功能的「模块总负责人」：",
                "   - 我要摇人界面的「提单/报障」过程（建单、填信息、提交、AI诊断出单等，涉及我要摇人模块）：优先派给「我要摇人」模块总负责人；",
                "   - 「处理工单」的系统任务（工单收件箱、任务处理、状态流转、接单/转派等，涉及系统任务模块）：优先派给「系统任务」模块总负责人；",
                "   - 「后台管理」相关（项目看板/跨项目看板、风险管理、状态检测、数据统计、角色授权/权限等，涉及后台管理模块）：优先派给「后台管理」模块总负责人。",
                "   判定依据以候选人 responsibility_modules 或 duty_text 中的「总负责人」标记为准（如『我要摇人总负责人』『系统任务总负责人』『后台管理总负责人』），",
                "   上述常见对应仅作参考，若候选人名单无对应总负责人，则退回按总分/模块匹配正常选人。",
            ])

        lines.extend([
            "",
            "输出 JSON。engineer_id 必须是候选人列表中该人选对应的 ID（「ID:」字段，即 users.id），必须精确复制，不要填姓名或自造标识。",
            '{"ticket_category":"support", "problem_domain":"产品", "product":"", "engineer_id":"<精确复制候选ID>", "confidence_score":0.85, "reasoning":"理由(说明类型/产品/模块/环节判断)", "decision_type":"auto"}',
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
