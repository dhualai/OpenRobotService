"""LLM 最终决策层（Step6）：在精排基础上做最终拍板选人

精排（排名 + 分数 + 原因）是系统给出的最强参考；本层 LLM 是最终决策者，
负责决定是否采纳精排 #1、以及在充分理由下（如用户重派要求、模块明显不匹配）
对候选做出调整。各维度判断（技术归属/产品/模块/工单类型）作为决策辅助信息。
"""

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

    def _resolve_redispatch_strong(
        self, ticket, engineers, ranked_scores
    ) -> Optional["EngineerProfile"]:
        """解析重新派单的强信号：仅指用户明确勾选的『结构化倾向人』。

        说明：用户重派时的『备注/原因』（preferred_assignee_remark）是转派的原因说明，
        不一定点名某人，不应从备注正则抠人名当强信号（易误配、语义失真）。
        因此这里只认 ticket.preferred_assignee（结构化 users.id，用户在表单中明确选择）。
        ”备注/原因“在 assign_ticket 已原样拼入 problem_description，
        并在 Step6 prompt 的【用户重新派单意图】段落作为上下文喂给 LLM 自行判断。
        """
        emap = {e.id: e for e in engineers}
        # 结构化倾向人（users.id）
        pref = (getattr(ticket, "preferred_assignee", "") or "").strip()
        if not pref:
            return None
        try:
            from app.core.user_identity import to_user_id
            pref_id = to_user_id(pref) or pref
        except Exception:
            pref_id = pref
        return emap.get(pref_id)


    async def adecide(self, ticket, engineers, recall_result, ranked_scores):
        """Step6 统一 LLM 最终决策入口。

        原则：精排为主 + 判断辅助 + 重派为上下文（无硬规则）。
        - 在所有情况下都调用 LLM，在精排 Top-K 窗口内做最终选人（没有确定性分支直接拍板）。
        - 结构化倾向人 / 摇人吧模块总负责人 仅作为「额外决策线索」参考提示喂给 LLM，
          由 LLM 自行判断是否采纳（并非强制规则）。
        - 重派备注/转派原因 只作为上下文（不从中抠人名），LLM 自行理解。
        - LLM 失败或窗口为空时，故障保护：保底回退精排第一名。
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

        # ── 决策日志：展示精排总分、LLM 维度分与候选窗口，便于定位"为什么派了某人" ──
        try:
            low_score_threshold = float(
                getattr(self._config, "llm_decision_low_score_threshold", 0.6)
            )
        except Exception:
            low_score_threshold = 0.6
        try:
            topk = int(getattr(self._config, "llm_decision_topk", 3))
            if topk < 1:
                topk = 1
        except Exception:
            topk = 3
        emap_diag = {e.id: e for e in engineers}
        # 窗口内 Top-K（按精排总分）
        window_names = [
            f"{emap_diag[eid].name if eid in emap_diag else eid[:8]}"
            f"(总={ranked_scores[eid].get('total_score',0):.2f},LLM={ranked_scores[eid].get('llm_score',0):.2f})"
            for eid, _ in items[:topk]
        ]
        # 窗口外但 LLM 分最高者（高 LLM 分却被精排/负载均衡挤出窗口 → 决策就看不到他）
        outside = [eid for eid, _ in items[topk:] if eid in emap_diag]
        outside_llm_top = sorted(
            outside, key=lambda eid: ranked_scores[eid].get("llm_score", 0.0), reverse=True
        )[:3]
        outside_str = ", ".join(
            f"{emap_diag[eid].name}(总={ranked_scores[eid].get('total_score',0):.2f},"
            f"LLM={ranked_scores[eid].get('llm_score',0):.2f},"
            f"在途={ranked_scores[eid].get('load_count','-')})"
            for eid in outside_llm_top
        ) or "-"
        top1_name = emap_diag[top_eid].name if top_eid in emap_diag else top_eid

        # ── 重新派单备注/倾向人 强信号：决策日志展示重派原因，便于定位"为什么最高分未被选" ──
        pref_assignee = (getattr(ticket, "preferred_assignee", "") or "").strip()
        pref_remark = (getattr(ticket, "preferred_assignee_remark", "") or "").strip()
        pref_desc = f"重派倾向人={pref_assignee or '-'}" if pref_assignee or pref_remark else ""
        if pref_remark:
            pref_desc += f" | 重派备注=\"{pref_remark[:120]}\""
        if pref_desc:
            logger.info(
                f"[派单:{getattr(ticket,'id','?')}] Step6 重新派单信息: {pref_desc}"
            )
        logger.info(
            f"[派单:{getattr(ticket,'id','?')}] Step6决策 | top1={top1_name} 总={top_score:.2f} "
            f"second={second_score:.2f} | 低分阈值={low_score_threshold} topk={topk} "
            f"| 窗口内=[{', '.join(window_names)}] | 窗口外LLM最高=[{outside_str}]"
        )

        # ── 收集"额外决策线索"（参考，不直接定人选）──
        # 所有情况统一交由 LLM 做最终决策；结构化倾向人 / 模块总负责人 仅作为提示传入 prompt，
        # 由 LLM 结合精排窗口协商权衡（尊重精排为主）。
        # 备注/转派原因(preferred_assignee_remark)不在此抠人名，只作上下文已在 prompt 呈现。
        extra_hints = []

        # 0) 结构化用户倾向处理人：作为提示供 LLM 协商，不无条件换人。
        try:
            strong_match = self._resolve_redispatch_strong(ticket, engineers, ranked_scores)
        except Exception:
            strong_match = None
        if strong_match is not None:
            s = float(ranked_scores.get(strong_match.id, {}).get("total_score", 0.0))
            in_window = strong_match.id in [eid for eid, _ in items[:topk]]
            logger.info(
                f"[派单:{getattr(ticket,'id','?')}] Step6 用户倾向处理人={strong_match.name} "
                f"总分={s:.2f} 在Top{topk}窗口={'是' if in_window else '否'} → 交由LLM协商"
            )
            extra_hints.append(
                f"用户明确指定的倾向处理人: {strong_match.name}(ID:{strong_match.id})，"
                f"总分={s:.2f}，{'在候选窗口内(优先考虑遵循用户意图)' if in_window else '不在候选窗口内(仅作参考，需谨慎)'}。"
                f"请结合精排与其分数决定是否改派；若遵循请在 reasoning 说明。"
            )

        # 1) 摇人吧专属：识别工单涉及的子界面/模块 → 找候选中的"模块总负责人/负责人"，
        #    仅作为一条「额外决策线索」喂给 LLM，由 LLM 决定是否参考，不是强制派给规则。
        try:
            enable_owner_hint = bool(self._config.yaorenba_force_module_owner)
        except Exception:
            enable_owner_hint = True

        if enable_owner_hint and self._is_yaorenba_intake(ticket):
            # 根据用户描述匹配具体子界面/模块，识别该模块的总负责人/负责人作为线索
            text = ((getattr(ticket, "title", "") or "") + " \n " + (getattr(ticket, "problem_description", "") or "")).lower()

            # 模块关键词映射（按优先级检查）
            module_map = {
                "我要摇人": ["我要摇人", "摇人界面", "摇人页面", "摇人"],
                "系统任务": ["系统任务", "任务界面", "收件箱", "工单收件箱"],
                "后台管理": ["后台管理", "管理后台", "权限", "看板", "数据统计"],
                "agent": ["agent", "ai", "ai诊断", "提单agent", "摇人agent", "机器人agent", "智能派单", "llm", "u老师"],
                "数据分析": ["日报", "周报", "数据分析", "数据看板", "统计"],
            }

            detected_modules = []
            for mod_key, keywords in module_map.items():
                for kw in keywords:
                    if kw in text:
                        if mod_key not in detected_modules:
                            detected_modules.append(mod_key)
                        break

            # 若检测到模块关键词，按检测顺序优先匹配模块总负责人 -> 负责人 -> 按分数选
            for mod in detected_modules:
                # 收集候选人：先找 duty_text 标注为该模块总负责人
                owners = []
                members = []
                for eng in engineers:
                    duty = (eng.duty_text or "").lower()
                    # 责任模块名扁平化
                    mods = [m.lower() for m in (eng.all_modules() or [])]

                    # 判断是否为该模块的总负责人（duty_text 中包含 模块名 + '总负责' 或 '总负责人'）
                    is_owner = False
                    if mod != "agent":
                        if (f"{mod}" in duty and ("总负责" in duty or "总负责人" in duty)):
                            is_owner = True
                    else:
                        # 对于 agent/AI 类型，查 duty_text 中含 '算法'/'ai'/'模型' 等关键词作为 owner
                        if any(x in duty for x in ("算法", "模型", "ai", "ml", "mlops")) and ("总负责" in duty or "总负责人" in duty):
                            is_owner = True

                    if is_owner:
                        owners.append(eng)
                        continue

                    # 非 owner 但负责该模块
                    if mod != "agent":
                        if any(mod in m for m in mods):
                            members.append(eng)
                    else:
                        # agent 类型匹配到负责算法/Agent 的工程师
                        if any(x in m for x in ("算法", "ai", "ml", "agent") for m in mods):
                            members.append(eng)

                chosen = None
                # 按优先级选择：owner 中按 ranked_scores 总分最高者；若无 owner 则在 members 中按分数选
                def score_of(e):
                    return float(ranked_scores.get(e.id, {}).get("total_score", 0.0))

                if owners:
                    chosen = max(owners, key=score_of)
                elif members:
                    chosen = max(members, key=score_of)

                if chosen:
                    s = score_of(chosen)
                    in_window = chosen.id in [eid for eid, _ in items[:topk]]
                    logger.info(
                        f"[派单:{getattr(ticket,'id','?')}] Step6 模块负责人线索: "
                        f"模块'{mod}' → {chosen.name}(总={s:.2f}) 在Top{topk}窗口={'是' if in_window else '否'} → 交由LLM参考"
                    )
                    extra_hints.append(
                        f"摇人吧模块专属提示: 工单涉及模块 '{mod}'，候选中的模块总负责人/负责人为 "
                        f"{chosen.name}(ID:{chosen.id}, 总分={s:.2f})，"
                        f"{'在候选窗口内(可优先考虑指派该模块负责人)' if in_window else '不在候选窗口内(仅作参考)'}。"
                    )

        # ── 统一决策：所有情况（含结构化倾向人、模块负责人线索）都经 LLM 在精排 Top-K 窗口内最终决策 ──
        try:
            topk = int(getattr(self._config, "llm_decision_topk", 3))
            if topk < 1:
                topk = 1
        except Exception:
            topk = 3

        # 构造 Top-K 窗口：仅保留精排前 K 的候选及其分数，供 LLM 挑选与解析。
        top_items = list(ranked_scores.items())[:topk]
        window_ranked = dict(top_items)
        window_engineers = []
        emap = {e.id: e for e in engineers}
        for eid, _ in top_items:
            eng = emap.get(eid)
            if eng is not None:
                window_engineers.append(eng)
        if not window_engineers:
            # 极端兜底：窗口为空则退回精排第一名
            eng = next((e for e in engineers if top_eid and e.id == top_eid), None)
            if eng:
                return AssignmentResult(
                    engineer_id=eng.id, engineer_name=eng.name,
                    confidence_score=round(float(top_score), 4),
                    reasoning="Decision window empty; fell back to top1 by ranking.",
                    decision_type="auto",
                )
            return None

        prompt = self._build_prompt(
            ticket, window_engineers, recall_result, window_ranked,
            extra_hints=extra_hints or None,
        )
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=400, temperature=0.3)
            # 打印 LLM 原始输出，便于核查大模型为何这么选（谁被推举、置信、reasoning）
            logger.info(
                f"[派单:{getattr(ticket,'id','?')}] Step6 LLM最终决策原始输出: {response[:500]}"
            )
            return self._parse(response, window_engineers)
        except Exception as e:
            logger.warning(
                f"[派单:{getattr(ticket,'id','?')}] Step6 LLM最终决策失败: {e}；"
                f"已降级采用精排第一名（决策保底，非规则）"
            )
            # LLM 失败时保底（故障保护）：仍采用精排第一名，保证派单不中断、尊重排名。
            eng = next((e for e in window_engineers if top_eid and e.id == top_eid), None)
            if eng:
                return AssignmentResult(
                    engineer_id=eng.id, engineer_name=eng.name,
                    confidence_score=round(float(top_score), 4),
                    reasoning="LLM decision failed; fell back to top1 by ranking (fault fallback).",
                    decision_type="fallback",
                )
            return None

    def _build_prompt(self, ticket, engineers, recall_result, ranked_scores, extra_hints=None):
        lines = [
            "你是本工单派单的『最终拍板决策者』。",
            "系统已通过部门过滤、召回与精排为你准备好了带依据的候选排名"
            "（见下方【候选人排名】：每个候选的总分、各维度分与精排原因）。",
            "精排是系统给你的最重要参考，但最终是否采纳这份排名、采纳哪位候选人，由你决定。",
            "你的职责：先通读工单与精排结果，做出最终的选人判断，并在 reasoning 里说明你最终采纳/调整的理由。",
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
            "【第三维度：工单类型（辅助你判断该派谁承接）】",
            "上游提单 Agent 给了一个初步类型（见下方工单区的 ticket_type），仅供参考；你可基于工单内容复核。"
            "这类判断帮助你理解工单性质与候选人匹配度，是支持你做最终拍板的信息之一。",
            "五类定义与边界（务必区分清楚，尤其 support 与 feature）：",
            "- support 咨询：询问使用方法/操作指导/配置协助，「不会用/怎么用/如何操作/需要指导」等；不新增功能、也不报故障。",
            "- feature 需求：希望新增/增加功能、提产品建议，「建议新增/希望支持/能不能加/增加一个」等。",
            "- bug 缺陷：功能本该有但行为错误/异常，与预期不符。",
            "- problem 报障：现场异常、故障报修、设备/系统出问题。",
            "- other 其他：无法归入以上四类（闲聊/感谢/无关内容）。",
            "承接参考：",
            "- 正常情况下以「精排 #1 为默认」（见【决策原则】），但你是最终决策者，可基于实质依据调整。",
            "- feature 需求可优先考虑负责「产品设计」模块、且归属产品与工单一致的候选人（产品设计师），"
            "但这只是改选理由之一（需匹配产品），不是强制规则；#1 若已合理匹配仍应保留。",
            "",

            "【候选人排名（已含职级折扣；#1 为总分最高，默认应优先考虑）】",
        ]

        emap = {e.id: e for e in engineers}
        for rank, (eid, d) in enumerate(list(ranked_scores.items())[:5], 1):
            eng = emap.get(eid)
            if not eng:
                continue
            dep = f"({eng.department})" if eng.department else ""
            lines.append(
                f"#{rank} ID:{eng.id} | L{eng.job_level} | {dep} "
                f"|{eng.modules_display()}"
            )
            lines.append(
                f"   分数: 总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} 语义={d.get('semantic_score',0):.2f} "
                f"历史={d.get('history_score',0):.2f}"
            )
            # 精排原因：说明该候选人为何排在当前位次，供决策者理解"排名依据"。
            # 主要依据各维度原始分 + 加权来源（职级/对接人/倾向人/部门）推导，不需要额外信息。
            raw_parts = []
            dims = [
                ("LLM", d.get("llm_score", 0.0)),
                ("语义", d.get("semantic_score", 0.0)),
                ("历史", d.get("history_score", 0.0)),
            ]
            if dims:
                top_dim, top_val = max(dims, key=lambda x: x[1])
                if top_val > 0:
                    raw_parts.append(f"主贡献={top_dim}({top_val:.2f})")
            boosts = []
            if d.get("preferred_assignee"):
                boosts.append("用户倾向人加权")
            if d.get("contact_assignee"):
                boosts.append("项目对接人加权")
            if d.get("dept_multiplier", 1.0) > 1.0:
                boosts.append(f"部门优先×{d.get('dept_multiplier')}")
            if d.get("level_multiplier", 1.0) < 1.0:
                boosts.append(f"职级×{d.get('level_multiplier')}")
            if boosts:
                raw_parts.append("提升=" + ",".join(boosts))
            if raw_parts:
                lines.append(f"   精排原因: {('; '.join(raw_parts))[:120]}")
            duty = (eng.duty_text or "")[:100]
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
        # 重新派单备注/转派原因作为重要决策上下文：让 LLM 审视"精排结果是否符合用户的转派要求"。
        # 备注一般为转派原因（原处理人不合适/需更合适/明确点名），用自然语言表达；
        # 结构化倾向人（用户明确勾选）由 adecide 分支 0 作为 extra_hints 传入，此处不重复。
        # 有备注 → 要求 LLM 作为最终决策者，结合备注审视精排 #1；无备注 → 正常拍板（默认采纳精排 #1）。
        _pref_remark = (getattr(ticket, "preferred_assignee_remark", "") or "").strip()
        if _pref_remark:
            _pref_lines = [
                "",
                "【用户重新派单意图（需纳入你的最终决策）】",
                f"用户转派原因/备注: {_pref_remark}",
                "作为最终决策者，请结合这段用户转派意图，审视精排 #1 是否符合用户的这一要求："
                "若 #1 已满足用户要求，则采纳 #1；"
                "若用户原因中明确点名/强烈倾向某位候选，且该候选在精排 Top-K 内、分数不至于显著过低，"
                "可决定改派给该人并在 reasoning 说明理由；"
                "若 #1 不符合用户要求但候选内没有明显更合适的，或原因未指向具体某人，"
                "仍以精排 #1 为准，并在 reasoning 说明为何未能满足/仅部分满足用户意图。",
            ]
            lines.extend(_pref_lines)

        # 额外决策线索（由 adecide 上游规则识别，作为 LLM 决策的参考提示，不直接定人选）：
        # 1) 结构化用户倾向处理人；2) 摇人吧模块总负责人。两者都由 LLM 在精排窗口内审视权衡。
        if extra_hints:
            lines.extend(["", "【额外决策线索（参考，非强制）】"])
            for hint in extra_hints:
                lines.append(f"- {hint}")

        lines.extend([
            "",
            "【决策原则】",
            "0. 你是最终拍板者：精排 #1（下方候选排名第一名）是系统给出的最强依据，正常情况下应采纳它。",
            "1. 你拥有最终决策权：若你结合工单内容、各维度判断或用户重派要求，认为应选择排名中其他候选人，"
            "你有权改选——但必须在 reasoning 中说明明确依据，且只能在下方面试名额（候选列表）内的人里选。",
            "2. 候选人 responsibility_modules 是平级模块清单，选人以「产品/模块匹配 + 精排分数」为准，"
            "不要因模块名带前端/后端而硬套技术分层。",
            "3. 有重派备注(【用户重新派单意图】)时：作为最终决策者，请审视精排 #1 是否符合用户这次转派的要求，"
            "再决定是否因此调整；正常情况下仍以精排 #1 为准，除非用户意图明确指向其他候选。",
            "4. 若你决定不采纳精排 #1，请给出清晰理由（如 #1 产品/模块明显不匹配、用户明确指定他人等），"
            "并在 reasoning 中说明最终所选候选相对 #1 的优势。",
            "5. 不要仅仅因为「你认为类型是 X」就更换人选；类型复核仅作为参考，改选仍需实质依据。",
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
