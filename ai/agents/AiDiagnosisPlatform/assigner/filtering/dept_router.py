"""Layer 1 部门路由：判断工单所属部门并据此收紧候选。

部门判定由 LLM(部门职责画像) + 历史(相似工单) 融合打分，只有高置信才 hard_filter；
再经 R-Audit（独立 LLM 单轮复核）二次把关，确保不派错部门。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import DeptRoutingResult
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.llm_dept_signal import LlmDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.history_dept_signal import HistoryDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.dept_audit_signal import DeptAuditSignal
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class DeptRouter:
    """Layer 1 部门路由。

    流程：
    - LLM(部门职责画像) + 历史(相似工单) 融合打分 → primary 部门与模式。
    - R-Audit 独立 LLM 单轮复核"部门派得对不对"：
      审查高置信纠正则采纳；纠正不明确则带反馈打回重判 1 次；审查失败则保守降级。
    - 模式：hard_filter 确定部门并过滤；soft_prior 倾向不强制；no_filter 兜底不框部门。
      审查流程会尽量让工单收敛到单一部门（业务上每个部门负责的产品不同，需明确归属）。
    - 目标：不派错部门。
    """

    def __init__(self, config: Optional[AssignerConfig] = None):
        
        self._config = config or AssignerConfig()                                       # 派单配置对象（召回/权重/阈值等全部配置的统一入口）
        self._routing_cfg: Dict[str, Any] = self._config.department_routing or {}       # department_routing 配置块
        self._thresholds: Dict[str, Any] = self._routing_cfg.get("thresholds") or {}    # 部门判定阈值
        self._fusion: Dict[str, Any] = self._routing_cfg.get("fusion") or {}            # 融合修正参数
        self._history_bonus: float = float(self._fusion.get("history_bonus", 0.05))    # 历史明确偏向该部门(占比≥threshold)时佐证加分
        self._history_confirm_threshold: float = float(self._fusion.get("history_confirm_threshold", 0.5))  # 历史占比达到此值才视为"明确偏向"
        self._audit_cfg: Dict[str, Any] = self._routing_cfg.get("audit") or {}          # 审查参数
        self._llm: LlmDeptSignal = LlmDeptSignal(config=self._config)                   # 基于「部门职责画像(profile_text)」给出候选部门
        self._history: HistoryDeptSignal = HistoryDeptSignal(config=self._config)       # 按历史相似工单的解决部门聚合
        self._audit: DeptAuditSignal = DeptAuditSignal(config=self._config)             # 独立 LLM 单轮复核"部门派得对不对
        
    @staticmethod
    def _filter_by_dept(
        engineers: List[EngineerProfile], dept: str,
    ) -> List[EngineerProfile]:
        if not dept:
            return list(engineers)
        return [e for e in engineers if (e.department or "") == dept]

    @staticmethod
    @staticmethod
    def _fuse(
        llm_scores: Dict[str, float],
        hist_scores: Dict[str, float],
        history_bonus: float,
        history_confirm_threshold: float,
    ) -> Dict[str, float]:
        """部门分数融合：以 LLM(R2) 为基础分（不打折），历史(R3) 只做可加预确认。

        - 历史完全无数据 / 该部门无历史（hist<=0）→ final = llm（纯 LLM，不打折）。
        - 历史有数据但占比低于阈值（0<hist<threshold）→ 不是强佐证，仍维持 LLM 分（不加不减，
          避免"有部分历史反而扣分"的不对称；历史不覆盖=无信息、不干预）。
        - 历史明确偏向（hist >= history_confirm_threshold）→ 佐证加分 history_bonus。
        """
        depts = set(llm_scores) | set(hist_scores)
        if not depts:
            return {}
        n_hist = len([d for d in hist_scores if hist_scores.get(d, 0.0) > 0])
        merged: Dict[str, float] = {}
        for dept in depts:
            llm = llm_scores.get(dept, 0.0)
            hist = hist_scores.get(dept, 0.0)
            if n_hist <= 0 or hist <= 0:
                # 历史缺失 / 该部门无历史 → 纯 LLM 分，不打折
                merged[dept] = round(llm, 4)
            elif hist >= history_confirm_threshold:
                # 历史明确偏向该部门（占比达阈值）→ 佐证加分
                merged[dept] = round(min(1.0, llm + history_bonus), 4)
            else:
                # 历史有分但占比不足 → 非强佐证，维持 LLM 分（不加不减）
                merged[dept] = round(llm, 4)
        return merged

    @staticmethod
    def _top_two(scores: Dict[str, float]) -> Tuple[str, float, float]:
        if not scores:
            return "", 0.0, 0.0
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        primary, top = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        return primary, top, top - second

    def _decide_mode(self, primary: str, score: float, margin: float) -> str:
        if not primary:
            return "no_filter"
        hard_score = float(self._thresholds.get("hard_filter_score", 0.80))
        hard_margin = float(self._thresholds.get("hard_filter_margin", 0.15))
        soft_score = float(self._thresholds.get("soft_prior_score", 0.55))
        if score >= hard_score and margin >= hard_margin:
            return "hard_filter"
        if score >= soft_score:
            return "soft_prior"
        return "no_filter"

    async def route(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
    ) -> Tuple[List[EngineerProfile], DeptRoutingResult]:
        ltag = f"[派单:{ticket.id}]"
        result = DeptRoutingResult()

        # ── LLM(部门画像) + 历史(相似工单) 并行判部门 ──
        llm_scores, hist_scores = await asyncio.gather(
            self._llm.classify(ticket),
            self._history.aggregate(ticket, engineers),
        )
        result.signals["llm"] = llm_scores
        result.signals["history"] = hist_scores

        # 融合：LLM(R2) 为基础分（不打折），历史(R3) 做佐证修正（history_bonus / history_penalty）。
        # 历史缺失（本地无知识沉淀，hist=0）时不改变 LLM 的强判定——R2 单独高置信即可触发收紧。
        merged = self._fuse(
            llm_scores, hist_scores,
            self._history_bonus, self._history_confirm_threshold,
        )
        result.dept_scores = merged
        primary, score, margin = self._top_two(merged)
        result.primary_dept = primary
        result.confidence = score
        result.margin = margin
        result.mode = self._decide_mode(primary, score, margin)

        if primary:
            reasons = []
            if llm_scores.get(primary):
                reasons.append(f"R2={llm_scores[primary]:.2f}")
            if hist_scores.get(primary):
                reasons.append(f"R3={hist_scores[primary]:.2f}")
            result.reasoning = f"{primary}({'+'.join(reasons)}) mode={result.mode}"
            logger.info(
                f"{ltag} Layer1-部门 融合 → {primary} conf={score:.2f} "
                f"margin={margin:.2f} mode={result.mode}"
            )
        else:
            result.reasoning = "未命中部门路由"
            logger.info(f"{ltag} Layer1-部门 未命中 → no_filter")

        # ── 部门派发审查（post-validator）：独立 LLM 单轮复核"部门派得对不对" ──
        #   - 审查通过 → 维持；
        #   - 审查高置信纠正 → 采纳纠正部门（确定性归属）；
        #   - 审查判错但纠正不明确 → 带审查反馈打回重判 1 次；
        #   - 审查失败（LLM 异常）→ 保守降级，不强制硬过滤。
        if (
            primary
            and bool(getattr(self._config, "dept_audit_enabled", True))
        ):
            audit = await self._audit.audit(ticket, primary)
            result.signals["audit"] = audit
            audit_min_conf = float(self._audit_cfg.get("min_confidence", 0.6))
            if audit.audit_failed:
                if result.mode == "hard_filter":
                    logger.warning(f"{ltag} 部门审查失败，hard_filter 降级 no_filter（保守）")
                    result.mode = "no_filter"
            elif audit.ok:
                logger.info(f"{ltag} 部门审查通过 → 维持 {primary}")
            elif audit.correct_dept and audit.confidence >= audit_min_conf:
                # 审查高置信给出纠正部门且合法 → 采纳
                logger.info(
                    f"{ltag} 部门审查纠正 {primary} → {audit.correct_dept} "
                    f"(conf={audit.confidence:.2f}) {audit.reason[:40]}"
                )
                primary = audit.correct_dept
                result.primary_dept = primary
                result.confidence = audit.confidence
                result.mode = "hard_filter"
                result.signals["audit_corrected"] = True
            else:
                # 审查判错但纠正不明确 → 打回重判 1 次（带审查反馈），最多一次
                feedback = (
                    f"上一轮判定为「{primary}」，审查认为可能不对：{audit.reason or ''}。"
                    "请重新判定，务必结合部门职责画像。"
                )
                try:
                    llm2, hist2 = await asyncio.gather(
                        self._llm.classify(ticket, feedback=feedback),
                        self._history.aggregate(ticket, engineers),
                    )
                    merged2 = self._fuse(llm2, hist2, self._history_bonus, self._history_confirm_threshold)
                    p2, s2, m2 = self._top_two(merged2)
                    if p2 and p2 != primary:
                        logger.info(f"{ltag} 部门审查打回重判 {primary} → {p2} (conf={s2:.2f})")
                        primary, score, margin = p2, s2, m2
                        result.primary_dept = primary
                        result.confidence = score
                        result.margin = margin
                        result.mode = self._decide_mode(p2, s2, m2)
                        result.signals["audit_redone"] = True
                    else:
                        # 重判后仍回到原部门：确保至少落到一个部门（单一归属）
                        logger.info(
                            f"{ltag} 部门审查打回重判后仍为 {primary} → 确定为该部门"
                        )
                        result.mode = "hard_filter"
                except Exception as e:
                    logger.warning(f"{ltag} 部门审查打回重判失败: {e}")
                    if result.mode == "hard_filter":
                        result.mode = "soft_prior"

        if result.mode == "hard_filter" and primary:
            filtered = self._filter_by_dept(engineers, primary)
            if filtered:
                logger.info(
                    f"{ltag} Layer1-部门 hard_filter {len(engineers)}→{len(filtered)}人"
                )
                return filtered, result
            logger.warning(f"{ltag} Layer1-部门 hard_filter 无候选人，回退全量")
            result.mode = "no_filter"

        return list(engineers), result
