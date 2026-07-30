"""Assigner 核心逻辑：智能派单

流程:
    TicketContext + EngineerProfile
        │
        ▼
    【Step 0 部门过滤】(极保守:仅服务号→智能规划)
        │
        ▼
    【Step 1 四路召回】
        ├── L1 纯LLM召回(0.50): LLM 看全员画像 → 直接打分
        ├── L2 关键词召回(0.00): LLM推断模块 → Jaccard(已关闭)
        ├── L3 语义召回(0.40):   Embedding 向量余弦相似度
        └── L4 历史召回(0.10):   tasks 表已解决工单匹配(数据积累中)
        │
        ▼
    【Step 2 精排 + 职级折扣】 raw_total × job_level 惩罚系数
        │
        ▼
    【Step 3 LLM 综合决策】成功→返回 / 失败→Step 4
        │
        ▼ (回退)
    【Step 4 规则决策】阈值判定: auto/recommend/fallback
"""

from typing import Dict, List, Optional

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.ranking.decision import DecisionMaker
from ai.agents.AiDiagnosisPlatform.assigner.filtering.department_matcher import DepartmentMatcher
from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decider import LlmDecider
from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recaller import LlmRecaller
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.recall.semantic_recaller import (
    SemanticRecaller, invalidate_semantic_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)

logger = get_logger("ASSIGNER")


class Assigner:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._dept_matcher = DepartmentMatcher(config=self._config)
        self._llm_recaller = LlmRecaller(config=self._config)
        self._semantic_recaller = SemanticRecaller(config=self._config)
        self._ranker = Ranker(config=self._config)
        self._llm_decider = LlmDecider(config=self._config)
        self._decision_maker = DecisionMaker(config=self._config)

    async def aassign(
        self,
        ticket_context: TicketContext,
        engineer_profiles: List[EngineerProfile],
        historical_matches: Optional[Dict[str, float]] = None,
    ) -> AssignmentResult:
        desc_preview = (ticket_context.problem_description or "")[:100].replace("\n", " ")
        logger.info(
            f"派单开始: [{ticket_context.title[:50]}] "
            f"desc={desc_preview} "
            f"fault={ticket_context.fault_code or '-'} robot={ticket_context.robot_type or '-'} "
            f"engineers={len(engineer_profiles)}人"
        )
        if not engineer_profiles:
            raise ValueError("工程师列表为空。请检查 users 表人员数据是否就绪。")
        if not ticket_context.problem_description and not ticket_context.title:
            raise ValueError("问题描述和标题均为空，无法推断责任模块。")

        # ── Step 0: 极保守部门过滤 ──
        candidates = self._dept_matcher.filter(
            ticket=ticket_context, engineers=engineer_profiles,
            project_name=ticket_context.project_name or "",
        )
        if not candidates:
            logger.warning("派单 Step 0: 过滤后无候选人，回退全量")
            candidates = engineer_profiles
        logger.info(f"派单 Step 0 部门过滤: {len(engineer_profiles)}→{len(candidates)}人")

        # ── Step 1: 三路召回 ──
        recall_result = RecallResult()
        # L1 纯LLM 召回
        try:
            recall_result.llm_recall = await self._llm_recaller.arecall(
                ticket=ticket_context, engineers=candidates,
            )
            logger.debug(f"派单 L1 LLM召回: {len(recall_result.llm_recall)} 人")
        except Exception:
            pass
        # L3 语义 + L4 历史（共享一次 Embedding）
        try:
            sem, his = await self._semantic_recaller.arecall(
                ticket=ticket_context, engineers=candidates,
            )
            recall_result.semantic_recall = sem
            recall_result.history_recall = his
            logger.debug(f"派单 L3语义:{len(sem)}人 L4历史:{len(his)}人")
        except Exception:
            pass

        # ── Step 2: 精排 + 职级折扣 ──
        ranked_scores = self._ranker.rank(recall_result, engineers=candidates)

        # ── Step 3: LLM 综合决策 ──
        result: Optional[AssignmentResult] = None
        decision_source = ""
        try:
            llm_result = await self._llm_decider.adecide(
                ticket=ticket_context, engineers=candidates,
                recall_result=recall_result, ranked_scores=ranked_scores,
            )
            if llm_result is not None:
                result = llm_result
                decision_source = "LLM决策"
        except Exception as e:
            logger.warning(f"派单 Step 3 LLM决策失败,回退: {e}")

        # ── Step 4: 规则兜底 ──
        if result is None:
            result = self._decision_maker.decide(ranked_scores=ranked_scores, engineers=candidates)
            decision_source = "规则兜底"

        # ── 结果汇总日志（含工单描述 + 被派人完整画像）──
        self._log_assignment_result(
            ticket=ticket_context,
            result=result,
            candidates=candidates,
            ranked_scores=ranked_scores,
            source=decision_source,
        )
        return result

    def _log_assignment_result(
        self,
        ticket: TicketContext,
        result: AssignmentResult,
        candidates: List[EngineerProfile],
        ranked_scores: Dict[str, Dict[str, float]],
        source: str,
    ):
        """打印派单结果汇总日志（工单 + 被派人完整画像 + Top3 排名）"""
        # ── 被派人完整画像 ──
        winner = next((e for e in candidates if e.id == result.engineer_id), None)
        if winner:
            modules_flat = []
            for prod, mods in winner.responsibility_modules.items():
                modules_flat.append(f"{prod}={','.join(mods)}" if mods else prod)
            modules_str = " | ".join(modules_flat) if modules_flat else "-"
            duty = (winner.duty_text or "")[:120].replace("\n", " ")
            scores = ranked_scores.get(winner.id, {})
            logger.info(
                f"派单结果 [{source}] | "
                f"工单: {ticket.title[:60]} | "
                f"描述: {(ticket.problem_description or '')[:120].replace(chr(10), ' ')} | "
                f"故障码={ticket.fault_code or '-'} 车型={ticket.robot_type or '-'} | "
                f"→ 指派: {winner.name} "
                f"users.id={winner.id} "
                f"部门={winner.department or '-'} "
                f"职级=L{winner.job_level} "
                f"模块=[{modules_str}] "
                f"职责={duty} | "
                f"置信度={result.confidence_score:.0%} "
                f"决策={result.decision_type} "
                f"LLM分={scores.get('llm_score', 0):.2f} "
                f"语义分={scores.get('semantic_score', 0):.2f} "
                f"历史分={scores.get('history_score', 0):.2f} "
                f"总分={scores.get('total_score', 0):.2f}"
            )

        # ── Top3 排名 ──
        top3 = list(ranked_scores.items())[:3]
        if top3:
            rank_lines = []
            for rank, (eid, d) in enumerate(top3, 1):
                eng = next((e for e in candidates if e.id == eid), None)
                name = eng.name if eng else eid[:8]
                rank_lines.append(
                    f"#{rank} {name}(L{d.get('job_level','?')}) "
                    f"总={d.get('total_score',0):.2f} "
                    f"LLM={d.get('llm_score',0):.2f} "
                    f"语义={d.get('semantic_score',0):.2f}"
                )
            logger.info(f"派单排名 Top3: {' | '.join(rank_lines)}")
        else:
            logger.info("派单排名: 无候选排名数据")

    def reload_config(self):
        self._config.reload()
        invalidate_semantic_cache()
