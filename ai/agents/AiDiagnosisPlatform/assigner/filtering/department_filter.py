"""部门过滤器：工单 → 部门

负责人画像按部门组织（users.department），部门过滤把工单归到对应部门，
只在该部门候选人中挑选接单人。

  机器人事业部     — 车体硬件/机械故障
  智能移动研究院   — 车端软件（传感器/算法/通信协议）
  智能规划研究院   — 调度系统 / 摇人吧服务号

分不清的不分：让多部门的人一起参与召回 + LLM 决定。

匹配策略（关键词保证确定性，embedding 做语义补漏）：
  1. strong 关键词命中 → 直接判定部门
  2. medium + weak 同文命中 → 判定部门
  3. 未命中 → embedding 匹配部门故障场景（阈值 0.65）

配置在 config.yaml 的 department_keywords（三级）/ department_scenes（场景库）。
"""

import numpy as np
from typing import Dict, List, Optional, Tuple

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER")

# 默认 embedding 语义匹配阈值（实际值从 config.yaml 的 department_filter.embed_threshold 读取）
_DEFAULT_EMBED_THRESHOLD = 0.65

# ── 场景 embedding 缓存 ──
_scene_cache = {
    "scene_embeddings": {},    # {(部门, 场景): embedding}
    "scene_hash": "",          # 场景库变更检测
}


def _cos(u, v):
    a = np.asarray(u); b = np.asarray(v)
    dot = np.dot(a, b); na = np.linalg.norm(a); nb = np.linalg.norm(b)
    return float(dot / (na * nb)) if na > 0 and nb > 0 else 0.0


class DepartmentFilter:

    def __init__(self, config: Optional[AssignerConfig] = None):
        self._config = config or AssignerConfig()
        # 部门 → {strong: [...], medium: [...], weak: [...]}（从 config.yaml 加载）
        self._dept_keywords: Dict[str, dict] = self._config.department_keywords or {}
        # 部门 → {场景: 描述}（embedding 语义补漏用）
        self._dept_scenes: Dict[str, dict] = self._config.department_scenes or {}
        # 部门过滤参数（从 config.yaml 的 department_filter 读取）
        df_cfg = self._config.department_filter or {}
        self._embed_threshold = float(df_cfg.get("embed_threshold", _DEFAULT_EMBED_THRESHOLD))

    # ── 三级关键词匹配 ──
    def _strong_match(self, text: str) -> List[Tuple[str, List[str]]]:
        """strong 关键词匹配：返回 [(部门, 命中词), ...]"""
        hits = []
        for dept, levels in self._dept_keywords.items():
            strong = levels.get("strong") or []
            kw_hits = [kw for kw in strong if kw.lower() in text]
            if kw_hits:
                hits.append((dept, kw_hits))
        return hits

    def _medium_weak_match(self, text: str) -> List[Tuple[str, List[str]]]:
        """medium + weak 同文命中：返回 [(部门, 命中词), ...]"""
        hits = []
        for dept, levels in self._dept_keywords.items():
            medium = levels.get("medium") or []
            weak = levels.get("weak") or []
            med_hits = [kw for kw in medium if kw.lower() in text]
            weak_hits = [kw for kw in weak if kw.lower() in text]
            # medium 命中 且 同文命中 weak（表示确实是故障类）→ 可判定
            if med_hits and weak_hits:
                hits.append((dept, med_hits + weak_hits))
        return hits

    def _medium_only_depts(self, text: str) -> set:
        """找出单独命中 medium（无 weak 组合判定）的部门集合。

        作用：为 embedding 缩小候选范围——medium 命中说明领域明确，
        让 embedding 只在该部门场景内匹配，避免误匹配到其他部门。
        """
        depts = set()
        for dept, levels in self._dept_keywords.items():
            medium = levels.get("medium") or []
            weak = levels.get("weak") or []
            med_hits = [kw for kw in medium if kw.lower() in text]
            weak_hits = [kw for kw in weak if kw.lower() in text]
            # medium 命中 但 无 weak（无法组合判定）→ 仅缩小 embedding 范围
            if med_hits and not weak_hits:
                depts.add(dept)
        return depts

    # ── embedding 语义匹配 ──
    async def _ensure_scene_cache(self):
        """预计算部门场景 embedding（场景库变更时重算）"""
        global _scene_cache
        scenes = self._dept_scenes or {}
        if not scenes:
            return

        import hashlib, json
        h = hashlib.md5(json.dumps(scenes, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if _scene_cache["scene_hash"] == h and _scene_cache["scene_embeddings"]:
            return

        from ai.core import get_embed_client
        ec = await get_embed_client()

        flat = []  # [(dept, scene, text)]
        for dept, scene_map in scenes.items():
            for scene, desc in scene_map.items():
                flat.append((dept, scene, desc))
        embs = await ec.embed_batch([t for _, _, t in flat])
        _scene_cache["scene_embeddings"] = {
            (dept, scene): (emb.tolist() if isinstance(emb, np.ndarray) else emb)
            for (dept, scene, _), emb in zip(flat, embs)
        }
        _scene_cache["scene_hash"] = h

    async def _embedding_match(
        self, text: str, candidate_depts: Optional[set] = None,
    ) -> Tuple[str, str, float]:
        """embedding 匹配：返回 (部门, 场景, 最高相似度)。

        candidate_depts: 限定候选部门集合（medium 单独命中时缩小范围），
                         为 None 时全部门场景匹配。
        """
        await self._ensure_scene_cache()
        if not _scene_cache["scene_embeddings"] or not text:
            return "", "", 0.0

        from ai.core import get_embed_client
        ec = await get_embed_client()
        qe = (await ec.embed(text)).tolist()

        best_dept, best_scene, best_score = "", "", 0.0
        for (dept, scene), memb in _scene_cache["scene_embeddings"].items():
            if candidate_depts is not None and dept not in candidate_depts:
                continue
            s = _cos(qe, memb)
            if s > best_score:
                best_dept, best_scene, best_score = dept, scene, s
        return best_dept, best_scene, best_score

    # ── 主匹配入口 ──
    async def match_department(self, ticket: TicketContext) -> str:
        text = " ".join(filter(None, [
            ticket.title, ticket.problem_description,
        ])).lower()

        # ── 第一优先级：strong 命中 → 直接判定 ──
        strong_hits = self._strong_match(text)
        if len(strong_hits) == 1:
            dept, kws = strong_hits[0]
            logger.info(f"[派单:{ticket.id}] Step1-部门匹配 strong({kws[:3]}) → {dept}")
            return dept
        if len(strong_hits) >= 2:
            details = ", ".join(f"{d}={k[:2]}" for d, k in strong_hits)
            logger.info(f"[派单:{ticket.id}] Step1-部门匹配 strong 跨部门歧义 → 不过滤 ({details})")
            return ""

        # ── 第二优先级：medium + weak 组合判定 ──
        medium_hits = self._medium_weak_match(text)
        if len(medium_hits) == 1:
            dept, kws = medium_hits[0]
            logger.info(f"[派单:{ticket.id}] Step1-部门匹配 medium+weak({kws[:3]}) → {dept}")
            return dept
        if len(medium_hits) >= 2:
            details = ", ".join(f"{d}={k[:2]}" for d, k in medium_hits)
            logger.info(f"[派单:{ticket.id}] Step1-部门匹配 medium+weak 跨部门歧义 → 不过滤 ({details})")
            return ""

        # ── 第三优先级：embedding 语义补漏 ──
        # medium 单独命中 → 缩小 embedding 候选部门范围（领域明确但是否故障不确定）
        candidate_depts = self._medium_only_depts(text) or None
        dept, scene, score = await self._embedding_match(text, candidate_depts)
        if dept and score >= self._embed_threshold:
            scope = "medium缩小" if candidate_depts else "全场景"
            logger.info(f"[派单:{ticket.id}] Step1-部门匹配 embedding[{scope}]({scene}:{score:.2f}) → {dept}")
            return dept

        logger.info(f"[派单:{ticket.id}] Step1-部门匹配 未命中(关键词+embedding) → 不过滤")
        return ""

    def filter_by_department(self, engineers, department, ticket=None):
        if not department:
            return list(engineers)
        filtered = [e for e in engineers if e.department == department]
        tag = f"[派单:{ticket.id}]" if ticket is not None else "[dept_filter]"
        logger.info(f"{tag} Step1-部门过滤: {len(engineers)}→{len(filtered)} ({department})")
        return filtered

    async def filter(self, ticket, engineers, project_name=""):
        if not engineers:
            return []
        dept = await self.match_department(ticket)
        if dept:
            return self.filter_by_department(engineers, dept, ticket)
        return list(engineers)
