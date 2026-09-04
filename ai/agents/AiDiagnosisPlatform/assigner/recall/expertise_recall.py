"""B路历史召回：按「问题域聚人」→ 工程师×模块×类型 经验画像

回答："哪个工程师在这个问题域（模块×类型）上解决得多、近、对口"。

设计要点：
- 域体系：模块 × 类型（如 "调度USP-算法-故障"、"摇人吧服务号-AI-需求"）
  （模块细分名经 module_keywords 的「产品-类别」映射归一为问题域标签）
- 数据源：预热时构建一次内存缓存画像（不每次查库）
    { engineer_id: { "模块-类型": {count, last_closed_at} } }
- 打分：经验分 = 域内解决数量 × 时间新鲜度，归一化到 0-1

与 L1 分工：L1 看静态画像（"宣称擅长"），B路看实际解决记录（"做过/长期做"），互补。
"""

import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.sync.history_sync import load_history_records
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

# ── 经验画像缓存（预热时构建）──
_cache = {
    "expertise": {},   # {engineer_id: {"模块-类型": {"count":..,"last_ts":..}}}
    "hash": "",        # 历史数据变更检测
}


def _domain_key(module: str, task_type: str) -> str:
    return f"{module}-{task_type}"


def _as_ts(created_at) -> Optional[float]:
    """把 created_at 规范化成时间戳；无法解析返回 None。"""
    if not created_at:
        return None
    try:
        if isinstance(created_at, (int, float)):
            return float(created_at)
        if isinstance(created_at, str):
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            dt = created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


class ExpertiseRecall:
    """B路：按问题域（模块×类型）召回有实绩经验的工程师。"""

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        hc = self._config.history_recall or {}
        self._half_life_days = float(hc.get("half_life_days", 90))
        self._decay_weight = float(hc.get("decay_weight", 0.5))

    # ── 画像构建 ────────────────────────────────────────

    def _build_expertise(self, recs: List[dict]) -> Dict[str, dict]:
        """从历史工单构建 {engineer_id: {域key: {count, last_ts}}}。"""
        exp: Dict[str, dict] = {}
        for rec in recs:
            eid = (rec.get("engineer_id") or "").strip()
            if not eid:
                continue
            mods = rec.get("modules") or []
            ttype = (rec.get("task_type") or "problem").strip().lower() or "problem"
            ts = _as_ts(rec.get("created_at"))
            tbl = exp.setdefault(eid, {})
            for mod in mods:
                key = _domain_key(mod, ttype)
                entry = tbl.setdefault(key, {"count": 0, "last_ts": 0.0})
                entry["count"] += 1
                if ts and ts > entry["last_ts"]:
                    entry["last_ts"] = ts
        return exp

    async def _ensure_cache(self):
        """预热：构建经验画像（历史数据变更时重建）。"""
        global _cache
        recs = load_history_records(self._config.module_keywords)
        if not recs:
            return

        import hashlib, json
        h = hashlib.md5(
            json.dumps(recs, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()
        if _cache["hash"] == h and _cache["expertise"]:
            return

        _cache["expertise"] = self._build_expertise(recs)
        _cache["hash"] = h
        logger.info(f"[expertise_recall] 经验画像构建完成: {len(_cache['expertise'])} 名工程师")

    # ── 当前工单域判定 ──────────────────────────────────

    def _ticket_domains(self, ticket: TicketContext) -> List[str]:
        """判定当前工单对应的域 key 列表（模块×类型）。

        优先用标题+描述+车型+故障码做模块关键词命中；类型取 ticket.ticket_type
        或回退 problem。可能命中多个模块 → 返回多个域。
        """
        text = " ".join(filter(None, [
            ticket.title or "",
            ticket.problem_description or "",
            ticket.robot_type or "",
            ticket.fault_code or "",
        ]))
        kws = self._config.module_keywords or {}
        ttype = (ticket.ticket_type or "problem").strip().lower() or "problem"

        doms = []
        if text and kws:
            tl = text.lower()
            for mod, kw_list in kws.items():
                for kw in kw_list:
                    if kw and kw.lower() in tl:
                        doms.append(_domain_key(mod, ttype))
                        break
        # 完全没有关键词命中 → 用一个通配"未知域"，避免 B路 落空
        if not doms:
            doms = [_domain_key("*", ttype)]
        return doms

    # ── 打分 ────────────────────────────────────────────

    async def arecall(self, ticket: TicketContext) -> Dict[str, float]:
        """按问题域聚人 → {engineer_id: score}（0-1 归一化）。"""
        await self._ensure_cache()
        exp = _cache["expertise"]
        if not exp:
            return {}

        domains = self._ticket_domains(ticket)
        now = time.time()

        # 每人：在这几个域的累计经验分 = Σ(单数 × 时间新鲜度)
        raw: Dict[str, float] = {}
        for eid, tbl in exp.items():
            total = 0.0
            for dom in domains:
                entry = tbl.get(dom)
                if not entry:
                    continue
                # 时间新鲜度：基于最近一单
                freshness = 1.0
                if entry.get("last_ts"):
                    elapsed_days = max(0.0, (now - entry["last_ts"]) / 86400.0)
                    freshness = float(np.exp(-elapsed_days / self._half_life_days))
                # 经验分 = 单数 × 新鲜度；单数做对数软化，避免海量单碾压
                count = entry.get("count", 0)
                count_component = float(np.log1p(count))
                total += count_component * freshness
            if total > 0:
                raw[eid] = total

        # 归一化到 0-1（除以最大值）
        if not raw:
            logger.debug(f"[派单:{ticket.id}] Step3-L3-B 问题域: 域{domains} 无命中")
            return {}
        maxv = max(raw.values()) or 1.0
        logger.debug(
            f"[派单:{ticket.id}] Step3-L3-B 问题域: 域={domains} 聚人={len(raw)}人"
        )
        return {eid: round(v / maxv, 4) for eid, v in raw.items()}


def invalidate_expertise_cache():
    global _cache
    _cache["expertise"] = {}
    _cache["hash"] = ""
