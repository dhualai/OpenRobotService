"""Assigner 核心逻辑：智能派单

流程：
    TicketContext + EngineerProfile
        │
        ▼
    【Step 0: 极保守部门过滤】仅服务号→智能规划（100%确定），其余全量参与
        │ 宁可不过滤，不可过滤错
        │ "车卡住不动"可能车端可能调度 → 不过滤
        ▼
    【Step 1: 多路召回（规则 + LLM 推断 + Embedding）】
        ├── 模块召回: LLM 推断工单模块 → Jaccard 匹配工程师责任模块
        ├── 历史召回: tasks 表已解决工单关键词命中
        └── 语义召回: Embedding 向量匹配工程师画像 + 历史任务
        │
        ▼
    【Step 2: 精排评分 + 职级折扣】
        ├── 模块 0.40 + 历史 0.35 + 语义 0.25 → raw_total
        └── raw_total × job_level 惩罚系数 → total_score
        │
        ▼
    【Step 3: LLM 综合分析】
        ├── 输入: 工单信息 + 工程师画像 + 各路召回分数 + 职级折扣
        ├── LLM 输出: engineer_id, confidence_score, reasoning, decision_type
        └── 成功则直接返回，失败则触发规则回退
        │
        ▼（回退路径）
    【Step 4: 规则决策】
        ├── 基于 ranked_scores + 阈值判定
        └── auto(≥0.8) / recommend(≥0.5) / fallback(<0.5)
"""

from typing import Dict, List, Optional

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.decision import DecisionMaker
from ai.agents.AiDiagnosisPlatform.assigner.department_matcher import DepartmentMatcher
from ai.agents.AiDiagnosisPlatform.assigner.llm_decider import LlmDecider
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall import MultiPathRecaller, RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.semantic_recall import SemanticRecaller
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)

logger = get_logger("assigner")


class Assigner:
    """工单负责人推荐器（全异步架构）"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

        self._dept_matcher = DepartmentMatcher(config=self._config)
        self._module_inferencer = ModuleInferencer(config=self._config)
        self._recaller = MultiPathRecaller(
            module_inferencer=self._module_inferencer,
            config=self._config,
        )
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
        logger.info(f"派单开始: ticket={ticket_context.title[:40]}, engineers={len(engineer_profiles)}人")
        if not engineer_profiles:
            raise ValueError("工程师列表为空，无法派单。请检查 users 表人员数据是否就绪。")
        if not ticket_context.problem_description and not ticket_context.title:
            raise ValueError("问题描述和标题均为空，无法推断责任模块。")

        # ── Step 0: 部门+产品硬过滤 ──
        candidates = self._dept_matcher.filter(
            ticket=ticket_context,
            engineers=engineer_profiles,
            project_name=ticket_context.project_name or "",
        )
        if not candidates:
            logger.warning("派单 Step 0: 部门过滤后无候选人，回退全量")
            candidates = engineer_profiles
        logger.info(f"派单 Step 0 部门过滤: {len(engineer_profiles)}→{len(candidates)}人")

        # ── Step 1: 多路召回 ──
        recall_result = await self._recaller.arecall(
            ticket=ticket_context, engineers=candidates,
            historical_matches=historical_matches,
        )
        try:
            semantic_result = await self._semantic_recaller.arecall(
                ticket=ticket_context, engineers=candidates,
            )
            recall_result.engineer_semantic = semantic_result.engineer_semantic
            recall_result.history_semantic = semantic_result.history_semantic
        except Exception:
            pass

        # ── Step 2: 精排 + 职级折扣 ──
        ranked_scores = self._ranker.rank(recall_result, engineers=candidates)

        # ── Step 3: LLM 决策 ──
        try:
            llm_result = await self._llm_decider.adecide(
                ticket=ticket_context, engineers=candidates,
                recall_result=recall_result, ranked_scores=ranked_scores,
            )
            if llm_result is not None:
                logger.info(f"派单 Step 3 LLM决策: {llm_result.engineer_name} ({llm_result.decision_type})")
                return llm_result
        except Exception as e:
            logger.warning(f"派单 Step 3 LLM决策失败,回退: {e}")

        # ── Step 4: 规则兜底 ──
        result = self._decision_maker.decide(
            ranked_scores=ranked_scores, engineers=candidates,
        )
        logger.info(f"派单 Step 4 规则兜底: {result.engineer_name} ({result.decision_type})")
        return result

    def reload_config(self):
        self._config.reload()
        self._semantic_recaller.reload()
