"""DispatchFlow 核心逻辑：智能派单主流程

流程:
    TicketContext + EngineerProfile
        │
        ▼
    【Step -1 提单人指定】(强信号"[指定处理人:X]" / LLM检测"转给张三" → 直接指派)
        │ (未指定)
        ▼
    【Step 0 部门过滤】(关键词匹配 → 过滤非本部门候选人)
        │
        ▼
    【Step 0.6 排除提单人】(常规派单不派给自己；提单人指定走 Step -1 不受影响)
        │
        ▼
    【Step 1 三路召回】
        ├── L1 纯LLM召回(0.70): LLM 看全员画像 → 直接打分
        ├── L2 语义召回(0.20):   Embedding 工单 → 模块锚文本(产品-类别) → 反查工程师
        └── L3 历史召回(0.10):   A路相似工单聚人 + B路问题域聚人(带缓存)
        │
        ▼
    【Step 2 精排 + 职级折扣】 raw_total × job_level 惩罚系数
        │
        ▼
    【Step 2.5 负载均衡】(全体候选人按在途工单数打折，查询 30s 缓存)
        │
        ▼
    【Step 3 LLM 综合决策】成功→返回 / 失败→Step 4
        │
        ▼ (回退)
    【Step 4 规则决策】阈值判定: auto/recommend/fallback
"""

import json, re
import asyncio
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
from ai.agents.AiDiagnosisPlatform.assigner.recall.expertise_recall import (
    ExpertiseRecall, invalidate_expertise_cache,
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
        self._history_recall = HistoryRecall(config=self._config)      # L3-A：相似工单聚人
        self._expertise_recall = ExpertiseRecall(config=self._config)   # L3-B：问题域聚人
        self._ranker = Ranker(config=self._config)
        self._llm_decision = LlmDecision(config=self._config)
        self._fallback_decision = FallbackDecision(config=self._config)

    async def aassign(
        self,
        ticket_context: TicketContext,
        engineer_profiles: List[EngineerProfile],
    ) -> AssignmentResult:
        desc_preview = (ticket_context.problem_description or "")[:100].replace("\n", " ")
        # 统一派单日志前缀：便于按工单关联整条派单链路日志
        ltag = f"[派单:{ticket_context.id}]"
        logger.info(
            f"{ltag} START 工单={ticket_context.title[:50]!r} "
            f"desc={desc_preview!r} "
            f"fault={ticket_context.fault_code or '-'} robot={ticket_context.robot_type or '-'} "
            f"候选={len(engineer_profiles)}人"
        )
        if not engineer_profiles:
            raise ValueError("工程师列表为空。请检查 users 表人员数据是否就绪。")
        if not ticket_context.problem_description and not ticket_context.title:
            raise ValueError("问题描述和标题均为空，无法推断责任模块。")

        # ── Step -1: LLM 识别提单人是否指定了期望接单人 ──
        preferred = await self._detect_preferred_assignee(ticket_context, engineer_profiles)
        if preferred is not None:
            logger.info(
                f"{ltag} STEP-1 提单人指定 → {preferred.engineer_name}"
                f"({preferred.engineer_id}) 置信={preferred.confidence_score:.2f}"
            )
            self._log_assignment_result(
                ticket=ticket_context, result=preferred,
                candidates=engineer_profiles, ranked_scores={},
                source="提单人指定", ltag=ltag,
            )
            return preferred

        # ── Step 0: 部门过滤 ──
        candidates = await self._dept_filter.filter(
            ticket=ticket_context, engineers=engineer_profiles,
            project_name=ticket_context.project_name or "",
        )
        if not candidates:
            logger.warning(f"{ltag} STEP0 部门过滤后无候选人，回退全量")
            candidates = engineer_profiles
        logger.info(f"{ltag} STEP0 部门过滤 {len(engineer_profiles)}→{len(candidates)}人")

        # ── Step 0.6: 排除提单人（常规派单不派给自己；Step -1 指定自己不受影响）──
        candidates = self._exclude_creator(ticket_context, candidates)
        if not candidates:
            logger.warning(f"{ltag} STEP0.6 排除提单人后无候选人，回退全量")
            candidates = engineer_profiles

        # ── Step 1: 三路召回（L1/L2/L3 互不依赖，并行执行提升吞吐）──
        recall_result = RecallResult()
        try:
            l1_fut, l2_fut, l3_fut = await asyncio.gather(
                self._llm_recall.arecall(ticket=ticket_context, engineers=candidates),
                self._semantic_recall.arecall(ticket=ticket_context, engineers=candidates),
                self._history_pair(ticket_context),
                return_exceptions=True,
            )
        except Exception as e:
            logger.warning(f"{ltag} STEP1 并行召回批次异常: {e}")
            l1_fut = l2_fut = l3_fut = {}

        # L1 纯LLM 召回
        if isinstance(l1_fut, Exception):
            logger.warning(f"{ltag} STEP1 L1召回异常: {l1_fut}")
            recall_result.llm_recall = {}
        else:
            recall_result.llm_recall = l1_fut or {}
            self._log_recall_top(
                ltag, "L1", recall_result.llm_recall, candidates, "LLM召回(逐人置信)", count=8,
            )
        # L2 语义召回
        if isinstance(l2_fut, Exception):
            logger.warning(f"{ltag} STEP1 L2召回异常: {l2_fut}")
            recall_result.semantic_recall = {}
        else:
            recall_result.semantic_recall = l2_fut or {}
            self._log_recall_top(
                ltag, "L2", recall_result.semantic_recall, candidates, "语义召回(命中模块分)", count=8,
            )
        # L3 历史召回（A路相似工单 + B路问题域），已合并成单个 dict
        if isinstance(l3_fut, Exception):
            logger.warning(f"{ltag} STEP1 L3召回异常: {l3_fut}")
            recall_result.history_recall = {}
        else:
            recall_result.history_recall = l3_fut or {}
            self._log_recall_top(
                ltag, "L3", recall_result.history_recall, candidates, "历史召回(融合)", count=8,
            )

        # ── Step 2: 精排 + 职级折扣 ──
        ranked_scores = self._ranker.rank(recall_result, engineers=candidates)

        # ── Step 2.5: 负载均衡（按在途工单数打折，避免单子集中在少数人）──
        ranked_scores = self._apply_load_balance(ranked_scores)
        self._log_ranked(ltag, ranked_scores, candidates, prefix="STEP2.5 负载均衡后Top")

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
                logger.info(
                    f"{ltag} STEP3 LLM决策 → {result.engineer_name}({result.engineer_id}) "
                    f"置信={result.confidence_score:.2f} 类型={result.decision_type}"
                )
        except Exception as e:
            logger.warning(f"{ltag} STEP3 LLM决策失败,回退规则: {e}")

        # ── Step 4: 规则兜底 ──
        if result is None:
            result = self._fallback_decision.decide(ranked_scores=ranked_scores, engineers=candidates)
            decision_source = "规则兜底"
            logger.info(f"{ltag} STEP4 规则兜底 → {result.engineer_name}({result.engineer_id})")

        # ── 结果汇总日志（含工单描述 + 被派人完整画像）──
        self._log_assignment_result(
            ticket=ticket_context,
            result=result,
            candidates=candidates,
            ranked_scores=ranked_scores,
            source=decision_source,
            ltag=ltag,
        )
        return result

    def _log_recall_top(self, ltag, name, scores, candidates, tag_desc, count=8):
        """记录一路召回的结果：人数 + Top-N 候选（名 + 分数 + 归属模块）。"""
        if not scores:
            logger.info(f"{ltag} STEP1 {name}召回 命中=0人（{tag_desc} 无命中）")
            return
        emap = {e.id: e for e in candidates}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:count]
        parts = []
        for eid, sc in top:
            eng = emap.get(eid)
            nm = eng.name if eng else eid[:10]
            mod = ""
            if eng:
                flat = []
                for p, ms in eng.responsibility_modules.items():
                    flat.append(f"{p}:{','.join(ms[:3])}")
                mod = "[" + ";".join(flat) + "]"
            parts.append(f"{nm}={sc:.2f}{mod}")
        logger.info(
            f"{ltag} STEP1 {name}召回 命中={len(scores)}人（{tag_desc}）: " + " | ".join(parts)
        )

    def _log_ranked(self, ltag, ranked_scores, candidates, count=5, prefix="精排Top"):
        """记录精排后的 Top 候选（含各维度分与总分）。"""
        if not ranked_scores:
            logger.info(f"{ltag} {prefix}: 无排名数据")
            return
        emap = {e.id: e for e in candidates}
        parts = []
        for rank, (eid, d) in enumerate(list(ranked_scores.items())[:count], 1):
            eng = emap.get(eid)
            nm = eng.name if eng else eid[:10]
            parts.append(
                f"#{rank}{nm}(L{d.get('job_level','?')}) "
                f"总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} "
                f"语义={d.get('semantic_score',0):.2f} "
                f"历史={d.get('history_score',0):.2f}"
                f"{' 在途=' + str(d.get('load_count','')) if 'load_count' in d else ''}"
            )
        logger.info(f"{ltag} {prefix}: " + " | ".join(parts))

    def _log_assignment_result(
        self,
        ticket: TicketContext,
        result: AssignmentResult,
        candidates: List[EngineerProfile],
        ranked_scores: Dict[str, Dict[str, float]],
        source: str,
        ltag: str = "[派单]",
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
                f"{ltag} FINAL 派单结果[{source}] | "
                f"工单={ticket.title[:60]!r} | "
                f"故障码={ticket.fault_code or '-'} 车型={ticket.robot_type or '-'} | "
                f"→ 指派: {winner.name}({winner.id}) "
                f"部门={winner.department or '-'} 职级=L{winner.job_level} "
                f"模块=[{modules_str}] "
                f"职责={duty} | "
                f"置信度={result.confidence_score:.0%} "
                f"决策={result.decision_type} "
                f"LLM分={scores.get('llm_score', 0):.2f} "
                f"语义分={scores.get('semantic_score', 0):.2f} "
                f"历史分={scores.get('history_score', 0):.2f} "
                f"总分={scores.get('total_score', 0):.2f}"
                f"{' 在途=' + str(scores.get('load_count','')) if 'load_count' in scores else ''}"
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
            logger.info(f"{ltag} FINAL 排名Top3: {' | '.join(rank_lines)}")
        else:
            logger.info(f"{ltag} FINAL 排名: 无候选排名数据")

    # ── L3 双路融合: A路(相似工单聚人) + B路(问题域聚人) ──
    async def _history_pair(self, ticket) -> Dict[str, float]:
        """并行执行 L3-A（相似工单）与 L3-B（问题域），融合成单一 history_recall dict。

        供 Step 1 三路并行 gather 使用；任一异常返回空 dict 不阻断。
        """
        ltag = f"[派单:{ticket.id}]"
        try:
            his_a, his_b = await asyncio.gather(
                self._history_recall.arecall(ticket=ticket),
                self._expertise_recall.arecall(ticket=ticket),
                return_exceptions=True,
            )
            if isinstance(his_a, Exception):
                logger.warning(f"{ltag} STEP1 L3-A 相似工单召回异常: {his_a}")
                his_a = {}
            if isinstance(his_b, Exception):
                logger.warning(f"{ltag} STEP1 L3-B 问题域召回异常: {his_b}")
                his_b = {}
            merged = self._merge_history(his_a, his_b)
            logger.info(
                f"{ltag} STEP1 L3历史召回: A路相似工单={len(his_a)}人 "
                f"B路问题域={len(his_b)}人 融合={len(merged)}人"
            )
            return merged
        except Exception as e:
            logger.warning(f"{ltag} STEP1 L3 历史召回异常: {e}")
            return {}

    def _merge_history(
        self, his_a: Dict[str, float], his_b: Dict[str, float],
    ) -> Dict[str, float]:
        """将 A路 与 B路 历史召回结果融合成单一 history_recall 分数。

        策略：两路各自归一化到 0-1，再按 weight_a / weight_b 加权相加。
        权重从 config.yaml 的 history_recall 读取（默认各 0.5）。
        """
        hc = self._config.history_recall or {}
        w_a = float(hc.get("weight_a", 0.5))
        w_b = float(hc.get("weight_b", 0.5))

        merged: Dict[str, float] = {}

        # A路归一化
        norm_a: Dict[str, float] = {}
        if his_a:
            maxv = max(his_a.values()) or 1.0
            norm_a = {k: v / maxv for k, v in his_a.items()}
        # B路归一化（B路本身已是 0-1，但保险起见也归一）
        norm_b: Dict[str, float] = {}
        if his_b:
            maxv = max(his_b.values()) or 1.0
            norm_b = {k: v / maxv for k, v in his_b.items()}

        for eid in set(norm_a) | set(norm_b):
            merged[eid] = round(
                w_a * norm_a.get(eid, 0.0) + w_b * norm_b.get(eid, 0.0)
                , 4
            )
        return merged

    # ── Step 0.6 实现: 排除提单人（常规派单不派给自己）──
    @staticmethod
    def _exclude_creator(
        ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> List[EngineerProfile]:
        """把提单人从候选人中排除（避免常规派单派给自己）。

        - 提单人 = TicketContext.creator（存 users.username，如 wechat_oD5oY3...）
        - 工程师标识 EngineerProfile.id 已统一为 username，直接精确匹配
        - 匹配不到提单人（如提单人不是工程师）则不过滤，正常派单
        - Step -1（提单人指定）在 Step 0 之前已直接返回，不受本规则影响
        """
        creator = (ticket.creator or "").strip()
        if not creator:
            return list(engineers)

        excluded = [e for e in engineers if e.id != creator]
        if len(excluded) < len(engineers):
            logger.info(f"派单 Step 0.6 排除提单人: {creator} 已从候选移除 ({len(engineers)}→{len(excluded)})")
        return excluded

    # ── Step 2.5 实现: 负载均衡（对全体候选人按在途工单数打折，带查询缓存）──
    _workload_cache: Dict[str, object] = {}  # {"ts": float, "data": {engineer_id: 在途数}}

    def _apply_load_balance(
        self, ranked_scores: Dict[str, Dict[str, float]],
    ) -> Dict[str, Dict[str, float]]:
        """对进入精排的全部候选人按在途工单数打折，避免单子集中在少数人。

        负载系数 = 1 / (1 + 在途数 × step)。对所有 rank 候选人统一施加；
        在途为 0 的人系数=1（不被打折）。在途数查询带短 TTL 缓存，降低 DB 压力。
        """
        lb_cfg = self._config.load_balance or {}
        if not lb_cfg.get("enabled", True):
            return ranked_scores
        step = float(lb_cfg.get("step", 0.15))
        if not ranked_scores:
            return ranked_scores

        # 查询全体候选人的在途工单数（含缓存）
        workload = self._query_workload()
        if not workload:
            return ranked_scores

        for eid in ranked_scores:
            count = workload.get(eid, 0)
            factor = 1.0 / (1.0 + count * step)
            old_total = ranked_scores[eid].get("total_score", 0.0)
            ranked_scores[eid]["load_factor"] = factor
            ranked_scores[eid]["load_count"] = count
            ranked_scores[eid]["total_score"] = round(old_total * factor, 4)
            if count and logger.isEnabledFor(10):
                logger.debug(
                    f"派单 Step 2.5 负载均衡: {eid} 在途={count} 系数={factor:.2f} "
                    f"分={old_total:.2f}→{ranked_scores[eid]['total_score']:.2f}"
                )

        return dict(sorted(ranked_scores.items(), key=lambda x: x[1]["total_score"], reverse=True))

    @classmethod
    def _query_workload(cls, ttl: float = 30.0) -> Dict[str, int]:
        """查询全体候选工程师的在途工单数（tasks.assigned_to 统计，status 非 closed）。

        结果做短 TTL 模块级缓存（默认 30s），避免高频派单时每张工单都查库。
        Returns: {engineer_id: 在途数}；查询失败返回空 dict（不阻断派单）。
        """
        cache = cls._workload_cache
        import time as _t
        now = _t.time()
        if cache.get("ts") and (now - cache["ts"]) < ttl:
            return cache["data"]

        try:
            from app.models.task import Task
            from app.core.db import SessionLocal
            from sqlalchemy import func

            db = SessionLocal()
            try:
                rows = (
                    db.query(Task.assigned_to, func.count(Task.id))
                    .filter(
                        Task.assigned_to.isnot(None),
                        Task.assigned_to != "",
                        Task.status != "closed",
                    )
                    .group_by(Task.assigned_to)
                    .all()
                )
                data = {uid: cnt for uid, cnt in rows if uid}
                cache["ts"] = now
                cache["data"] = data
                return data
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"派单 Step 2.5 查询在途工单失败，跳过负载均衡: {e}")
            return {}

    # ── Step -1 实现: 识别提单人期望接单人（强信号 + LLM 兜底）──
    # 强信号：提单 Agent 结构化输出的"[指定处理人：贾爽]"等格式
    _PREFERRED_STRONG_RE = None

    @classmethod
    def _extract_strong_preferred(cls, text: str) -> Optional[str]:
        """从结构化"指定处理人：XXX"强信号中提取人名（不调 LLM）。

        例如 "指定处理人：贾爽" / "[指定处理人：贾爽]" / "指定处理人:张三"。
        命中返回人名，未命中返回 None（走弱信号兜底）。
        """
        if not text:
            return None
        if cls._PREFERRED_STRONG_RE is None:
            import re
            # 匹配 指定处理人/指定人[:：]后跟 2~6 个非分隔字符（排除 ] 空白 标点 冒号）
            cls._PREFERRED_STRONG_RE = re.compile(
                r"指定(?:处理人|人|人员)[:：]\s*([^\]\s，,；;:：）)】]{2,6})"
            )
        m = cls._PREFERRED_STRONG_RE.search(text or "")
        return m.group(1).strip() if m else None

    async def _detect_preferred_assignee(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Optional[AssignmentResult]:
        """识别提单人是否明确指定了期望接单人。

        两级策略：
        1. 强信号：提单 Agent 结构化输出的"指定处理人：XXX"，直接提取人名匹配（不调 LLM）。
        2. 弱信号兜底：自由文本（"这个给张三看一下"等）经轻量预判命中后，用 LLM 识别。

        Returns: 匹配成功返回 AssignmentResult，未指定/未匹配返回 None（继续走正常派单）。
        """
        text = f"标题: {ticket.title or ''}\n描述: {ticket.problem_description or ''}"

        # ── 1. 强信号：结构化"指定处理人：XXX"（提单 Agent 标准输出，直接匹配，不调 LLM）──
        strong_name = self._extract_strong_preferred(text)
        if strong_name:
            matched = self._match_engineer_by_name(strong_name, engineers)
            if matched:
                logger.info(
                    f"派单 Step -1 [提单人指定-强信号]: '{strong_name}' "
                    f"→ {matched.name}({matched.id})"
                )
                return AssignmentResult(
                    engineer_id=matched.id,
                    engineer_name=matched.name,
                    confidence_score=0.95,
                    reasoning=f"提单Agent指定接单人: {strong_name} → 匹配 {matched.name}",
                    decision_type="auto",
                )
            logger.info(
                f"派单 Step -1: 强信号指定 '{strong_name}' 未匹配到工程师，走正常派单"
            )
            return None

        # ── 2. 弱信号兜底：自由文本预判命中才走 LLM（避免每单白跑一次 LLM）──
        if not self._maybe_has_preferred(text):
            return None

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

    # 指派意图名词（命中才触发 LLM 识别，避免无谓 LLM 调用）
    _PREFERRED_INTENT_RE = None

    @classmethod
    def _maybe_has_preferred(cls, text: str) -> bool:
        """轻量预判：文本是否疑似包含"指定某人处理"的意图。

        命中规则才需要 LLM 进一步识别，否则直接跳过（省一次 LLM 调用）。
        只做粗粒度过滤，允许误报（多调一次 LLM），但避免漏报主要场景。
        """
        if not text:
            return False
        if cls._PREFERRED_INTENT_RE is None:
            import re
            # 动作词 + 2~4 中文人名；或 人名 + 归属/处理词
            cls._PREFERRED_INTENT_RE = re.compile(
                r"(?:给|让|转给|派给|找|安排给|提给|请|交由|交予)?"
                r"[一-龥]{2,4}"
                r"(?:负责|比较熟|熟悉|来搞|来处理|处理|看下|看一下|有空|跟进|接手|对接|处理一下|来跟进)"
                r"|(?:给|让|转给|派给|找|安排给|提给|请|交由|交予)"
                r"[一-龥]{2,4}"
            )
        return bool(cls._PREFERRED_INTENT_RE.search(text))

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
        invalidate_expertise_cache()
