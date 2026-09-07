"""L1 纯LLM召回：工单 + 全员画像 → LLM 直接推荐 Top-K

这是三路召回中语义理解最强的一路。LLM 能同时看到所有人的 duty_text
和 responsibility_modules，理解模糊边界（"这个人主要负责地图但也参与后端"）。
"""

import json, re
from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import llm_person_label, match_engineer_from_llm
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class LlmRecall:
    """分层 L1 召回：按人数单轮或分批，产出 {id: score} + 原因给精排。

    - 人少（≤ single_round_max）：单轮只要 Top single_top_k（默认 5）
    - 人多：按 batch_size 分批，每批只要 Top batch_top_k（默认 3），
      各批胜者全部保留进 Step4，不再合并决选、不再按 5 截断。
    任一轮 LLM 失败仅跳过该批/该组，不阻断。
    """

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        self.last_reasons: Dict[str, str] = {}
        lr = getattr(self._config, "llm_recall", None) or {}
        if not isinstance(lr, dict):
            lr = {}

        def _i(key: str, default: int) -> int:
            try:
                return max(1, int(lr.get(key, default)))
            except (TypeError, ValueError):
                return default

        if "single_top_k" in lr:
            self._single_top_k = _i("single_top_k", 5)
        else:
            self._single_top_k = _i("final_top_k", 5)
        self._batch_top_k = _i("batch_top_k", 3)
        self._single_round_max = _i("single_round_max", 12)
        self._batch_size = _i("batch_size", 8)

    @staticmethod
    def _clip_top(
        scores: Dict[str, float], reasons: Dict[str, str], k: int,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """按分数留前 k 名；k 大于人数则全留。"""
        if not scores or k <= 0:
            return {}, {}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        keep = [eid for eid, _ in top]
        return dict(top), {eid: (reasons or {}).get(eid, "") for eid in keep}

    async def _llm_score_batch(
        self, ticket: TicketContext, engineers: List[EngineerProfile], top_k: int,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """对一组工程师调 LLM，返回该组 top_k 的 {id: score} 与 {id: reason}。失败返回空。"""
        if not engineers:
            return {}, {}
        k = min(max(1, top_k), len(engineers))
        prompt = self._build_prompt(ticket, engineers, top_k=k)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=1200, temperature=0.0)
            logger.info(
                f"[派单:{ticket.id}] Step3-L1 LLM原始输出(候选{len(engineers)}人,要Top{k}): {response[:800]}"
            )
            scores, reasons = self._parse(response, engineers)
            return self._clip_top(scores, reasons, k)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step3-L1 LLM召回失败: {e}")
            return {}, {}

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """人少单轮 Top single_top_k；人多分批每批 Top batch_top_k，合并后全部进精排。

        分批不再做第二轮决选。任一轮 LLM 失败仅跳过该批，不阻断。
        """
        self.last_reasons = {}
        if not engineers:
            return {}

        n = len(engineers)
        k_single = min(self._single_top_k, n)
        reasons: Dict[str, str] = {}

        # ── 候选人数少：单轮只要 Top-K ──
        if n <= self._single_round_max:
            scores, reasons = await self._llm_score_batch(
                ticket, engineers, top_k=k_single,
            )
            scores, reasons = self._clip_top(scores, reasons, k_single)
            self.last_reasons = reasons
            logger.info(
                f"[派单:{ticket.id}] Step3-L1 单轮 Top{k_single} 人数={n} 输出={len(scores)}人"
            )
            return scores

        # ── 候选人数多：分批召回，各批胜者全部保留进 Step4 ──
        stage1: Dict[str, float] = {}
        batches = [
            engineers[i:i + self._batch_size]
            for i in range(0, n, self._batch_size)
        ]
        logger.info(
            f"[派单:{ticket.id}] Step3-L1 分批召回 总人数={n} 分{len(batches)}批 "
            f"每批Top{self._batch_top_k}（合并后全部进精排，不再决选）"
        )
        for bi, batch in enumerate(batches, 1):
            scores, batch_reasons = await self._llm_score_batch(
                ticket, batch, top_k=min(self._batch_top_k, len(batch)),
            )
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: self._batch_top_k]
            stage1.update(dict(top))
            for eid, _ in top:
                if eid in batch_reasons:
                    reasons[eid] = batch_reasons[eid]
            top_names = []
            for eid, sc in top:
                hit = next((e for e in batch if e.id == eid), None)
                top_names.append(f"{llm_person_label(eng=hit) if hit else llm_person_label(eid)}:{sc:.2f}")
            logger.debug(
                f"[派单:{ticket.id}] Step3-L1   批次{bi}/{len(batches)} 人数={len(batch)} "
                f"命中={len(top)}人 [{', '.join(top_names)}]"
            )

        scores, reasons = self._keep_batch_union(stage1, reasons)
        self.last_reasons = reasons
        if not scores:
            logger.warning(f"[派单:{ticket.id}] Step3-L1 分批召回无胜者，返回空")
            return {}
        logger.info(
            f"[派单:{ticket.id}] Step3-L1 分批合并 {n}→{len(scores)}人（各批 Top{self._batch_top_k} 全保留）"
        )
        return scores

    @staticmethod
    def _keep_batch_union(
        stage1: Dict[str, float], reasons: Dict[str, str],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """各批胜者并集全部保留，不按 single_top_k 再截。"""
        if not stage1:
            return {}, {}
        return dict(stage1), {eid: (reasons or {}).get(eid, "") for eid in stage1}

    def _build_prompt(self, ticket, engineers, top_k: int = 5):
        from ai.agents.AiDiagnosisPlatform.assigner.prompts.step3 import build_l1
        return build_l1(ticket, engineers, top_k)

    def _parse(
        self, response: str, engineers: List[EngineerProfile],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            logger.debug(f"Step3-L1 LLM 返回无 JSON，raw: {response[:200]}")
            return {}, {}
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.debug(f"Step3-L1 JSON 解析失败，raw: {response[:300]}")
            return {}, {}

        rankings = data.get("rankings", [])
        if not isinstance(rankings, list) or not rankings:
            logger.debug(f"Step3-L1 rankings 为空或非列表: {rankings}")
            return {}, {}

        scores: Dict[str, float] = {}
        reasons: Dict[str, str] = {}
        not_found = []
        for r in rankings:
            eid = (r.get("engineer_id") or "").strip()
            raw_name = (r.get("engineer_name") or "").strip()
            conf = float(r.get("confidence", 0.0))
            eng = match_engineer_from_llm(eid, engineers, raw_name)
            if eng is not None and conf > 0:
                scores[eng.id] = min(conf, 1.0)
                reasons[eng.id] = str(r.get("reason") or "").strip()
            else:
                not_found.append(f"{eid}(conf={conf})")
        if not_found:
            logger.debug(f"Step3-L1 ID 未匹配 {len(not_found)}: {not_found[:5]}")
        return scores, reasons
