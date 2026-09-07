"""LLM 最终决策层（Step6）：铁律 + 产品附录；找不到人返回 None，不回精排 #1。"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import (
    llm_person_label,
    match_engineer_id_strict,
    recall_source_label,
    score_tag_labels,
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

_YAORENBA_MODULE_MAP = {
    "我要摇人": ["我要摇人", "摇人界面", "摇人页面", "摇人"],
    "系统任务": ["系统任务", "任务界面", "收件箱", "工单收件箱"],
    "后台管理": ["后台管理", "管理后台", "权限", "看板", "数据统计"],
    "agent": ["agent", "ai", "ai诊断", "提单agent", "摇人agent", "机器人agent", "智能派单", "llm", "u老师"],
    "数据分析": ["日报", "周报", "数据分析", "数据看板", "统计"],
}


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
        倾向人 / 原处理人由 Step2 打在候选人标签上；备注有才单独带一句。
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

    def _window_k(self, n: int) -> int:
        """仲裁窗口人数。llm_decision_topk<=0 → 精排全量不截。"""
        try:
            k = int(getattr(self._config, "llm_decision_topk", 0))
        except (TypeError, ValueError):
            k = 0
        if n <= 0:
            return 0
        if k <= 0:
            return n
        return min(k, n)

    def _resolve_product(self, ticket, product: str = "") -> str:
        """附录用产品名。优先用 Step1 已判的产品，否则按项目名对四套产品。"""
        given = (product or "").strip()
        if given:
            return given
        if self._is_yaorenba_intake(ticket):
            return "摇人吧服务号"
        blob = (getattr(ticket, "project_name", None) or "").replace(" ", "").replace("\u3000", "")
        low = blob.lower()
        if "车端硬件" in blob:
            return "车端硬件"
        if "车端软件" in blob:
            return "车端软件"
        if "车端" in blob:
            return "车端软件"
        if "usp" in low or "调度" in blob:
            return "调度USP"
        return ""

    @staticmethod
    def _cannot_decide(data: dict) -> bool:
        v = data.get("can_decide", True)
        if v is False:
            return True
        if isinstance(v, str) and v.strip().lower() in ("false", "0", "no", "否"):
            return True
        return False

    def _yaorenba_owner_lines(self, ticket, engineers, ranked_scores) -> List[str]:
        """摇人吧附录附加：命中子界面时列参考负责人，不强制。"""
        lines: List[str] = []
        try:
            enable = bool(self._config.yaorenba_force_module_owner)
        except Exception:
            enable = True
        if not enable:
            return lines
        text = (
            (getattr(ticket, "title", "") or "") + " \n "
            + (getattr(ticket, "problem_description", "") or "")
        ).lower()
        detected = []
        for mod_key, keywords in _YAORENBA_MODULE_MAP.items():
            if any(kw in text for kw in keywords):
                detected.append(mod_key)
        if not detected:
            return lines

        def score_of(e):
            return float(ranked_scores.get(e.id, {}).get("total_score", 0.0))

        for mod in detected:
            owners, members = [], []
            for eng in engineers:
                duty = (eng.duty_text or "").lower()
                mods = [m.lower() for m in (eng.all_modules() or [])]
                is_owner = False
                if mod != "agent":
                    if mod in duty and ("总负责" in duty or "总负责人" in duty):
                        is_owner = True
                else:
                    if any(x in duty for x in ("算法", "模型", "ai", "ml", "mlops")) and (
                        "总负责" in duty or "总负责人" in duty
                    ):
                        is_owner = True
                if is_owner:
                    owners.append(eng)
                    continue
                if mod != "agent":
                    if any(mod in m for m in mods):
                        members.append(eng)
                elif any(x in m for x in ("算法", "ai", "ml", "agent") for m in mods):
                    members.append(eng)
            chosen = max(owners, key=score_of) if owners else (
                max(members, key=score_of) if members else None
            )
            if chosen:
                lines.append(
                    f"- 子界面「{mod}」可参考: {llm_person_label(eng=chosen)}"
                    f"（总分={score_of(chosen):.2f}）"
                )
        return lines

    def _tree_interfaces(self, product: str) -> List[str]:
        tree = getattr(self._config, "module_tree", None) or {}
        node = tree.get(product) or {}
        ifaces = node.get("interfaces") or []
        names = []
        for it in ifaces:
            if isinstance(it, dict):
                name = (it.get("name") or "").strip()
            else:
                name = str(it).strip()
            if name and name not in names:
                names.append(name)
        return names

    def _appendix_lines(self, ticket, engineers, ranked_scores, product: str = "") -> List[str]:
        from ai.agents.AiDiagnosisPlatform.assigner.prompts.step6 import build_product_appendix

        resolved = self._resolve_product(ticket, product)
        extra: List[str] = []
        if resolved == "摇人吧服务号":
            extra = self._yaorenba_owner_lines(ticket, engineers, ranked_scores)
        text = build_product_appendix(
            resolved,
            extra_lines=extra,
            interfaces=self._tree_interfaces(resolved) if resolved else None,
        )
        return text.splitlines()

    def _log_decision_basis(self, ticket, result, recall_result, ranked_scores):
        """选中某人后记依据（L1 / 错派 / tag），不进 tip、不进 reasoning。"""
        eid = result.engineer_id
        d = (ranked_scores or {}).get(eid) or {}
        tags = score_tag_labels(d)
        l1 = (getattr(recall_result, "llm_reasons", None) or {}).get(eid) or ""
        pen = ((getattr(recall_result, "transfer_signals", None) or {}).get("penalties") or {}).get(eid) or {}
        wrong = (pen or {}).get("reason") or ""
        logger.info(
            f"[派单:{getattr(ticket, 'id', '?')}] Step6 依据: "
            f"L1原因={l1 or '-'} / 错派={wrong or '-'} / tags={tags or '-'}（不进 tip）"
        )

    async def adecide(self, ticket, engineers, recall_result, ranked_scores, product: str = ""):
        """Step6 统一 LLM 最终决策入口。

        合法出口：选中某人，或 None（很难决策 / 调用失败 / 名单外 id / 窗口空）。
        本层禁止回精排 #1。
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
            topk = self._window_k(len(items))
        except Exception:
            topk = len(items)
        emap_diag = {e.id: e for e in engineers}
        # 窗口 = 精排前 topk；topk 等于人数时即全量
        window_names = [
            f"{emap_diag[eid].name if eid in emap_diag else eid[:8]}"
            f"(总={ranked_scores[eid].get('total_score',0):.2f},LLM={ranked_scores[eid].get('llm_score',0):.2f})"
            for eid, _ in items[:topk]
        ]
        # 被截出窗口的人（本版默认不截，outside 为空）
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
            f"second={second_score:.2f} | 低分阈值={low_score_threshold} "
            f"窗口={'全量' if topk >= len(items) else f'Top{topk}'}({topk}人) "
            f"| 窗口内=[{', '.join(window_names)}] | 窗口外LLM最高=[{outside_str}]"
        )

        # 结构化倾向人只打日志；重派意图已在 prompt 正文。摇人吧负责人改走产品附录。
        try:
            strong_match = self._resolve_redispatch_strong(ticket, engineers, ranked_scores)
        except Exception:
            strong_match = None
        if strong_match is not None:
            s = float(ranked_scores.get(strong_match.id, {}).get("total_score", 0.0))
            logger.info(
                f"[派单:{getattr(ticket,'id','?')}] Step6 用户倾向处理人={strong_match.name} "
                f"总分={s:.2f} → 交由LLM协商（附录/标签，非强制）"
            )

        # ── 统一决策：精排全量（或配置的 Top-K）进 LLM ──
        topk = self._window_k(len(list(ranked_scores.items())))

        # 构造窗口：本版默认全量；旧配置 llm_decision_topk>0 时仍可截。
        top_items = list(ranked_scores.items())[:topk]
        window_ranked = dict(top_items)
        window_engineers = []
        emap = {e.id: e for e in engineers}
        for eid, _ in top_items:
            eng = emap.get(eid)
            if eng is not None:
                window_engineers.append(eng)
        if not window_engineers:
            logger.warning(
                f"[派单:{getattr(ticket,'id','?')}] Step6 窗口为空 → None（不回精排#1）"
            )
            return None

        prompt = self._build_prompt(
            ticket, window_engineers, recall_result, window_ranked,
            product=product,
        )
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=400, temperature=0.3)
            logger.info(
                f"[派单:{getattr(ticket,'id','?')}] Step6 LLM最终决策原始输出: {response[:500]}"
            )
            result = self._parse(response, window_engineers)
            if result is None:
                logger.info(
                    f"[派单:{getattr(ticket,'id','?')}] Step6 交不出人"
                    f"（can_decide=false / 名单外 id / 解析失败）→ None"
                )
                return None
            self._log_decision_basis(ticket, result, recall_result, window_ranked)
            return result
        except Exception as e:
            logger.warning(
                f"[派单:{getattr(ticket,'id','?')}] Step6 LLM最终决策失败: {e} → None（不回精排#1）"
            )
            return None

    def _build_prompt(self, ticket, engineers, recall_result, ranked_scores, extra_hints=None, product: str = ""):
        from ai.agents.AiDiagnosisPlatform.assigner.prompts.step6 import (
            IRON_RULES,
            JUDGE_HINTS,
            OUTPUT_CONTRACT,
        )
        lines = [
            "你是本工单派单的『最终拍板决策者』。",
            "系统已通过召回与精排准备好带依据的候选排名。精排是最强参考，最终选谁由你决定。",
            "",
            IRON_RULES,
            "",
            JUDGE_HINTS,
            "",
            "【候选人排名（已含职级折扣；#1 为总分最高）】",
        ]

        emap = {e.id: e for e in engineers}
        # 给大模型看的人一律 姓名: + ID:；给提单人的 reasoning 再洗成姓名（见 _parse）。
        for rank, (eid, d) in enumerate(list(ranked_scores.items()), 1):
            eng = emap.get(eid)
            if not eng:
                continue
            dep = f"({eng.department})" if eng.department else ""
            tags = [f"[{t}]" for t in score_tag_labels(d)]
            tag_str = (" " + " ".join(tags)) if tags else ""
            # 职级语义：L1 一线 / L2 管理·审核 / L3 最高（兜底）。数字越大职级越高、越是上级，
            # 供 LLM 在用户重派备注提到"上报上级/请领导"时据此选择更合适职级的人。
            _lv_txt = {
                1: "L1一线",
                2: "L2管理·审核",
                3: "L3最高·兜底",
            }.get(int(eng.job_level or 1), f"L{eng.job_level}")
            lines.append(
                f"#{rank} {llm_person_label(eng=eng)} | {_lv_txt} | {dep} "
                f"|{eng.modules_display()}{tag_str}"
            )
            lines.append(
                f"   分数: 总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} "
                f"相似={d.get('similar_score', d.get('history_score',0)):.2f} "
                f"簇={d.get('cluster_score',0):.2f}"
            )
            lines.append(f"   {recall_source_label(d)}")
            # 精排原因：说明该候选人为何排在当前位次，供决策者理解"排名依据"。
            # 主要依据各维度原始分 + 加权来源（职级/对接人/倾向人/部门）推导，不需要额外信息。
            raw_parts = []
            dims = [
                ("LLM", d.get("llm_score", 0.0)),
                ("相似", d.get("similar_score", d.get("history_score", 0.0))),
                ("簇", d.get("cluster_score", 0.0)),
            ]
            if dims:
                top_dim, top_val = max(dims, key=lambda x: x[1])
                if top_val > 0:
                    raw_parts.append(f"主贡献={top_dim}({top_val:.2f})")
            boosts = []
            if d.get("preferred_assignee"):
                fl = d.get("preferred_floor")
                boosts.append(f"倾向接单人保底≥{fl}" if fl else "倾向接单人保底")
            if d.get("dept_multiplier", 1.0) > 1.0:
                boosts.append(f"部门优先×{d.get('dept_multiplier')}")
            if d.get("level_multiplier", 1.0) < 1.0:
                boosts.append(f"职级×{d.get('level_multiplier')}")
            if boosts:
                raw_parts.append("提升=" + ",".join(boosts))
            if raw_parts:
                lines.append(f"   精排原因: {('; '.join(raw_parts))[:120]}")
            l1_reason = (getattr(recall_result, "llm_reasons", None) or {}).get(eid) or ""
            if l1_reason:
                lines.append(f"   L1原因: {l1_reason[:120]}")
            duty = (eng.duty_text or "")[:100]
            if duty:
                lines.append(f"   职责: {duty}")

        from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import ticket_fields_block
        lines.extend(["", ticket_fields_block(ticket).rstrip()])
        # 倾向人 / 原处理人已在排名行的 Step2 标签上；有倾向人时补一句采纳口径，备注有才带。
        has_pref = any(
            (d or {}).get("preferred_assignee")
            for d in (ranked_scores or {}).values()
        ) or bool((getattr(ticket, "preferred_assignee", "") or "").strip())
        remark = (getattr(ticket, "preferred_assignee_remark", "") or "").strip()
        if has_pref or remark:
            extra = [""]
            if has_pref:
                extra.append(
                    "名单中带 [倾向接单人] 的是用户勾选。"
                    "正常情况不要拒绝这一选择，除非另有非常合适的人。"
                )
            if remark:
                extra.append(f"用户重派备注：{remark}")
            lines.extend(extra)

        appendix = self._appendix_lines(ticket, engineers, ranked_scores, product=product)
        if appendix:
            lines.extend([""] + appendix)

        lines.extend(["", OUTPUT_CONTRACT])
        return "\n".join(lines)

    def _parse(self, response, engineers):
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
        if self._cannot_decide(data):
            return None
        raw = (data.get("engineer_id") or "").strip()
        eng = match_engineer_id_strict(raw, engineers)
        if not eng:
            return None
        dt = (data.get("decision_type") or "fallback").strip().lower()
        reason = (data.get("reasoning") or "").strip()

        if reason:
            for e in engineers:
                reason = reason.replace(llm_person_label(eng=e), e.name)
                reason = reason.replace(f"ID:{e.id}", e.name)
                reason = reason.replace(f"({e.id})", f"({e.name})")
                reason = reason.replace(f"（{e.id}）", f"（{e.name}）")
                reason = reason.replace(e.id, e.name)

        return AssignmentResult(
            engineer_id=eng.id, engineer_name=eng.name,
            confidence_score=round(float(data.get("confidence_score", 0.0)), 4),
            reasoning=reason,
            decision_type=dt if dt in ("auto", "recommend", "fallback") else "fallback",
        )
