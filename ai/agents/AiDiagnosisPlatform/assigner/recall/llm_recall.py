"""Step3·画像召回：工单 + 全员职责卡片 → 逐人打分。

三路里看人的那一路。能读 duty_text 和责任模块，理解模糊边界
（「这个人主要负责地图但也参与后端」）。不看历史工单。
"""

import json, re
from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import llm_person_label, match_engineer_from_llm
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class LlmRecall:
    """画像召回：按人数单轮或分批，产出 {id: score} + 原因给精排。

    - 人少（≤ single_round_max）：单轮请模型在 single_top_min～single_top_max 内选人
    - 人多：按 batch_size 分批，每批 batch_top_min～batch_top_max，并集全部进 Step4，
      不再合并决选。
    解析侧只按区间上限封顶，不强制凑到下限。任一轮 LLM 失败仅跳过该批，不阻断。
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

        # 上限：新键优先；兼容旧 single_top_k / final_top_k / batch_top_k
        if "single_top_max" in lr:
            self._single_top_max = _i("single_top_max", 6)
        elif "single_top_k" in lr:
            self._single_top_max = _i("single_top_k", 6)
        else:
            self._single_top_max = _i("final_top_k", 6)
        self._single_top_min = _i("single_top_min", 3)
        if self._single_top_min > self._single_top_max:
            self._single_top_min = self._single_top_max

        if "batch_top_max" in lr:
            self._batch_top_max = _i("batch_top_max", 4)
        else:
            self._batch_top_max = _i("batch_top_k", 4)
        self._batch_top_min = _i("batch_top_min", 2)
        if self._batch_top_min > self._batch_top_max:
            self._batch_top_min = self._batch_top_max

        # 兼容旧测试读 _single_top_k / _batch_top_k（表示上限）
        self._single_top_k = self._single_top_max
        self._batch_top_k = self._batch_top_max

        self._single_round_max = _i("single_round_max", 12)
        self._batch_size = _i("batch_size", 8)

    @staticmethod
    def _clip_top(
        scores: Dict[str, float], reasons: Dict[str, str], k: int,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """按分数留前 k 名；k 大于人数则全留。不强制凑满。"""
        if not scores or k <= 0:
            return {}, {}
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k]
        keep = [eid for eid, _ in top]
        return dict(top), {eid: (reasons or {}).get(eid, "") for eid in keep}

    async def _llm_score_batch(
        self,
        ticket: TicketContext,
        engineers: List[EngineerProfile],
        top_min: int,
        top_max: int,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """对一组工程师调 LLM；按 top_max 封顶，不强制凑到 top_min。失败返回空。"""
        if not engineers:
            return {}, {}
        hi = min(max(1, top_max), len(engineers))
        lo = min(max(1, top_min), hi)
        prompt = self._build_prompt(ticket, engineers, top_min=lo, top_max=hi)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=1200, temperature=0.0)
            logger.info(
                f"[派单:{ticket.id}] Step3 画像 LLM原始输出"
                f"(候选{len(engineers)}人,要{lo}～{hi}): {response[:800]}"
            )
            scores, reasons = self._parse(response, engineers)
            return self._clip_top(scores, reasons, hi)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step3 画像召回失败: {e}")
            return {}, {}

    @staticmethod
    def unpack_arecall(result) -> Tuple[Dict[str, float], Dict[str, str]]:
        """把 arecall 返回值拆成（分数, 理由）。异常或空 → 两个空 dict。

        理由必须跟这次返回的分数走，不能事后读 self.last_reasons：
        Worker 里两张单可能同时跑，会把别人的理由盖进来。
        """
        if result is None or isinstance(result, Exception):
            return {}, {}
        if isinstance(result, tuple):
            scores = result[0] if result else {}
            reasons = result[1] if len(result) > 1 else {}
            return dict(scores or {}), dict(reasons or {})
        if isinstance(result, dict):
            return dict(result), {}
        return {}, {}

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """人少单轮按 3～6 选；人多分批每批 2～4，合并后全部进精排，不再决选。"""
        self.last_reasons = {}
        if not engineers:
            return {}, {}

        n = len(engineers)
        reasons: Dict[str, str] = {}

        if n <= self._single_round_max:
            scores, reasons = await self._llm_score_batch(
                ticket, engineers,
                top_min=self._single_top_min,
                top_max=self._single_top_max,
            )
            self.last_reasons = reasons
            logger.info(
                f"[派单:{ticket.id}] Step3 画像 单轮 {self._single_top_min}～"
                f"{self._single_top_max} 人数={n} 输出={len(scores)}人"
            )
            return scores, reasons

        stage1: Dict[str, float] = {}
        batches = [
            engineers[i:i + self._batch_size]
            for i in range(0, n, self._batch_size)
        ]
        logger.info(
            f"[派单:{ticket.id}] Step3 画像 分批召回 总人数={n} 分{len(batches)}批 "
            f"每批{self._batch_top_min}～{self._batch_top_max}"
            f"（合并后全部进精排，不再决选）"
        )
        for bi, batch in enumerate(batches, 1):
            scores, batch_reasons = await self._llm_score_batch(
                ticket, batch,
                top_min=self._batch_top_min,
                top_max=self._batch_top_max,
            )
            hi = min(self._batch_top_max, len(batch))
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:hi]
            stage1.update(dict(top))
            for eid, _ in top:
                if eid in batch_reasons:
                    reasons[eid] = batch_reasons[eid]
            top_names = []
            for eid, sc in top:
                hit = next((e for e in batch if e.id == eid), None)
                top_names.append(
                    f"{llm_person_label(eng=hit) if hit else llm_person_label(eid)}:{sc:.2f}"
                )
            logger.debug(
                f"[派单:{ticket.id}] Step3 画像   批次{bi}/{len(batches)} 人数={len(batch)} "
                f"命中={len(top)}人 [{', '.join(top_names)}]"
            )

        scores, reasons = self._keep_batch_union(stage1, reasons)
        self.last_reasons = reasons
        if not scores:
            logger.warning(f"[派单:{ticket.id}] Step3 画像 分批召回无胜者，返回空")
            return {}, {}
        logger.info(
            f"[派单:{ticket.id}] Step3 画像 分批合并 {n}→{len(scores)}人"
            f"（各批最多 Top{self._batch_top_max} 全保留）"
        )
        return scores, reasons

    @staticmethod
    def _keep_batch_union(
        stage1: Dict[str, float], reasons: Dict[str, str],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """各批胜者并集全部保留，不再截断。"""
        if not stage1:
            return {}, {}
        return dict(stage1), {eid: (reasons or {}).get(eid, "") for eid in stage1}

    def _build_prompt(
        self, ticket, engineers, top_min: int = 3, top_max: int = 6, top_k: int | None = None,
    ):
        """top_k 仅兼容旧调用：当作 top_max，且 top_min=top_max（钉死人数的旧测例）。"""
        from ai.agents.AiDiagnosisPlatform.assigner.prompts.step3 import build_l1
        if top_k is not None:
            top_min = top_max = max(1, int(top_k))
        return build_l1(ticket, engineers, top_min=top_min, top_max=top_max)

    def _parse(
        self, response: str, engineers: List[EngineerProfile],
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            logger.debug(f"Step3 画像 LLM 返回无 JSON，raw: {response[:200]}")
            return {}, {}
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.debug(f"Step3 画像 JSON 解析失败，raw: {response[:300]}")
            return {}, {}

        rankings = data.get("rankings", [])
        if not isinstance(rankings, list) or not rankings:
            logger.debug(f"Step3 画像 rankings 为空或非列表: {rankings}")
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
            logger.debug(f"Step3 画像 ID 未匹配 {len(not_found)}: {not_found[:5]}")
        return scores, reasons
