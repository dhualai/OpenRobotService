"""Assigner 核心逻辑：智能派单

流程：
    TicketContext + EngineerProfile
        │
        ▼
    【第一层: 规则过滤】默认只保留 level=1 的一线工程师
        │
        ▼
    【第二层: 多路召回（规则 + LLM 推断，无 Embedding 也可工作）】
        ├── 关键词召回: 模块召回 + 标签召回 + 历史召回
        └── 语义召回: Embedding 向量匹配工程师画像 + 历史任务
        │
        ▼
    【第三层: LLM 综合分析】
        ├── 输入: 工单信息 + 工程师画像 + 各路召回分数
        ├── LLM 输出: engineer_id, confidence_score, reasoning, decision_type
        └── 成功则直接返回，失败则触发回退
        │
        ▼（回退路径）
    【第四层: 规则精排 + 决策】
        ├── 精排评分: 固定权重多维度
        └── 决策: 基于阈值判定

所有外部依赖（LLM、Embedding）直接从 ai.core 获取单例。
"""

from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.config_loader import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.decision import DecisionMaker
from ai.agents.AiDiagnosisPlatform.assigner.llm_decider import LlmDecider
from ai.agents.AiDiagnosisPlatform.assigner.module_inferencer import ModuleInferencer
from ai.agents.AiDiagnosisPlatform.assigner.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall import MultiPathRecaller, RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.rule_filter import RuleFilter
from ai.agents.AiDiagnosisPlatform.assigner.semantic_recall import SemanticRecaller
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
)


class Assigner:
    """工单负责人推荐器（全异步架构）"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._rule_filter = RuleFilter()

        # 第二层：多路召回（LLM 推断 + 规则，不依赖 Embedding）
        self._module_inferencer = ModuleInferencer(config=self._config)
        self._recaller = MultiPathRecaller(
            module_inferencer=self._module_inferencer,
            config=self._config,
        )
        # 语义召回（使用 ai.core Embedding 单例）
        self._semantic_recaller = SemanticRecaller(config=self._config)

        # 第三层：LLM 综合分析
        self._llm_decider = LlmDecider(config=self._config)

        # 第四层：规则精排 + 决策（回退用）
        self._ranker = Ranker(config=self._config)
        self._decision_maker = DecisionMaker(config=self._config)

    async def aassign(
        self,
        ticket_context: TicketContext,
        engineer_profiles: List[EngineerProfile],
        historical_matches: Optional[Dict[str, float]] = None,
    ) -> AssignmentResult:
        """异步根据工单上下文推荐唯一负责人。"""
        if not engineer_profiles:
            raise ValueError("工程师列表为空，无法派单。请检查 engineers.json 是否加载成功。")

        if not ticket_context.problem_description and not ticket_context.title:
            raise ValueError("问题描述和标题均为空，无法推断责任模块。")

        # 第一层：规则过滤
        filtered = self._rule_filter.filter(
            ticket=ticket_context,
            engineers=engineer_profiles,
        )

        if not filtered:
            return self._decision_maker._fallback_result(
                engineer_profiles[0], "规则过滤后无可用工程师，强制兜底"
            )

        # 第二层：多路召回（异步：LLM 推断 + 关键词 + 历史）
        recall_result = await self._recaller.arecall(
            ticket=ticket_context,
            engineers=filtered,
            historical_matches=historical_matches,
        )

        # 语义召回（如果 Embedding 可用）
        try:
            semantic_result = await self._semantic_recaller.arecall(
                ticket=ticket_context,
                engineers=filtered,
            )
            recall_result.engineer_semantic = semantic_result.engineer_semantic
            recall_result.history_semantic = semantic_result.history_semantic
        except Exception:
            pass  # 语义召回失败不影响主流程

        # 第三层：LLM 综合分析（异步）
        try:
            ranked_scores = self._ranker.rank(recall_result)
            llm_result = await self._llm_decider.adecide(
                ticket=ticket_context,
                engineers=filtered,
                recall_result=recall_result,
                ranked_scores=ranked_scores,
            )
            if llm_result is not None:
                return llm_result
        except Exception:
            pass  # LLM 失败则回退

        # 第四层：回退到规则精排 + 决策
        ranked_scores = self._ranker.rank(recall_result)
        result = self._decision_maker.decide(
            ranked_scores=ranked_scores,
            engineers=filtered,
        )
        return result

    def reload_config(self):
        """热加载配置（可在运行时更新关键词/权重/Prompt）。"""
        self._config.reload()
        self._semantic_recaller.reload()
