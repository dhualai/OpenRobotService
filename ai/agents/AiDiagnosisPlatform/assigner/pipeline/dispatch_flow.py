"""DispatchFlow 核心逻辑：智能派单主流程

流程:
    TicketContext + EngineerProfile
        │
        ▼
    【Step 0 提单人指定】(强信号"[指定处理人:X]" / LLM检测"转给张三" → 直接指派 + tip)
        │ (未指定 / 指定人找不到：写 tip 后继续)
        ▼
    【Step 1 候选收紧】部门(R2 LLM + R3 历史融合 + R-Audit) → 产品(项目标记>部门映射>默认)
        │
        ▼
    【Step 2 打标】(提单人 / 项目对接人 / 倾向接单人 / 原用户不满意的接单人；不踢自提人)
        ▼
    【Step 2.5/2.6 强制保留】(对接人 / 用户倾向处理人 被过滤则补回候选)
        │
        ▼
    【Step 3 三路召回】
        ├── LLM：看人卡片推断谁能接
        ├── 相似工单：最像的已解决/已关闭单 → 按人（可空）
        └── 问题域：自动簇里谁常结单（可空）
        │
        ▼
    【Step 4 精排】三路各自归一；空路权重摊给有人的路；职级折扣 × 部门soft_prior；倾向人保底；对接人只打标
        │
        ▼
    【Step 6 LLM 最终决策】铁律 + 产品附录；失败/很难决策/名单外 → None，不回精排#1
        │
        ▼ (None / 模糊截断)
    【Step 7 兜底】本单对接人 → 本单项目经理 → 配置项目经理；都空则未指派 + tip，不拔精排#1
"""

import json, re
import asyncio
import random
from typing import Dict, List, Optional, Tuple

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.ranking.fallback_decision import (
    FallbackDecision,
    REASON_STEP6,
    REASON_VAGUE,
)
from ai.agents.AiDiagnosisPlatform.assigner.filtering.candidate_tightener import CandidateTightener
from ai.agents.AiDiagnosisPlatform.assigner.filtering.routing_schemas import TightenResult
from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recall import LlmRecall
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import (
    llm_person_label,
    match_vague_strong_signal,
    score_tag_labels,
)
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
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


def _engineer_profile_dict(eng: "EngineerProfile") -> Dict:
    """构建被派人/候选工程师的画像字典（含完整性 missing）落 task_dispatch_log。

    画像完整性判定仅用三项：department / job_level / responsibility_modules
    （duty_text 仅用于展示、不参与完整性判定；responsibility_modules 全空才算缺失）。
    """
    missing: List[str] = []
    if not (eng.department or "").strip():
        missing.append("department")
    if not eng.job_level:
        missing.append("job_level")
    rm = eng.responsibility_modules
    if not rm or not (rm or {}):
        missing.append("responsibility_modules")
    return {
        "dept": eng.department,
        "job_level": eng.job_level,
        "modules": eng.all_modules(),
        "duty": eng.duty_text,
        "missing": missing,
    }


def _step0_winner_profile(eng: "EngineerProfile", collision_random: bool = False) -> Dict:
    """Step0 落库画像：缺项进 missing；同名随机选中再打 collision_random，供 tip 提醒补画像。"""
    prof = _engineer_profile_dict(eng)
    if collision_random:
        prof["collision_random"] = True
    return prof


def _candidate_dict(rank: int, eng: "EngineerProfile", scores: Dict, tags: List[str]) -> Dict:
    """把单个工程师序列化为候选快照字典（供 task_dispatch_log.candidates，R2 弹窗数据源）。"""
    p = _engineer_profile_dict(eng)
    return {
        "rank": rank,
        "engineer_id": eng.id,
        "name": eng.name,
        "department": p.get("dept"),
        "job_level": p.get("job_level"),
        "modules": p.get("modules"),
        "duty": p.get("duty"),
        # 画像缺失英文字段（department/job_level/responsibility_modules），供 M3 高情商话术
        # 判定「倾向人画像不完整」并点明缺失项（历史数据无此字段 → 视为完整，安全降级）
        "missing": p.get("missing") or [],
        "scores": {
            "llm": scores.get("llm_score", 0),
            "similar": scores.get("similar_score", 0),
            "cluster": scores.get("cluster_score", 0),
            "history": scores.get("similar_score", scores.get("history_score", 0)),
            "total": scores.get("total_score", 0),
        },
        "tags": tags,
    }


def _profile_has_any(e: "EngineerProfile") -> bool:
    """是否“有画像”：department / job_level / responsibility_modules 任一非空。"""
    if (e.department or "").strip():
        return True
    if e.job_level:
        return True
    rm = e.responsibility_modules
    if rm and (rm or {}):
        return True
    return False


def _candidates_snapshot(ranked_scores, candidates: List["EngineerProfile"], topk: int = 10) -> List[Dict]:
    """导出候选快照（供 task_dispatch_log.candidates，R2 弹窗数据源）。

    优先取精排 Top-N；当精排结果不足以填满候选时（ranked_scores 为空 / 太少，
    例如 Step0 提单人指定直接返回、或精排被收紧）、或精排缺失时，
    自动把当前可用候选人（candidates）兜底纳入——已入选的在前，其余按“有画像优先、无画像殿后”补齐，
    保证重派弹窗永远有可选人，而不是显示“暂无精排候选”。
    """
    emap = {e.id: e for e in candidates}
    shot: List[Dict] = []
    seen = set()
    for rank, (eid, d) in enumerate(list(ranked_scores.items())[:topk], 1):
        eng = emap.get(eid)
        if eng is None:
            continue
        seen.add(eid)
        tags = score_tag_labels(d)
        shot.append(_candidate_dict(rank, eng, d, tags))

    # ── 兜底：精排不足时，从未入选候选人中补齐（有画像优先），保证弹窗总有可选项 ──
    if len(shot) < topk and candidates:
        rest = [e for e in candidates if e.id not in seen]
        rest_sorted = (
            [e for e in rest if _profile_has_any(e)]
            + [e for e in rest if not _profile_has_any(e)]
        )
        for eng in rest_sorted[: topk - len(shot)]:
            rank = len(shot) + 1
            shot.append(_candidate_dict(rank, eng, {}, []))
    return shot


def _dup_names(candidates: List["EngineerProfile"]) -> set:
    """返回候选工程师集合中出现次数 >1 的姓名集合。

    同名时（多个候选人姓名相同），人工阅读日志光看姓名无法区分谁是谁，
    因此在日志里对这些重名候选人追加 (users.id)。
    """
    from collections import Counter
    cnt = Counter((e.name or "").strip() for e in candidates)
    return {n for n, c in cnt.items() if c > 1 and n}


class DispatchFlow:
    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self._tightener = CandidateTightener(config=self._config)
        self._llm_recall = LlmRecall(config=self._config)
        self._history_recall = HistoryRecall(config=self._config)      # L3-A：相似工单聚人
        self._expertise_recall = ExpertiseRecall(config=self._config)   # L3-B：自动簇聚人
        self._ranker = Ranker(config=self._config)
        self._llm_decision = LlmDecision(config=self._config)
        self._fallback_decision = FallbackDecision(config=self._config)
        self._last_tighten: Optional[TightenResult] = None

    @property
    def last_tighten(self) -> Optional[TightenResult]:
        """最近一次派单的候选收紧结果（供 eval / debug）。"""
        return self._last_tighten

    async def aassign(
        self,
        ticket_context: TicketContext,
        engineer_profiles: List[EngineerProfile],
    ) -> AssignmentResult:
        desc_preview = (ticket_context.problem_description or "")[:100].replace("\n", " ")
        # 统一派单日志前缀（工单编号置前）：便于按工单关联整条派单链路日志
        ltag = f"[派单:{ticket_context.id}]"
        logger.info(
            f"{ltag} 开始派单 | 工单={ticket_context.title[:50]!r} "
            f"| 描述={desc_preview!r} "
            f"| 故障码={ticket_context.fault_code or '-'} 车型={ticket_context.robot_type or '-'} "
            f"| 候选={len(engineer_profiles)}人"
        )
        if not engineer_profiles:
            raise ValueError("工程师列表为空。请检查 users 表人员数据是否就绪。")
        if not ticket_context.problem_description and not ticket_context.title:
            raise ValueError("问题描述和标题均为空，无法推断责任模块。")

        # ── Step 0: 提单人指定（LLM 识别是否指定期望接单人）──
        preferred, specified_unresolved = await self._detect_preferred_assignee(
            ticket_context, engineer_profiles,
        )
        if preferred is not None:
            logger.info(
                f"{ltag} Step0 提单人指定 → {preferred.engineer_name}"
                f"({preferred.engineer_id}) 置信={preferred.confidence_score:.2f}"
                f" preferred_id={preferred.preferred_id} pinyin={preferred.pinyin_match}"
            )
            self._log_assignment_result(
                ticket=ticket_context, result=preferred,
                candidates=engineer_profiles, ranked_scores={},
                source="提单人指定", ltag=ltag,
            )
            return preferred
        if specified_unresolved:
            logger.info(f"{ltag} Step0 指定人未命中，记下 specified_name={specified_unresolved!r} 进入后续")

        # ── 项目对接人（Step 4 加权 / 强制保留用；可能为 None → 不加权不保留）──
        contact_assignee_id = self._resolve_contact_assignee(ticket_context)
        contact_name = next(
            (e.name for e in engineer_profiles if e.id == contact_assignee_id),
            contact_assignee_id,
        )
        if contact_assignee_id:
            logger.info(f"{ltag} 项目对接人: {contact_name}({contact_assignee_id})（只打标，不加分）")

        # ── 用户倾向处理人（预留：前端传 ticket.preferred_assignee 即启用；未传返回 None 不生效）──
        preferred_assignee_id = None
        pref_name = None
        if self._config.preferred_assignee_enabled:
            preferred_assignee_id = self._resolve_preferred_assignee(
                ticket_context, engineer_profiles,
            )
            if preferred_assignee_id:
                pref_name = next(
                    (e.name for e in engineer_profiles if e.id == preferred_assignee_id),
                    preferred_assignee_id,
                )
                logger.info(
                    f"{ltag} 用户倾向处理人: {pref_name}"
                    f"（精排保底 {getattr(self._config, 'preferred_floor', 0.9):.1f}"
                    f"{'，强制保留进候选' if self._config.preferred_assignee_force_keep else ''}）"
                )

        # ── Step 1: 候选收紧（部门 → 产品 → 模块）──
        tighten: TightenResult = await self._tightener.tighten(
            ticket=ticket_context, engineers=engineer_profiles,
        )
        self._last_tighten = tighten
        candidates = tighten.candidates
        if not candidates:
            logger.warning(f"{ltag} Step1 收紧后无候选人，回退全量")
            candidates = engineer_profiles
        logger.info(
            f"{ltag} Step1 候选收紧 {tighten.before_count}→{tighten.after_count}人 | "
            f"部门={tighten.dept.mode}({tighten.dept.primary_dept or '-'}) | "
            f"产品={tighten.product.product or '-'} | 模块层=已移除(不收紧)"
        )

        # ── Step 2: 打标（不踢自提人；标签一路传到仲裁）──
        creator_id = self._resolve_creator_id(ticket_context)
        prev_assignee_id = self._resolve_prev_assignee_id(ticket_context)
        if creator_id:
            logger.info(f"{ltag} Step2 提单人={creator_id} 保留在候选（标记提单人，交由LLM判断能否接单）")
        if prev_assignee_id:
            logger.info(f"{ltag} Step2 原接单人={prev_assignee_id}（标记原用户不满意的接单人）")
        skip_recall = match_vague_strong_signal(ticket_context, self._config)
        if skip_recall:
            logger.info(f"{ltag} Step2 模糊强信号命中 → 跳过 Step3–6，进 Step7")
            result = self._run_step7(
                ticket_context, contact_assignee_id, contact_name,
                engineer_profiles, REASON_VAGUE, ltag,
            )
            return self._finalize_assignment(
                ticket_context, result, candidates, {},
                "Step7兜底", ltag, specified_unresolved, engineer_profiles,
            )

        # ── Step 2.5: 强制保留项目对接人（即使被部门/产品/排除提单人过滤掉也加回候选）──
        # 例外：对接人 == 提单人（自提单）时**不**强制保留，交由 Step2 正常排除（自提不自接）。
        if contact_assignee_id:
            creator_raw = (ticket_context.creator or "").strip()
            try:
                from app.core.user_identity import to_user_id
                creator_id = to_user_id(creator_raw) or creator_raw
            except Exception:
                creator_id = creator_raw
            if contact_assignee_id == creator_id:
                creator_raw_name = next(
                    (e.name for e in engineer_profiles if e.id == creator_id),
                    creator_id,
                )
                logger.info(
                    f"{ltag} Step2.5 对接人==提单人({creator_raw_name}({creator_id}))，不强制保留（自提不自接）"
                )
            elif not any(e.id == contact_assignee_id for e in candidates):
                # 对接人可能仍在全量工程师里但被过滤掉 → 强制补回
                contact_eng = next(
                    (e for e in engineer_profiles if e.id == contact_assignee_id), None
                )
                if contact_eng is not None:
                    candidates.append(contact_eng)
                    logger.info(
                        f"{ltag} Step2.5 强制保留项目对接人 {contact_name}({contact_assignee_id})"
                        f" -> 候选 {len(candidates)}人"
                    )

        # ── Step 2.6: 强制保留用户倾向处理人（预留：即使被部门/产品/排除提单人过滤也加回候选）──
        if (
            preferred_assignee_id
            and self._config.preferred_assignee_force_keep
            and not any(e.id == preferred_assignee_id for e in candidates)
        ):
            pref_eng = next(
                (e for e in engineer_profiles if e.id == preferred_assignee_id), None
            )
            if pref_eng is not None:
                candidates.append(pref_eng)
                logger.info(
                    f"{ltag} Step2.6 强制保留用户倾向处理人 {pref_name}({preferred_assignee_id})"
                    f" -> 候选 {len(candidates)}人"
                )

        # ── Step 3: 三路召回（LLM / 相似工单 / 问题域），后两路可空 ──
        recall_result = RecallResult()
        try:
            l1_fut, sim_fut, clu_fut = await asyncio.gather(
                self._llm_recall.arecall(ticket=ticket_context, engineers=candidates),
                self._history_recall.arecall(ticket=ticket_context),
                self._expertise_recall.arecall(ticket=ticket_context),
                return_exceptions=True,
            )
        except Exception as e:
            logger.warning(f"{ltag} Step3 并行召回批次异常: {e}")
            l1_fut = sim_fut = clu_fut = {}

        if isinstance(l1_fut, Exception):
            logger.warning(f"{ltag} Step3 LLM召回异常: {l1_fut}")
            recall_result.llm_recall = {}
        else:
            recall_result.llm_recall = l1_fut or {}
            recall_result.llm_reasons = dict(getattr(self._llm_recall, "last_reasons", {}) or {})
            self._log_recall_top(
                ltag, "LLM", recall_result.llm_recall, candidates, "LLM召回(逐人置信)", count=8,
            )

        if isinstance(sim_fut, Exception):
            logger.warning(f"{ltag} Step3 相似工单召回异常: {sim_fut}")
            recall_result.similar_recall = {}
        else:
            recall_result.similar_recall = sim_fut or {}
            self._log_recall_top(
                ltag, "相似", recall_result.similar_recall, candidates, "相似工单(可空)", count=8,
            )
        if not recall_result.similar_recall:
            logger.info(f"{ltag} Step3 相似工单: 空")

        if isinstance(clu_fut, Exception):
            logger.warning(f"{ltag} Step3 问题域召回异常: {clu_fut}")
            recall_result.cluster_recall = {}
        else:
            recall_result.cluster_recall = clu_fut or {}
            self._log_recall_top(
                ltag, "问题域", recall_result.cluster_recall, candidates, "问题域自动簇(可空)", count=8,
            )
        if not recall_result.cluster_recall:
            logger.info(f"{ltag} Step3 问题域: 空")

        recall_result.transfer_signals = self._history_recall.empty_transfer_signals()
        logger.info(f"{ltag} Step3 转派旁路 transfer_signals=empty")

        # ── Step 4: 精排 + 职级折扣（对接人只打标；倾向人 max(分, preferred_floor)）──
        ranked_scores = self._ranker.rank(
            recall_result, engineers=candidates,
            contact_assignee_id=contact_assignee_id,
            preferred_assignee_id=preferred_assignee_id,
            creator_id=creator_id,
            prev_assignee_id=prev_assignee_id,
            dept_routing=tighten.dept,
        )
        # 三路并集：历史捞回但不在收紧名单的人，补进候选交给 Step6，并标明来源。
        cand_ids = {e.id for e in candidates}
        for eid in list(ranked_scores):
            if eid in cand_ids:
                continue
            extra = next((e for e in engineer_profiles if e.id == eid), None)
            if extra is None:
                logger.info(f"{ltag} Step4 并集命中 {eid} 但不在工程师画像，跳过")
                ranked_scores.pop(eid, None)
                continue
            candidates.append(extra)
            cand_ids.add(eid)
            ranked_scores[eid]["outside_tighten"] = True
            logger.info(
                f"{ltag} Step4 并集补入 {extra.name}({eid})（不在收紧名单，历史捞回）"
            )
        self._log_ranked(ltag, ranked_scores, candidates, prefix="Step4 精排Top")

        # ── Step 6: LLM 综合决策 ──
        result: Optional[AssignmentResult] = None
        decision_source = ""
        try:
            llm_result = await self._llm_decision.adecide(
                ticket=ticket_context, engineers=candidates,
                recall_result=recall_result, ranked_scores=ranked_scores,
                product=getattr(tighten.product, "product", "") or "",
            )
            if llm_result is not None:
                result = llm_result
                decision_source = "LLM决策"
                _reason = (result.reasoning or "")
                logger.info(
                    f"{ltag} Step6 LLM决策 → {result.engineer_name}({result.engineer_id}) "
                    f"置信={result.confidence_score:.2f} 类型={result.decision_type} "
                    f"理由={_reason[:120]}"
                )
            else:
                logger.info(f"{ltag} Step6 交不出人 → 交 Step7")
        except Exception as e:
            logger.warning(f"{ltag} Step6 LLM决策失败: {e} → 交 Step7")

        # ── Step 7: 对接人 → 本单项目经理 → 配置项目经理；都空则未指派 + tip ──
        if result is None:
            result = self._run_step7(
                ticket_context, contact_assignee_id, contact_name,
                engineer_profiles, REASON_STEP6, ltag,
            )
            decision_source = "Step7兜底"

        return self._finalize_assignment(
            ticket_context, result, candidates, ranked_scores,
            decision_source, ltag, specified_unresolved, engineer_profiles,
        )

    def _run_step7(
        self, ticket, contact_id, contact_name, engineers, reason, ltag,
    ) -> AssignmentResult:
        """对接人 → 本单项目经理 → 配置项目经理。都空则返回未指派结果（写 tip，不编精排 #1）。"""
        row = self._load_project_row(ticket)
        project_pm_id, project_pm_name = self._pm_from_row(row, engineers)
        cfg_pm = self._config_project_manager()
        result = self._fallback_decision.decide(
            contact_id=contact_id,
            contact_name=contact_name,
            project_pm_id=project_pm_id,
            project_pm_name=project_pm_name,
            config_pm_id=cfg_pm[0] if cfg_pm else None,
            config_pm_name=cfg_pm[1] if cfg_pm else None,
            reason=reason,
        )
        if result is None:
            logger.error(
                f"{ltag} Step7 对接人与项目经理都空，派单失败（不拔精排#1，写 tip 不派人）"
            )
            return AssignmentResult(
                engineer_id="",
                engineer_name="",
                confidence_score=0.0,
                reasoning="项目未配置对接人和项目经理，暂时无法派单",
                decision_type="fallback",
                profile={"unassignable": True},
            )
        logger.info(
            f"{ltag} Step7 → {result.engineer_name}({result.engineer_id}) 理由={reason}"
        )
        return result

    def _finalize_assignment(
        self, ticket, result, candidates, ranked_scores, source, ltag,
        specified_unresolved, engineer_profiles,
    ):
        self._log_assignment_result(
            ticket=ticket, result=result, candidates=candidates,
            ranked_scores=ranked_scores, source=source, ltag=ltag,
        )
        winner = None
        if result.engineer_id:
            winner = next((e for e in candidates if e.id == result.engineer_id), None)
            if winner is None:
                winner = next(
                    (e for e in engineer_profiles if e.id == result.engineer_id), None,
                )
        if winner is not None:
            result.profile = _engineer_profile_dict(winner)
            result.candidates = _candidates_snapshot(ranked_scores, candidates, topk=10)
        pref = (getattr(ticket, "preferred_assignee", "") or "").strip()
        if pref:
            try:
                from app.core.user_identity import to_user_id
                pref_id = to_user_id(pref) or pref
            except Exception:
                pref_id = pref
            result.preferred_id = pref_id
            result.matched_pref = bool(result.engineer_id and result.engineer_id == pref_id)
        if specified_unresolved:
            prof = dict(result.profile or {})
            prof["specified_name"] = specified_unresolved
            result.profile = prof
        if getattr(self._config, "dept_profiles_missing", False):
            prof = dict(result.profile or {})
            prof["no_dept_profile"] = True
            result.profile = prof
        return result

    def _log_recall_top(self, ltag, name, scores, candidates, tag_desc, count=8):
        """记录一路召回的结果：人数 + Top-N 候选（名 + 分数 + 归属模块）。"""
        if not scores:
            logger.info(f"{ltag} Step3 {name}召回 命中=0人（{tag_desc} 无命中）")
            return
        emap = {e.id: e for e in candidates}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:count]
        parts = []
        for eid, sc in top:
            eng = emap.get(eid)
            nm = llm_person_label(eng=eng) if eng else llm_person_label(eid, "未知")
            mod = ""
            if eng:
                flat = []
                for p, by_iface in eng.responsibility_modules.items():
                    if isinstance(by_iface, dict):
                        flat.append(f"{p}:{{" + ','.join(
                            f"{i}:{','.join(fs[:3])}" for i, fs in by_iface.items()
                        ) + "}")
                    else:
                        flat.append(f"{p}:{','.join(by_iface[:3])}")
                mod = f"[{';'.join(flat)}]"
            parts.append(f"{nm}={sc:.2f}{mod}")
        logger.info(
            f"{ltag} Step3 {name}召回 | 命中={len(scores)}人（{tag_desc}）| "
            + " | ".join(parts)
        )

    def _log_ranked(self, ltag, ranked_scores, candidates, count=5, prefix="精排Top"):
        """记录精排后的 Top 候选（含各维度分与总分）。"""
        if not ranked_scores:
            logger.info(f"{ltag} {prefix}: 无排名数据")
            return
        # 同名检测：候选集合存在同名时，日志该名追加 (id) 以便区分（同名光看姓名分不清）
        _dup = _dup_names(candidates)
        emap = {e.id: e for e in candidates}
        parts = []
        for rank, (eid, d) in enumerate(list(ranked_scores.items())[:count], 1):
            eng = emap.get(eid)
            nm = eng.name if eng else "未知"
            if eng and nm in _dup:
                nm = f"{nm}({eng.id})"
            load = f"在途={d['load_count']}" if 'load_count' in d else ""
            tag = "".join(f" [{t}]" for t in score_tag_labels(d))
            parts.append(
                f"#{rank} {nm}(L{d.get('job_level','?')}) "
                f"总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} "
                f"相似={d.get('similar_score', d.get('history_score',0)):.2f} "
                f"簇={d.get('cluster_score',0):.2f}"
                f"{load}{tag}"
            )
        logger.info(f"{ltag} {prefix} | " + " | ".join(parts))

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
        _dup = _dup_names(candidates)
        # ── 被派人完整画像 ──
        winner = next((e for e in candidates if e.id == result.engineer_id), None)
        if winner:
            modules_str = winner.modules_display() or "-"
            duty = (winner.duty_text or "")[:120].replace("\n", " ")
            scores = ranked_scores.get(winner.id, {})
            reason = (result.reasoning or "").replace("\n", " ")
            stags = "".join(f" [{t}]" for t in score_tag_labels(scores))
            winner_label = f"{winner.name}({winner.id})" if (winner.name or "") in _dup else winner.name
            logger.info(
                f"{ltag} 派单结果[{source}] | "
                f"工单={ticket.title[:60]!r} | "
                f"指派={winner_label}{stags} "
                f"部门={winner.department or '-'} 职级=L{winner.job_level} | "
                f"置信度={result.confidence_score:.0%} 决策={result.decision_type} | "
                f"模块=[{modules_str}] | "
                f"职责={duty} | "
                f"理由={reason[:200]} | "
                f"LLM={scores.get('llm_score',0):.2f} "
                f"相似={scores.get('similar_score', scores.get('history_score',0)):.2f} "
                f"簇={scores.get('cluster_score',0):.2f} "
                f"总={scores.get('total_score',0):.2f}"
            )
        elif (result.profile or {}).get("unassignable") or not result.engineer_id:
            logger.info(
                f"{ltag} 派单结果[{source}] | 工单={ticket.title[:60]!r} | "
                f"未指派 unassignable | 理由={(result.reasoning or '')[:120]}"
            )

        # ── Top3 排名 ──
        top3 = list(ranked_scores.items())[:3]
        if top3:
            rank_lines = []
            for rank, (eid, d) in enumerate(top3, 1):
                eng = next((e for e in candidates if e.id == eid), None)
                name = eng.name if eng else "未知"
                if eng and (eng.name or "") in _dup:
                    name = f"{eng.name}({eng.id})"
                tag = "".join(f" [{t}]" for t in score_tag_labels(d))
                rank_lines.append(
                    f"#{rank} {name}(L{d.get('job_level','?')}){tag} "
                    f"总={d.get('total_score',0):.2f} "
                    f"LLM={d.get('llm_score',0):.2f} "
                    f"相似={d.get('similar_score', d.get('history_score',0)):.2f} "
                    f"簇={d.get('cluster_score',0):.2f}"
                )
            logger.info(f"{ltag} 排名Top3 | {' | '.join(rank_lines)}")
        else:
            logger.info(f"{ltag} 排名: 无候选排名数据")

    # ── Step 2 实现: 识别提单人 users.id（不再排除，仅标记"提单人"，交由 LLM 判断可否接单）──
    @staticmethod
    def _resolve_creator_id(ticket: TicketContext) -> Optional[str]:
        """识别提单人 users.id。

        原"自提不自接"硬排除已改为"保留 + 标记"：提单人仍留在候选，精排/决策时打上
        is_creator 标识（[提单人]），由 Step6 LLM 判断该提单人是否恰当接单
        （如"派单算法 bug"由派单引擎负责人自提时可合理接回给自己）。
        - 提单人 = TicketContext.creator（存 users.id 或 username）
        - 匹配不到（如提单人不是工程师）→ 返回 None，不启用自提标识
        - Step 0（提单人指定）在 Step 1 之前已直接返回，不受本逻辑影响
        """
        creator = (ticket.creator or "").strip()
        if not creator:
            return None
        try:
            from app.core.user_identity import to_user_id
            creator_id = to_user_id(creator) or creator
        except Exception:
            creator_id = creator
        return creator_id

    # ── 项目对接人 / 项目经理（Step7 兜底）──
    @staticmethod
    def _load_project_row(ticket: TicketContext):
        """按 project_id / project_name 查 project 表。失败返回 None。"""
        key = (ticket.project_id or "").strip() or (ticket.project_name or "").strip()
        if not key:
            return None
        try:
            from ai.core.database import ProjectDelivery, SessionLocal
            db = SessionLocal()
            try:
                row = None
                if (ticket.project_id or "").strip():
                    row = db.query(ProjectDelivery).filter(
                        ProjectDelivery.code == ticket.project_id.strip()
                    ).first()
                if not row and (ticket.project_name or "").strip():
                    row = db.query(ProjectDelivery).filter(
                        ProjectDelivery.name == ticket.project_name.strip()
                    ).first()
                return row
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] 查项目失败: {e}")
            return None

    @staticmethod
    def _resolve_contact_assignee(ticket: TicketContext) -> Optional[str]:
        """项目对接人 users.id；没有则 None。"""
        row = DispatchFlow._load_project_row(ticket)
        if not row:
            return None
        cid = (row.contact_person_id or "").strip()
        if not cid:
            logger.info(
                f"[派单:{ticket.id}] 项目对接人缺失（contact_person_id 为空）: "
                f"project={row.code or row.name}"
            )
            return None
        return cid

    def _pm_from_row(self, row, engineers) -> Tuple[Optional[str], Optional[str]]:
        """本单 project.project_manager_id；姓名优先用表字段，再从候选人/用户表补。"""
        if row is None:
            return None, None
        pmid = (getattr(row, "project_manager_id", None) or "").strip()
        if not pmid:
            return None, None
        name = (getattr(row, "project_manager", None) or "").strip()
        if not name:
            name = next((e.name for e in (engineers or []) if e.id == pmid), "")
        if not name:
            looked = self._lookup_user_name(pmid)
            name = looked or pmid
        return pmid, name

    def _lookup_user_name(self, user_id: str) -> Optional[str]:
        try:
            from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import _fetch_from_users_table
            rows = _fetch_from_users_table() or []
        except Exception as e:
            logger.warning(f"[派单] 查用户姓名失败 user_id={user_id}: {e}")
            return None
        for r in rows:
            if str((r or {}).get("id")) == str(user_id):
                nm = ((r or {}).get("name") or "").strip()
                return nm or None
        return None

    def _config_project_manager(self) -> Optional[tuple]:
        """配置里的兜底项目经理。空或查不到人 → None。"""
        pm_id = (getattr(self._config, "project_manager_id", "") or "").strip()
        if not pm_id:
            return None
        nm = self._lookup_user_name(pm_id) or (
            getattr(self._config, "project_manager_name", "") or ""
        ).strip()
        if not nm:
            logger.warning(
                f"[派单] 配置了项目经理 pm_id={pm_id} 但未查到姓名，仍按该 ID 兜底"
            )
            nm = pm_id
        return str(pm_id), nm

    # ── 用户倾向处理人解析（预留功能，Step 4 加权 / 强制保留用）──
    @staticmethod
    def _resolve_preferred_assignee(
        ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Optional[str]:
        """解析用户提单时填写的"倾向处理人"，返回工程师 users.id。

        - 数据源：ticket.preferred_assignee（前端传工程师 users.id，预留字段）
        - 前端未传该字段（None/空）→ 返回 None，不启用、完全向后兼容
        - 传入时按 e.id 精确匹配工程师；匹配不到 → 返回 None（不阻断派单，仅不加权）
        """
        preferred = (ticket.preferred_assignee or "").strip()
        if not preferred:
            return None
        try:
            from app.core.user_identity import to_user_id
            preferred_id = to_user_id(preferred) or preferred
        except Exception:
            preferred_id = preferred
        matched = next((e for e in engineers if e.id == preferred_id), None)
        if matched is None:
            logger.info(
                f"[派单:{ticket.id}] 用户倾向处理人 '{preferred}' 未匹配到候选工程师，跳过加权"
            )
            return None
        return matched.id

    def _resolve_prev_assignee_id(self, ticket: TicketContext) -> Optional[str]:
        """原接单人 users.id（重派时前端传 ticket.prev_assignee）。只打标，不强制入候选。"""
        prev = (getattr(ticket, "prev_assignee", None) or "").strip()
        if not prev:
            return None
        try:
            from app.core.user_identity import to_user_id
            return to_user_id(prev) or prev
        except Exception:
            return prev

    # ── Step 5 实现: 负载均衡（对全体候选人按在途工单数打折，带查询缓存）──
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
                    f"Step5 负载均衡: {eid} 在途={count} 系数={factor:.2f} "
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
            logger.warning(f"Step5 查询在途工单失败，跳过负载均衡: {e}")
            return {}

    # ── Step 0 实现: 识别提单人期望接单人（强信号 + LLM 兜底）──
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
    ) -> Tuple[Optional[AssignmentResult], Optional[str]]:
        """识别提单人是否明确指定了期望接单人。

        两级策略：
        1. 强信号：提单 Agent 结构化输出的"指定处理人：XXX"，直接提取人名匹配（不调 LLM）。
        2. 弱信号兜底：自由文本（"这个给张三看一下"等）经轻量预判命中后，用 LLM 识别。

        Returns:
            (result, specified_unresolved)
            - 匹配成功：result 已写 preferred_id / matched_pref / pinyin_match / profile（与重派同一出口）
            - 指定了但找不到：result=None，specified_unresolved=指定名，继续智能派单
            - 未指定：result=None，specified_unresolved=None
        """
        text = f"标题: {ticket.title or ''}\n描述: {ticket.problem_description or ''}"

        # ── 1. 强信号：结构化"指定处理人：XXX"（提单 Agent 标准输出，直接匹配，不调 LLM）──
        strong_name = self._extract_strong_preferred(text)
        if strong_name:
            # 二次派单感知增强（M5/M6）：精确全等 → 拼音全拼兜底；同名/同音多人走画像完整度/单轮 LLM
            matches, pinyin_hit = self._match_engineer_with_pinyin(strong_name, engineers)
            winner = None
            if matches:
                winner, llm_reason, collision_random = await self._pick_collision(
                    ticket, strong_name, matches,
                )
                collision = len(matches) > 1
                reason = f"提单Agent指定接单人: {strong_name} → 匹配 {winner.name}"
                if pinyin_hit:
                    reason += "（按拼音匹配）"
                if llm_reason:
                    reason += f"（{llm_reason}）"
            else:
                # 指定的人在准入工程师池匹配不到（可能缺画像）→ 全量 active 用户兜底，
                # 只要确有其人就直接派过去（用户指定优先，不再要求满足工程师画像）。
                _m = self._match_preferred_everyone(strong_name)
                if _m:
                    _everyone_id, _everyone_name, _everyone_py = _m
                    from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile as _EP
                    winner = _EP(id=_everyone_id, name=_everyone_name)
                    pinyin_hit = _everyone_py
                    collision = False
                    collision_random = False
                    reason = f"提单Agent指定接单人: {strong_name} → 匹配 {winner.name}（无完整画像，按指定直接指派）"
                    if pinyin_hit:
                        reason += "（按拼音匹配）"
                    llm_reason = ""
            if winner is not None:
                # 排单强信号匹配过程：保留 INFO，便于看日志了解"为何派给此人"。
                # 行首整齐由 logging.ReadableFormatter 解决（[派单:N] 前的定位信息移至行尾）。
                logger.info(
                    f"[派单:{ticket.id}] Step0 [提单人指定-强信号] '{strong_name}' "
                    f"→ {winner.name}{'(' + winner.id + ')' if collision else ''}"
                    f"{' 同名=' + str(len(matches)) if collision else ''}"
                    f"{'[拼音]' if pinyin_hit else ''}"
                    f"{'[全量兜底/无画像]' if not matches else ''}"
                )
                return AssignmentResult(
                    engineer_id=winner.id,
                    engineer_name=winner.name,
                    # 拼音命中 confidence 降为 0.85（D7）；精确/同名评估维持 0.95
                    confidence_score=0.85 if pinyin_hit else 0.95,
                    reasoning=reason,
                    decision_type="auto",
                    name_collision=collision,
                    pinyin_match=pinyin_hit,
                    preferred_id=winner.id,
                    matched_pref=True,
                    profile=_step0_winner_profile(winner, collision_random),
                ), None
            logger.info(
                f"[派单:{ticket.id}] Step0 强信号指定 '{strong_name}' 未匹配到工程师，走正常派单"
            )
            return None, strong_name

        # ── 2. 弱信号兜底：自由文本预判命中才走 LLM（避免每单白跑一次 LLM）──
        if not self._maybe_has_preferred(text):
            return None, None

        from ai.agents.AiDiagnosisPlatform.assigner.prompts.step0 import build_weak
        prompt = build_weak(ticket)

        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=120, temperature=0.1)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step0 LLM 识别失败: {e}")
            return None, None

        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            logger.debug(f"[派单:{ticket.id}] Step0 无 JSON，raw: {response[:150]}")
            return None, None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.debug(f"[派单:{ticket.id}] Step0 JSON 解析失败，raw: {response[:200]}")
            return None, None

        if not data.get("has_preference"):
            return None, None

        preferred_name = (data.get("preferred_name") or "").strip()
        if not preferred_name:
            return None, None

        # 匹配工程师名（二次派单感知增强 M5/M6：精确→拼音；同名/同音多人走画像完整度/单轮 LLM）
        matches, pinyin_hit = self._match_engineer_with_pinyin(preferred_name, engineers)
        winner = None
        if matches:
            winner, llm_reason, collision_random = await self._pick_collision(
                ticket, preferred_name, matches,
            )
            collision = len(matches) > 1
            reason = f"提单人指定接单人: {preferred_name} → 匹配 {winner.name}"
            if pinyin_hit:
                reason += "（按拼音匹配）"
            if llm_reason:
                reason += f"（{llm_reason}）"
        else:
            # 指定的处理人在准入工程师池匹配不到（可能缺画像）→ 全量 active 用户兜底，
            # 只要确有其人就直接派过去（用户指定优先，不再要求满足工程师画像）。
            _m = self._match_preferred_everyone(preferred_name)
            if _m:
                _everyone_id, _everyone_name, _everyone_py = _m
                from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile as _EP
                winner = _EP(id=_everyone_id, name=_everyone_name)
                pinyin_hit = _everyone_py
                collision = False
                collision_random = False
                reason = f"提单人指定接单人: {preferred_name} → 匹配 {winner.name}（无完整画像，按指定直接指派）"
                if pinyin_hit:
                    reason += "（按拼音匹配）"
                llm_reason = ""
        if winner is not None:
            logger.info(
                f"[派单:{ticket.id}] Step0 [提单人指定] '{preferred_name}'"
                f" → {winner.name}{'(' + winner.id + ')' if collision else ''}"
                f"{' 同名=' + str(len(matches)) if collision else ''}"
                f"{'[拼音]' if pinyin_hit else ''}"
                f"{'[全量兜底/无画像]' if not matches else ''}"
            )
            return AssignmentResult(
                engineer_id=winner.id,
                engineer_name=winner.name,
                # 拼音命中 confidence 降为 0.85（D7）；精确/同名评估维持默认
                confidence_score=0.85 if pinyin_hit else 0.95,
                reasoning=reason,
                decision_type="auto",
                name_collision=collision,
                pinyin_match=pinyin_hit,
                preferred_id=winner.id,
                matched_pref=True,
                    profile=_step0_winner_profile(winner, collision_random),
                ), None

        logger.info(
            f"[派单:{ticket.id}] Step0 提单人指定 '{preferred_name}'，"
            f"未匹配到工程师，走正常派单"
        )
        return None, preferred_name

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
        """按姓名匹配工程师：**严格全等**（返回第一个精确命中，兼容旧调用）。

        二次派单感知增强（M5）推荐使用 _match_engineer_names 获取全部命中做同名处理；
        不允许"包含/被包含"匹配（否则"张三"会误命中"张三丰"），拼音兜底属 M6。
        """
        if not name:
            return None
        for e in engineers:
            if e.name == name:
                return e
        return None

    @classmethod
    def _match_engineer_names(
        cls, name: str, engineers: List[EngineerProfile],
    ) -> List[EngineerProfile]:
        """按姓名匹配工程师：返回**全部命中的同名集合**（精确 + 包含并集）。

        二次派单感知增强（M5/D6b）：匹配到多人即视为同名（name_collision），
        并按画像完整度排序（`missing` 少者优先，即 department/job_level/
        responsibility_modules 命中数多者靠前），供上层做同名抉择。
        """
        if not name or not engineers:
            return []
        # 姓名**严格全等**匹配（不许"包含/被包含"——否则"张三"会误命中"张三丰"）。
        # 拼音兜底属于 M6；此处仅精确命中，同名=多个姓名完全相同的工程师。
        hits: List[EngineerProfile] = [e for e in engineers if e.name == name]
        if len(hits) <= 1:
            return hits
        # 同名多人 → 按画像完整度排序（missing 少者优先）
        def _completeness(e: EngineerProfile) -> int:
            p = _engineer_profile_dict(e)
            return -len(p.get("missing") or [])
        try:
            hits.sort(key=_completeness, reverse=True)
        except Exception:
            pass
        return hits

    @staticmethod
    def _to_pinyin(name: str) -> str:
        """中文姓名 → 全拼小写（多音字取常用读音，去掉声调；非中文原样保留）。

        仅供拼音兜底匹配用；pypinyin 不可用或转换失败时返回空串（上层自然降级）。
        """
        if not name:
            return ""
        # 输入本身已是拼音（如 zhangsan，不含中文字符）→ 原样小写返回，不再过 pypinyin
        if not any('\u4e00' <= ch <= '\u9fff' for ch in name):
            return name.lower()
        try:
            from pypinyin import pinyin, Style
            parts = pinyin(name, style=Style.NORMAL, errors="ignore")
            return "".join(p[0] for p in parts if p)
        except Exception:
            return ""

    @classmethod
    def _match_engineer_with_pinyin(
        cls, name: str, engineers: List[EngineerProfile],
    ) -> tuple:
        """二次派单感知增强（M6/D7）：姓名匹配统一入口，返回 (matches, pinyin_hit)。

        先 **严格全等**（`_match_engineer_names`）；未命中再 **拼音全拼兜底**（多音字取常用读音）。
        - matches: 命中集合（已按画像完整度排序；可能含多个 = 同名/同音）
        - pinyin_hit: 是否经由拼音命中（精确未命中才可能为 True）
        """
        exact = cls._match_engineer_names(name, engineers)
        if exact:
            return exact, False
        # 精确未命中 → 拼音全拼兜底
        name_py = cls._to_pinyin(name)
        if not name_py:
            return [], False
        py_hits = [e for e in engineers if e and cls._to_pinyin(e.name) == name_py]
        if not py_hits:
            return [], False
        # 同音多人 → 按画像完整度排序（复用同名排序逻辑）
        def _completeness(e: EngineerProfile) -> int:
            p = _engineer_profile_dict(e)
            return -len(p.get("missing") or [])
        try:
            py_hits.sort(key=_completeness, reverse=True)
        except Exception:
            pass
        return py_hits, True

    def _match_preferred_everyone(self, name: str) -> Optional[tuple]:
        """全量 active 用户兜底匹配（含无画像者）：精确全等 → 拼音全拼；返回 (uid, uname, pinyin_hit)。

        用途：当指定的处理人在「准入画像工程师列表」里匹配不到（例如 TA 缺部门/责任模块，没进派单候选池），
        只要该人确实存在于 users（status='active'），仍按用户意愿直接指派——即「指定了就直接派过去」。
        返回 None 表示全量 active 用户里也没有该人（只能走正常派单）。
        """
        if not name:
            return None
        try:
            from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import _fetch_from_users_table
            rows = _fetch_from_users_table()
        except Exception as e:
            logger.warning(f"[派单] Step0 全量兜底匹配加载用户失败: {e}")
            return None
        if not rows:
            return None
        name = name.strip()
        # 精确全等
        exact = [r for r in rows if (r.get("name") or "").strip() == name]
        if exact:
            return exact[0]["id"], exact[0]["name"], False
        # 拼音全拼兜底
        name_py = self._to_pinyin(name)
        if name_py:
            py = [r for r in rows if self._to_pinyin((r.get("name") or "").strip()) == name_py]
            if py:
                return py[0]["id"], py[0]["name"], True
        return None

    async def _pick_collision(
        self, ticket: TicketContext, pref_name: str, matches: List[EngineerProfile],
    ) -> tuple:
        """二次派单感知增强（M5/D6b）：同名多人抉择。

        前提：matches 已按画像完整度排序（_match_engineer_names 结果）。
        - 完整度不同（第一个最完整）→ 取 matches[0]。
        - 完整度相同 → 单轮 LLM 抉择（JSON {selected_id, reason} / {can_determine:false}）；
          分辨不出 → 随机选（画像多半两边都不完整，tip 提醒补画像）；LLM 失败 → 取第一个。
        返回 (winner, llm_reason, collision_random)。异常安全：任何失败都回退到 matches[0]。
        """
        if not matches:
            return None, "", False
        if len(matches) == 1:
            return matches[0], "", False
        # 画像完整度是否相同（前两名 missing 数是否相等）
        def _missing(e: EngineerProfile) -> int:
            return len((_engineer_profile_dict(e).get("missing")) or [])
        try:
            _same = _missing(matches[0]) == _missing(matches[1])
        except Exception:
            _same = False
        if not _same:
            # 完整度不同 → 取最完整者
            return matches[0], "", False

        # 完整度相同 → 单轮 LLM 抉择
        cand_list = "、".join(llm_person_label(eng=e) for e in matches)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            from ai.agents.AiDiagnosisPlatform.assigner.prompts.step0 import build_collision
            prompt = build_collision(ticket, cand_list)
            resp = await llm.complete(prompt, max_tokens=200, temperature=0.2)
            m = re.search(r"\{.*\}", resp or "", re.DOTALL)
            if m:
                data = json.loads(m.group())
                # prompt 约定无法区分时输出 can_determine:false；以前误写成 is True，这条出口等于睡着。
                if data.get("can_determine") is False:
                    logger.info(
                        f"[派单:{ticket.id}] 同名 '{pref_name}' LLM 无法区分，随机选择一个"
                    )
                    return random.choice(matches), "同名无法区分，随机选择", True
                sel = data.get("selected_id") or ""
                reason = (data.get("reason") or "").strip()
                if any(e.id == sel for e in matches):
                    return next(e for e in matches if e.id == sel), reason, False
                logger.info(
                    f"[派单:{ticket.id}] 同名 '{pref_name}' LLM 返回 id 不在候选内({sel})，随机选择"
                )
                return random.choice(matches), "同名未能区分，随机选择", True
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] 同名 '{pref_name}' 单轮 LLM 抉择失败，兜底取第一个: {e}")
        return matches[0], "同名评估失败，已按默认选择", False

    def reload_config(self):
        self._config.reload()
        invalidate_history_cache()
        invalidate_expertise_cache()
