"""DispatchFlow 核心逻辑：智能派单主流程

流程:
    TicketContext + EngineerProfile
        │
        ▼
    【Step -1 提单人指定】(LLM 检测"转给张三" → 直接指派)
        │ (未指定)
        ▼
    【Step 0 部门过滤】(关键词匹配 → 过滤非本部门候选人)
        │
        ▼
    【Step 1 三路召回】
        ├── L1 纯LLM召回(0.70): LLM 看全员画像 → 直接打分
        ├── L2 语义召回(0.20):   Embedding 工单 → 模块锚文本 → 反查工程师
        └── L3 历史召回(0.10):   tasks 表已解决工单匹配(数据积累中)
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

import json, re
from typing import Dict, List, Optional

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.ranking.fallback_decision import FallbackDecision
from ai.agents.AiDiagnosisPlatform.assigner.filtering.department_filter import DepartmentFilter
from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recall import LlmRecall
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.recall.semantic_recall import (
    SemanticRecall, invalidate_semantic_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import (
    HistoryRecall, invalidate_history_cache,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult, EngineerProfile, TicketContext,
)

logger = get_logger("ASSIGNER")


class DispatchFlow:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._dept_filter = DepartmentFilter(config=self._config)
        self._llm_recall = LlmRecall(config=self._config)
        self._semantic_recall = SemanticRecall(config=self._config)
        self._history_recall = HistoryRecall(config=self._config)
        self._ranker = Ranker(config=self._config)
        self._llm_decision = LlmDecision(config=self._config)
        self._fallback_decision = FallbackDecision(config=self._config)

    async def aassign(
        self,
        ticket_context: TicketContext,
        engineer_profiles: List[EngineerProfile],
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

        # ── Step -1: LLM 识别提单人是否指定了期望接单人 ──
        preferred = await self._detect_preferred_assignee(ticket_context, engineer_profiles)
        if preferred is not None:
            self._log_assignment_result(
                ticket=ticket_context, result=preferred,
                candidates=engineer_profiles, ranked_scores={},
                source="提单人指定",
            )
            return preferred

        # ── Step 0: 部门过滤 ──
        candidates = self._dept_filter.filter(
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
            recall_result.llm_recall = await self._llm_recall.arecall(
                ticket=ticket_context, engineers=candidates,
            )
            logger.debug(f"派单 L1 LLM召回: {len(recall_result.llm_recall)} 人")
        except Exception as e:
            logger.warning(f"派单 L1 LLM召回异常: {e}")
        # L2 语义召回
        try:
            recall_result.semantic_recall = await self._semantic_recall.arecall(
                ticket=ticket_context, engineers=candidates,
            )
            logger.debug(f"派单 L2语义召回: {len(recall_result.semantic_recall)} 人")
        except Exception:
            pass
        # L3 历史召回
        try:
            recall_result.history_recall = await self._history_recall.arecall(
                ticket=ticket_context,
            )
            logger.debug(f"派单 L3历史召回: {len(recall_result.history_recall)} 人")
        except Exception:
            pass

        # ── Step 2: 精排 + 职级折扣 ──
        ranked_scores = self._ranker.rank(recall_result, engineers=candidates)

        # ── Step 3: LLM 综合决策 ──
        result: Optional[AssignmentResult] = None
        decision_source = ""
        try:
            llm_result = await self._llm_decision.adecide(
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
            result = self._fallback_decision.decide(ranked_scores=ranked_scores, engineers=candidates)
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

    # ── Step -1 实现: LLM 识别提单人期望 + 姓名匹配 ──
    async def _detect_preferred_assignee(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Optional[AssignmentResult]:
        """LLM 识别提单人是否明确指定了期望接单人。

        工单描述中常见表达：
        - "这个给张三看一下" / "让李四处理" / "请王五帮忙看看"
        - "转给赵六" / "最好是钱七来搞" / "这个问题周八比较熟"

        Returns: 匹配成功返回 AssignmentResult，未指定/未匹配返回 None（继续走正常派单）。
        """
        text = f"标题: {ticket.title or ''}\n描述: {ticket.problem_description or ''}"
        prompt = (
            '分析以下工单内容，判断提单人是否明确表达了”希望由谁处理”的意图。\n'
            '\n'
            '典型表达（不限于此）：\n'
            '- “这个给张三看一下” / “让李四处理” / “请王五帮忙看看”\n'
            '- “转给赵六” / “最好是钱七来搞” / “这个问题周八比较熟”\n'
            '- “找某某某” / “某某某有空吗” / “安排给某某某”\n'
            '- “需提给某某某” / “提给某某某” / “需要某某某看一下”\n'
            '- “这个某某某负责” / “某某某来搞” / “派给某某某”\n'
            '\n'
            f'{text}\n'
            '\n'
            '只关注中文人名，忽略”U老师””小U””系统””admin”等非人名。\n'
            '输出 JSON：{“has_preference”: true/false, “preferred_name”: “姓名”}\n'
            'has_preference=false 时 preferred_name 填 null。'
        )

        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=120, temperature=0.1)
        except Exception as e:
            logger.warning(f"派单 Step -1 LLM 识别失败: {e}")
            return None

        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            logger.debug(f"派单 Step -1 无 JSON，raw: {response[:150]}")
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.debug(f"派单 Step -1 JSON 解析失败，raw: {response[:200]}")
            return None

        if not data.get("has_preference"):
            return None

        preferred_name = (data.get("preferred_name") or "").strip()
        if not preferred_name:
            return None

        # 匹配工程师名
        matched = self._match_engineer_by_name(preferred_name, engineers)
        if not matched:
            logger.info(
                f"派单 Step -1: 提单人指定 '{preferred_name}'，"
                f"未匹配到工程师，走正常派单"
            )
            return None

        logger.info(
            f"派单 Step -1 [提单人指定]: '{preferred_name}'"
            f" → {matched.name}({matched.id})"
        )
        return AssignmentResult(
            engineer_id=matched.id,
            engineer_name=matched.name,
            confidence_score=0.95,
            reasoning=f"提单人指定接单人: {preferred_name} → 匹配 {matched.name}",
            decision_type="auto",
        )

    @staticmethod
    def _match_engineer_by_name(
        name: str, engineers: List[EngineerProfile],
    ) -> Optional[EngineerProfile]:
        """按姓名匹配工程师：精确 > 包含/被包含"""
        if not name:
            return None
        # 1. 精确匹配 name
        for e in engineers:
            if e.name == name:
                return e
        # 2. 包含匹配（"张三" 在 "张三丰" 里，或 "张三丰" 包含 "张三"）
        for e in engineers:
            if name in (e.name or "") or (e.name or "") in name:
                return e
        return None

    def reload_config(self):
        self._config.reload()
        invalidate_semantic_cache()
        invalidate_history_cache()
