"""L1 纯LLM召回：工单 + 全员画像 → LLM 直接推荐 Top-K

这是三路召回中语义理解最强的一路。LLM 能同时看到所有人的 duty_text
和 responsibility_modules，理解模糊边界（"这个人主要负责地图但也参与后端"）。
"""

import json, re
from typing import Dict, List, Optional

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")


class LlmRecall:
    """分层 L1 召回：LLM 两层评估，避免一次看 20+ 人画像。

    候选人常 20+ 人，若一次把全部画像塞给 LLM，token 巨大、响应慢、且 LLM
    对海量候选逐一打分精度下降。改为 LLM 两轮逐步聚焦：
      第一层（分批初选）：把候选人按 BATCH_SIZE 分批，每批让 LLM 选出该批最符合
        的 ROUND1_TOP_K 人（每批只出 top-K，prompt 小、判断准）；
      第二层（合并决选）：收集所有批的胜者，若人数仍 > ROUND2_MAX，再让 LLM 从
        这组胜者中选出最终 ROUND2_MAX 人并回置信度；否则直接用第一层结果。
    产出 {engineer_id: score} 供精排。任一轮 LLM 失败仅跳过该批/该组，不阻断。
    """

    BATCH_SIZE = 8        # 分批时的每批人数
    SINGLE_ROUND_MAX = 12  # 候选人数 ≤ 此值时：单轮一次性全量评估（不分批）
    ROUND1_TOP_K = 3       # 分批时每批初选保留 top-K
    ROUND2_MAX = 6         # 第二层决选人数上限（超过才触发第二轮）

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()

    async def _llm_score_batch(
        self, ticket: TicketContext, engineers: List[EngineerProfile], top_k: int,
    ) -> Dict[str, float]:
        """对一组工程师调 LLM，返回该组 top_k 的 {id: score}。失败返回 {}。"""
        if not engineers:
            return {}
        prompt = self._build_prompt(ticket, engineers, top_k=top_k)
        try:
            from ai.core import get_llm_client
            llm = await get_llm_client()
            response = await llm.complete(prompt, max_tokens=1200, temperature=0.3)
            return self._parse(response, engineers)
        except Exception as e:
            logger.warning(f"[派单:{ticket.id}] Step3-L1 LLM召回失败: {e}")
            return {}

    async def arecall(
        self, ticket: TicketContext, engineers: List[EngineerProfile],
    ) -> Dict[str, float]:
        """按候选人数量自适应：人少单轮全量评估；人多分批初选 + 合并决选。

        返回 {engineer_id: score} 给精排。任一轮 LLM 失败仅跳过该批/该组，不阻断。
        """
        if not engineers:
            return {}

        n = len(engineers)

        # ── 候选人数少：单轮一次性全量评估，不分批 ──
        if n <= self.SINGLE_ROUND_MAX:
            scores = await self._llm_score_batch(
                ticket, engineers, top_k=n,  # 要求评估全部 n 位
            )
            logger.debug(f"[派单:{ticket.id}] Step3-L1 单轮全量评估 人数={n} 输出={len(scores)}人")
            return scores

        # ── 候选人数多：分批初选 + 合并决选 ──
        stage1: Dict[str, float] = {}
        batches = [
            engineers[i:i + self.BATCH_SIZE]
            for i in range(0, n, self.BATCH_SIZE)
        ]
        logger.info(
            f"[派单:{ticket.id}] Step3-L1 分批初选 总人数={n} 分{len(batches)}批 每批取Top{self.ROUND1_TOP_K}"
        )
        for bi, batch in enumerate(batches, 1):
            scores = await self._llm_score_batch(ticket, batch, top_k=self.ROUND1_TOP_K)
            top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[: self.ROUND1_TOP_K]
            stage1.update(dict(top))
            top_names = [
                f"{next((e.name for e in batch if e.id == eid), eid[:8])}:{sc:.2f}"
                for eid, sc in top
            ]
            logger.debug(
                f"[派单:{ticket.id}] Step3-L1   批次{bi}/{len(batches)} 人数={len(batch)} "
                f"命中={len(top)}人 [{', '.join(top_names)}]"
            )
        logger.debug(f"[派单:{ticket.id}] Step3-L1 分批初选汇总: {n}→{len(stage1)} 人")

        winners = [e for e in engineers if e.id in stage1]
        if not winners:
            logger.warning(f"[派单:{ticket.id}] Step3-L1 分批初选无胜者，返回空")
            return {}

        # 第二层决选（胜者数仍偏多才触发）
        if len(winners) <= self.ROUND2_MAX:
            logger.debug(
                f"[派单:{ticket.id}] Step3-L1 胜者{len(winners)}≤ROUND2_MAX，不再决选返回"
            )
            # 用工程师 id（字符串）作 key；注意 winners 是 EngineerProfile 对象列表，
            # 绝不能把对象本身当 dict key（unhashable → TypeError）。
            return {w.id: stage1[w.id] for w in winners if w.id in stage1}
        final = await self._llm_score_batch(ticket, winners, top_k=self.ROUND2_MAX)
        logger.info(
            f"[派单:{ticket.id}] Step3-L1 合并决选 胜者={len(winners)}人 → 决选出={len(final)}人"
        )
        return final

    def _build_prompt(self, ticket, engineers, top_k: int = 5):
        all_flag = top_k >= len(engineers)
        intro = (
            "你是派单专家。请评估每一位候选工程师与工单的匹配度（0~1），"
            f"并为全部 {len(engineers)} 位给出分数。"
            if all_flag
            else (
                "你是派单专家。请从下面的候选工程师中，选出最符合的 "
                f"Top {top_k}（排名不分先后），并为每人给出匹配度（0~1）。"
            )
        )
        lines = [
            intro,
            "综合考虑：责任模块是否对口、职责描述是否匹配、过往经验是否相关。",
            "",
            "【工单】",
            f"标题: {ticket.title or '无'}",
            f"描述: {ticket.problem_description}",
        ]
        if ticket.robot_type:
            lines.append(f"车型: {ticket.robot_type}")
        if ticket.fault_code:
            lines.append(f"故障码: {ticket.fault_code}")

        lines.extend(["", "【候选工程师】"])
        for e in engineers:
            dep = f"({e.department})" if e.department else ""
            lines.append(f"候选ID: {e.id} | L{e.job_level} | {dep}")
            lines.append(f"   产品:{e.modules_display()}")
            duty = (e.duty_text or "")[:120]
            if duty:
                lines.append(f"   职责:{duty}")

        lines.extend([
            "",
            ("必须且只能从上面的候选工程师中评估全部候选人并给出分数。"
             if all_flag
             else f"必须且只能从上面的候选工程师中选出 {top_k} 位。"),
            "输出 JSON。engineer_id 必须是候选人列表中该人选对应的 ID（「候选ID」字段，即 users.id），必须精确复制，不要填姓名或自造标识。confidence 填 0~1 的浮点数。",
            '{"rankings":[{"engineer_id":"<精确复制候选ID>","confidence":0.85},...]}',
        ])
        return "\n".join(lines)

    def _parse(self, response: str, engineers: List[EngineerProfile]) -> Dict[str, float]:
        m = re.search(r"\{.*\}", response, re.DOTALL)
        if not m:
            logger.debug(f"Step3-L1 LLM 返回无 JSON，raw: {response[:200]}")
            return {}
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.debug(f"Step3-L1 JSON 解析失败，raw: {response[:300]}")
            return {}

        rankings = data.get("rankings", [])
        if not isinstance(rankings, list) or not rankings:
            logger.debug(f"Step3-L1 rankings 为空或非列表: {rankings}")
            return {}

        id_map = {e.id: e for e in engineers}
        scores = {}
        not_found = []
        for r in rankings:
            eid = r.get("engineer_id", "").strip()
            conf = float(r.get("confidence", 0.0))
            if eid in id_map and conf > 0:
                scores[eid] = min(conf, 1.0)
            else:
                not_found.append(f"{eid}(conf={conf})")
        if not_found:
            logger.debug(f"Step3-L1 ID 未匹配 {len(not_found)}: {not_found[:5]}")
        return scores
