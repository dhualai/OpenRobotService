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
        ├── 画像召回：看职责卡片推断谁能接
        ├── 相似工单：近邻旧单的处理人（可空）
        └── 问题簇：这类问题堆里的常客（可空）
        │
        ▼
    【Step 4 精排】三路绝对 0～1 取最高；职级折扣 × 部门soft_prior；倾向人保底；对接人只打标
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
from ai.agents.AiDiagnosisPlatform.assigner.recall.history_recall import HistoryRecall
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


def _step0_winner_profile(
    eng: "EngineerProfile",
    collision_random: bool = False,
    specified_name: Optional[str] = None,
    specified_multi: bool = False,
) -> Dict:
    """Step0 落库画像：缺项进 missing；同名随机选中再打 collision_random，供 tip 提醒补画像。

    拼音命中时把用户原文（如「加双」）写入 specified_name，和工程师名（如「贾爽」）对照。
    强信号里写了多个人时打 specified_multi；按顺序派上第一个找得到的人。
    """
    prof = _engineer_profile_dict(eng)
    if collision_random:
        prof["collision_random"] = True
    if specified_multi:
        prof["specified_multi"] = True
    query = (specified_name or "").strip()
    if query and query != (eng.name or "").strip():
        prof["specified_name"] = query
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
        self._history_recall = HistoryRecall(config=self._config)      # 相似工单：近邻聚人
        self._expertise_recall = ExpertiseRecall(config=self._config)   # 问题簇：类型熟手
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

        # ── Step 3: 三路召回（画像 / 相似工单 / 问题簇），后两路可空 ──
        recall_result = RecallResult()
        sim_fb: Dict[str, Dict[str, str]] = {}
        try:
            l1_fut, sim_fut, clu_fut = await asyncio.gather(
                self._llm_recall.arecall(ticket=ticket_context, engineers=candidates),
                self._history_recall.arecall(ticket=ticket_context, feedback=sim_fb),
                self._expertise_recall.arecall(ticket=ticket_context),
                return_exceptions=True,
            )
        except Exception as e:
            logger.warning(f"{ltag} Step3 并行召回批次异常: {e}")
            l1_fut = sim_fut = clu_fut = {}

        if isinstance(l1_fut, Exception):
            logger.warning(f"{ltag} Step3 画像召回异常: {l1_fut}")
            recall_result.llm_recall = {}
            recall_result.llm_reasons = {}
        else:
            scores, reasons = LlmRecall.unpack_arecall(l1_fut)
            recall_result.llm_recall = scores
            recall_result.llm_reasons = reasons
            self._log_recall_top(
                ltag, "画像", recall_result.llm_recall, candidates, "画像召回(逐人置信)", count=8,
            )

        if isinstance(sim_fut, Exception):
            logger.warning(f"{ltag} Step3 相似工单召回异常: {sim_fut}")
            recall_result.similar_recall = {}
        else:
            recall_result.similar_recall = sim_fut or {}
            recall_result.misassign_confirmed = dict(sim_fb.get("confirmed") or {})
            recall_result.misassign_rejected = dict(sim_fb.get("rejected") or {})
            self._log_recall_top(
                ltag, "相似", recall_result.similar_recall, candidates, "相似工单(可空)", count=8,
            )
        if not recall_result.similar_recall:
            logger.info(f"{ltag} Step3 相似工单: 空")

        if isinstance(clu_fut, Exception):
            logger.warning(f"{ltag} Step3 问题簇召回异常: {clu_fut}")
            recall_result.cluster_recall = {}
        else:
            recall_result.cluster_recall = clu_fut or {}
            self._log_recall_top(
                ltag, "问题簇", recall_result.cluster_recall, candidates, "问题簇(可空)", count=8,
            )
        if not recall_result.cluster_recall:
            logger.info(f"{ltag} Step3 问题簇: 空")

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
        """对接人 → 本单项目经理 → 配置项目经理。都空则返回未指派结果（写 tip，不编精排 #1）。

        这三人都是配置的兜底人，不看智能派单准入门槛（画像可以不完整）。
        """
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
            tag = "".join(f" [{t}]" for t in score_tag_labels(d))
            parts.append(
                f"#{rank} {nm}(L{d.get('job_level','?')}) "
                f"总={d.get('total_score',0):.2f} "
                f"LLM={d.get('llm_score',0):.2f} "
                f"相似={d.get('similar_score', d.get('history_score',0)):.2f} "
                f"簇={d.get('cluster_score',0):.2f}"
                f"{tag}"
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

    # ── Step 0 实现: 识别提单人期望接单人（强信号 + LLM 兜底）──
    # 强信号：提单 Agent 结构化输出的"[指定处理人：贾爽]"等格式
    _PREFERRED_STRONG_RE = None

    @staticmethod
    def _loads_llm_json(raw: Optional[str]) -> Optional[dict]:
        """解析 Step0 LLM 输出。中文引号、第一段扁平 JSON 都能认。"""
        if not isinstance(raw, str) or not raw.strip():
            return None
        txt = (
            raw.replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2018", "'").replace("\u2019", "'")
        )
        m = re.search(r"\{[^{}]*\}", txt)
        if not m:
            m = re.search(r"\{.*\}", txt, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _split_preferred_names(raw: str) -> List[str]:
        """把「张三、李四」拆成多个人名；每人仍按 2～6 字。"""
        parts = [p.strip() for p in re.split(r"[、，,；;]+", raw or "") if p.strip()]
        return [p for p in parts if 2 <= len(p) <= 6]

    @classmethod
    def _parse_strong_preferred_names(cls, text: str) -> List[str]:
        """强信号抽出全部人名，按书写顺序。无人则空列表。"""
        if not text:
            return []
        if cls._PREFERRED_STRONG_RE is None:
            # 捕获到 ] / 空白 / 冒号为止；顿号逗号留在组内再拆
            cls._PREFERRED_STRONG_RE = re.compile(
                r"指定(?:处理人|人|人员)[:：]\s*([^\]\s:：）)】]{2,40})"
            )
        m = cls._PREFERRED_STRONG_RE.search(text)
        if not m:
            return []
        return cls._split_preferred_names(m.group(1).strip())

    @classmethod
    def _parse_strong_preferred(cls, text: str) -> Tuple[Optional[str], bool]:
        """强信号抽人名：返回 (第一个人, 是否写了多个)。"""
        names = cls._parse_strong_preferred_names(text)
        if not names:
            return None, False
        return names[0], len(names) >= 2

    @classmethod
    def _extract_strong_preferred(cls, text: str) -> Optional[str]:
        """从结构化"指定处理人：XXX"强信号中提取人名（不调 LLM）。

        例如 "指定处理人：贾爽" / "[指定处理人：贾爽]" / "指定处理人:张三"。
        多人返回名单里的第一个（匹配时按顺序往后试）。未命中返回 None。
        """
        first, _ = cls._parse_strong_preferred(text)
        return first

    async def _resolve_preferred_name(
        self,
        ticket: TicketContext,
        name: str,
        engineers: List[EngineerProfile],
        reason_prefix: str,
    ):
        """匹配一个指定名：精确 → 拼音 → 全量用户兜底。找不到返回 None。"""
        matches, pinyin_hit = self._match_engineer_with_pinyin(name, engineers)
        if matches:
            winner, llm_reason, collision_random = await self._pick_collision(
                ticket, name, matches,
            )
            collision = len(matches) > 1
            reason = f"{reason_prefix}{name} → 匹配 {winner.name}"
            if pinyin_hit:
                reason += "（按拼音匹配）"
            if llm_reason:
                reason += f"（{llm_reason}）"
            return winner, pinyin_hit, collision, collision_random, reason, matches
        _m = self._match_preferred_everyone(name)
        if not _m:
            return None
        stubs, everyone_py = _m
        if not stubs:
            return None
        winner, llm_reason, collision_random = await self._pick_collision(
            ticket, name, stubs,
        )
        collision = len(stubs) > 1
        reason = (
            f"{reason_prefix}{name} → 匹配 {winner.name}"
            "（无完整画像，按指定直接指派）"
        )
        if everyone_py:
            reason += "（按拼音匹配）"
        if llm_reason:
            reason += f"（{llm_reason}）"
        return winner, everyone_py, collision, collision_random, reason, stubs

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
        strong_names = self._parse_strong_preferred_names(text)
        if strong_names:
            specified_multi = len(strong_names) >= 2
            for idx, strong_name in enumerate(strong_names):
                resolved = await self._resolve_preferred_name(
                    ticket, strong_name, engineers, "提单人指定: ",
                )
                if resolved is None:
                    continue
                winner, pinyin_hit, collision, collision_random, reason, matches = resolved
                skip_note = ""
                if specified_multi:
                    skip_note = f"（多人第{idx + 1}人）" if idx else "（多人只派一人）"
                logger.info(
                    f"[派单:{ticket.id}] Step0 [提单人指定-强信号] '{strong_name}'"
                    f"{skip_note}"
                    f" → {winner.name}{'(' + winner.id + ')' if collision else ''}"
                    f"{' 同名=' + str(len(matches)) if collision else ''}"
                    f"{'[拼音]' if pinyin_hit else ''}"
                    f"{'[全量兜底/无画像]' if '无完整画像' in reason else ''}"
                )
                return AssignmentResult(
                    engineer_id=winner.id,
                    engineer_name=winner.name,
                    confidence_score=0.85 if pinyin_hit else 0.95,
                    reasoning=reason,
                    decision_type="auto",
                    name_collision=collision,
                    pinyin_match=pinyin_hit,
                    preferred_id=winner.id,
                    matched_pref=True,
                    profile=_step0_winner_profile(
                        winner, collision_random,
                        specified_name=strong_name,
                        specified_multi=specified_multi,
                    ),
                ), None
            logger.info(
                f"[派单:{ticket.id}] Step0 强信号指定 {strong_names!r} 均未匹配，走正常派单"
            )
            return None, strong_names[0]

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

        data = self._loads_llm_json(response)
        if not data:
            logger.debug(f"[派单:{ticket.id}] Step0 无 JSON，raw: {(response or '')[:150]}")
            return None, None

        if not data.get("has_preference"):
            return None, None

        preferred_name = (data.get("preferred_name") or "").strip()
        if not preferred_name:
            return None, None

        resolved = await self._resolve_preferred_name(
            ticket, preferred_name, engineers, "提单人指定接单人: ",
        )
        if resolved is not None:
            winner, pinyin_hit, collision, collision_random, reason, matches = resolved
            logger.info(
                f"[派单:{ticket.id}] Step0 [提单人指定] '{preferred_name}'"
                f" → {winner.name}{'(' + winner.id + ')' if collision else ''}"
                f"{' 同名=' + str(len(matches)) if collision else ''}"
                f"{'[拼音]' if pinyin_hit else ''}"
                f"{'[全量兜底/无画像]' if '无完整画像' in reason else ''}"
            )
            return AssignmentResult(
                engineer_id=winner.id,
                engineer_name=winner.name,
                confidence_score=0.85 if pinyin_hit else 0.95,
                reasoning=reason,
                decision_type="auto",
                name_collision=collision,
                pinyin_match=pinyin_hit,
                preferred_id=winner.id,
                matched_pref=True,
                profile=_step0_winner_profile(
                    winner, collision_random, specified_name=preferred_name,
                ),
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
        """按姓名匹配工程师：返回**姓名严格全等**的全部命中（即同名集合，不含"包含/被包含"）。

        二次派单感知增强（M5/D6b）：匹配到多个姓名完全相同的人即视为同名（name_collision），
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

    @staticmethod
    def _everyone_stubs(rows: list) -> List[EngineerProfile]:
        stubs: List[EngineerProfile] = []
        for r in rows or []:
            if not r or not r.get("id"):
                continue
            mods = r.get("responsibility_modules") or {}
            if not isinstance(mods, dict):
                mods = {}
            jl = r.get("job_level")
            stubs.append(EngineerProfile(
                id=r["id"],
                name=(r.get("name") or "").strip() or r["id"],
                department=(r.get("department") or "").strip() or None,
                job_level=jl if jl else 0,
                responsibility_modules=mods,
            ))
        return stubs

    def _match_preferred_everyone(self, name: str) -> Optional[tuple]:
        """全量 active 用户兜底：精确全等 → 拼音全拼。返回 (stubs, pinyin_hit)。

        准入池没有此人（缺部门/职级/责任模块）时，只要在职名单里有，仍按指定派。
        同名/同音多人交给 _pick_collision，不再取表里第一个。
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
        exact = [r for r in rows if (r.get("name") or "").strip() == name]
        if exact:
            stubs = self._everyone_stubs(exact)
            return (stubs, False) if stubs else None
        name_py = self._to_pinyin(name)
        if name_py:
            py = [r for r in rows if self._to_pinyin((r.get("name") or "").strip()) == name_py]
            if py:
                stubs = self._everyone_stubs(py)
                return (stubs, True) if stubs else None
        return None

    async def _pick_collision(
        self, ticket: TicketContext, pref_name: str, matches: List[EngineerProfile],
    ) -> tuple:
        """二次派单感知增强（M5/D6b）：同名多人抉择。

        前提：matches 已按画像完整度排序（_match_engineer_names 结果）。
        只让「最完整那一档」参与：缺项数 = 第一名的人进 LLM / 随机，残缺更差的不争。
        该档仅一人 → 直接取；多人 → 单轮 LLM；分辨不出 / id 不在档内 → 在该档随机；
        LLM 失败 → 该档第一个。异常安全：任何失败都回退到 pool[0]。
        返回 (winner, llm_reason, collision_random)。
        """
        if not matches:
            return None, "", False
        if len(matches) == 1:
            return matches[0], "", False

        def _missing(e: EngineerProfile) -> int:
            return len((_engineer_profile_dict(e).get("missing")) or [])

        try:
            best = _missing(matches[0])
            # matches[0] 自己就满足 _missing(e)==best，故 pool 一定非空（无需 or 兜底）
            pool = [e for e in matches if _missing(e) == best]
        except Exception:
            pool = list(matches)
        if len(pool) == 1:
            return pool[0], "", False

        cand_list = "、".join(llm_person_label(eng=e) for e in pool)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            from ai.agents.AiDiagnosisPlatform.assigner.prompts.step0 import build_collision
            prompt = build_collision(ticket, cand_list)
            resp = await llm.complete(prompt, max_tokens=200, temperature=0.2)
            data = self._loads_llm_json(resp)
            if data:
                # prompt 约定无法区分时输出 can_determine:false
                if data.get("can_determine") is False:
                    logger.info(
                        f"[派单:{ticket.id}] 同名 '{pref_name}' LLM 无法区分，随机选择一个"
                    )
                    return random.choice(pool), "同名无法区分，随机选择", True
                sel = data.get("selected_id") or ""
                reason = (data.get("reason") or "").strip()
                if any(e.id == sel for e in pool):
                    return next(e for e in pool if e.id == sel), reason, False
                logger.info(
                    f"[派单:{ticket.id}] 同名 '{pref_name}' LLM 返回 id 不在最完整档({sel})，随机选择"
                )
                return random.choice(pool), "同名未能区分，随机选择", True
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] 同名 '{pref_name}' 单轮 LLM 抉择失败，兜底取第一个: {e}")
        return pool[0], "同名评估失败，已按默认选择", False

    def reload_config(self):
        self._config.reload()
        invalidate_expertise_cache()
        from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import (
            invalidate_cache as invalidate_history_sync,
        )
        from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import (
            invalidate_cache as invalidate_personnel,
        )
        invalidate_history_sync()
        invalidate_personnel()
        rec = getattr(self, "_expertise_recall", None)
        if rec is not None and hasattr(rec, "reload_cluster_params"):
            rec.reload_cluster_params()
