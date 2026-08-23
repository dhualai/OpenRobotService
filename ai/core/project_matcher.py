"""
项目名称匹配器：模糊匹配用户输入 → helpdesk_724.project 标准项目名

用法:
    matcher = ProjectMatcher()
    await matcher.ensure_loaded()
    result = matcher.match("多摩川")
    # → ProjectMatch(name="江苏常州多摩川混场项目", code="13", score=0.9)
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import httpx
from sqlalchemy import text

from ai.config import get_ai_config

logger = logging.getLogger(__name__)

# ── 项目列表缓存 ──────────────────────────────────────────
_CACHE_TTL = 3600  # 1 小时，项目列表变动不频繁
_MEILI_INDEX = "projects"  # Meilisearch 索引名
_MEILI_TIMEOUT = 2.0  # Meili HTTP 超时（秒）


@dataclass
class ProjectMatch:
    name: str
    code: str
    score: float


class ProjectMatcher:
    """项目名称匹配器（内存缓存）"""

    def __init__(self):
        self._projects: List[Dict] = []  # [{"name": ..., "code": ...}, ...]
        self._loaded_at: float = 0.0
        self._lock = asyncio.Lock()
        self._meili_ok: bool = False  # Meili 同步成功标记
        self._client: Optional[httpx.AsyncClient] = None

    # ── 加载 ──────────────────────────────────────────────

    async def ensure_loaded(self) -> bool:
        """确保项目列表已加载（过期自动刷新）。返回 True 表示有数据可用。"""
        now = time.time()
        if self._projects and (now - self._loaded_at) < _CACHE_TTL:
            return True

        async with self._lock:
            # 双重检查
            if self._projects and (now - self._loaded_at) < _CACHE_TTL:
                return True
            try:
                self._projects = await self._fetch_projects()
                self._loaded_at = time.time()
                logger.info(
                    f"[ProjectMatcher] loaded {len(self._projects)} projects from DB"
                )
                # 同步到 Meilisearch（失败不阻塞——内存降级可用）
                self._meili_ok = await self._sync_to_meili()
            except Exception as e:
                logger.warning(f"[ProjectMatcher] failed to load projects: {e}")
                # 不清空旧缓存，下次继续用
        return len(self._projects) > 0

    async def _fetch_projects(self) -> List[Dict]:
        """从数据库查询所有项目名称和编号"""
        from .database import SessionLocal
        loop = asyncio.get_running_loop()

        def _query():
            session = SessionLocal()
            try:
                # 跨库查询：明确指定 helpdesk_724.project
                rows = session.execute(
                    text("SELECT name, code FROM helpdesk_724.project ORDER BY name")
                ).fetchall()
                return [{"name": r[0], "code": r[1]} for r in rows if r[0]]
            finally:
                session.close()

        result = await loop.run_in_executor(None, _query)
        # 每次查库都打印完整列表，方便确认拉到的项目匹配库内容
        _sample = [f"{p['code']}:{p['name']}" for p in result]
        logger.info(f"[ProjectMatcher] fetched {len(result)} projects: {_sample}")
        return result

    # ── 匹配 ──────────────────────────────────────────────

    def match(self, user_input: str, min_score: float = 0.5) -> Optional[ProjectMatch]:
        """匹配用户输入 → 最佳项目。返回 None 表示无匹配（用原始输入）。"""
        user = user_input.strip()
        if not user or not self._projects:
            return None

        candidates = self._get_candidates(user)
        if not candidates:
            return None

        best = candidates[0]

        if best.score >= min_score:
            # 如果第一名和第二名分数很接近（<0.05），记录 warning 但不拦截
            if len(candidates) > 1 and (best.score - candidates[1].score) < 0.05:
                logger.info(
                    f"[ProjectMatcher] ambiguous: '{user}' -> "
                    f"'{best.name}' ({best.score:.2f}) vs "
                    f"'{candidates[1].name}' ({candidates[1].score:.2f})"
                )
            logger.info(
                f"[ProjectMatcher] matched: '{user}' -> '{best.name}' "
                f"(score={best.score:.2f})"
            )
            return best

        logger.info(f"[ProjectMatcher] no match for '{user}' (best={best.score:.2f})")
        return None

    def get_candidates(
        self, user_input: str, min_score: float = 0.7, top_n: int = 5
    ) -> List[ProjectMatch]:
        """返回所有 ≥ min_score 的候选（按得分降序），供 LLM 二次裁决。"""
        user = user_input.strip()
        if not user or not self._projects:
            return []
        candidates = self._get_candidates(user)
        return [c for c in candidates if c.score >= min_score][:top_n]

    async def get_candidates_async(
        self, user_input: str, min_score: float = 0.7, top_n: int = 5
    ) -> List[ProjectMatch]:
        """异步查询：Meilisearch 做召回，内存 _score() 做统一打分。

        Meilisearch 的 _rankingScore 和内存 bigram/Jaccard 打分量纲不同，
        但共用同一个 min_score 阈值 → 同一 query 在 Meili 在线/离线时结果不同。
        现在统一用 _score() 打分，Meili 仅负责快速召回候选。
        """
        candidates: List[ProjectMatch] = []
        # Meilisearch 召回（快速全文本搜索）
        if self._meili_ok:
            try:
                hits = await self._meili_search(user_input, top_n * 3)
                if hits is not None:
                    # 用统一的 _score() 重新打分，不再信任 Meili 的 _rankingScore
                    for h in hits:
                        s = self._score(user_input, h["name"])
                        if s is not None:
                            candidates.append(ProjectMatch(name=h["name"], code=h["code"], score=s))
                    logger.info(
                        f"[ProjectMatcher] meili recall: '{user_input}' -> "
                        f"{len(hits)} hits, {len(candidates)} scored>0"
                    )
            except Exception as e:
                logger.warning(f"[ProjectMatcher] meili search failed, fallback to memory: {e}")
        # 内存打分（Meili 不可用或没召回结果时兜底）
        if not candidates:
            candidates = self._get_candidates(user_input)
        # 统一按 _score() 打分排序
        candidates.sort(key=lambda x: x.score, reverse=True)
        result = [c for c in candidates if c.score >= min_score][:top_n]
        return result

    async def _meili_search(self, query: str, limit: int = 5) -> Optional[List[dict]]:
        """调 Meilisearch 搜索。失败返回 None（不抛异常）。"""
        if not self._client:
            return None
        config = get_ai_config()
        headers = {"Content-Type": "application/json"}
        if config.meili_master_key:
            headers["Authorization"] = f"Bearer {config.meili_master_key}"
        try:
            resp = await self._client.post(
                f"{config.meili_host_url}/indexes/{_MEILI_INDEX}/search",
                json={
                    "q": query,
                    "limit": limit,
                    "attributesToRetrieve": ["name", "code"],
                    "showRankingScore": True,
                },
                headers=headers,
                timeout=_MEILI_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("hits", [])
            logger.warning(
                f"[ProjectMatcher] meili search HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return None
        except Exception as e:
            logger.warning(f"[ProjectMatcher] meili search error: {e}")
            return None

    async def _sync_to_meili(self) -> bool:
        """将内存项目列表全量同步到 Meilisearch projects 索引。返回是否同步成功。"""
        config = get_ai_config()
        if not config.meili_enabled:
            return False
        if not self._projects:
            return False
        try:
            if not self._client:
                self._client = httpx.AsyncClient(timeout=_MEILI_TIMEOUT)
            headers = {"Content-Type": "application/json"}
            if config.meili_master_key:
                headers["Authorization"] = f"Bearer {config.meili_master_key}"
            host = config.meili_host_url

            # 1. 确保索引存在（不存在则创建，已存在 PUT 忽略）
            await self._client.put(
                f"{host}/indexes/{_MEILI_INDEX}",
                json={"uid": _MEILI_INDEX, "primaryKey": "id"},
                headers=headers,
            )
            # 2. 设置 searchableAttributes（code 也搜得到）
            await self._client.patch(
                f"{host}/indexes/{_MEILI_INDEX}/settings",
                json={"searchableAttributes": ["name", "code"]},
                headers=headers,
            )
            # 3. 全量替换文档
            docs = [
                {"id": i, "name": p["name"], "code": p["code"]}
                for i, p in enumerate(self._projects)
            ]
            await self._client.delete(
                f"{host}/indexes/{_MEILI_INDEX}/documents",
                headers=headers,
            )
            resp = await self._client.put(
                f"{host}/indexes/{_MEILI_INDEX}/documents",
                json=docs,
                headers=headers,
            )
            ok = resp.status_code in (200, 201, 202)
            if ok:
                logger.info(
                    f"[ProjectMatcher] synced {len(docs)} projects to meilisearch "
                    f"({host}/indexes/{_MEILI_INDEX})"
                )
                return True
            logger.warning(
                f"[ProjectMatcher] meili sync HTTP {resp.status_code}: {resp.text[:200]}"
            )
            return False
        except Exception as e:
            logger.warning(f"[ProjectMatcher] meili sync failed: {e}")
            return False

    def _get_candidates(self, user: str) -> List[ProjectMatch]:
        """内部：计算所有候选并降序排列"""
        candidates: List[ProjectMatch] = []
        for proj in self._projects:
            name = proj["name"]
            score = self._score(user, name)
            if score is not None:
                candidates.append(ProjectMatch(name=name, code=proj["code"], score=score))
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates
        return None

    def _score(self, user: str, project_name: str) -> Optional[float]:
        """计算匹配得分，返回 None 表示不匹配"""
        u = user.lower().strip()
        p = project_name.lower().strip()

        # 1. 精确匹配
        if u == p:
            return 1.0

        # 2. 用户输入是项目名的子串（如 "多摩川" ⊂ "江苏常州多摩川混场项目"）
        if u in p:
            # 越长的子串匹配越可靠
            ratio = len(u) / len(p)
            return 0.9 if ratio >= 0.3 else 0.75

        # 3. 项目名是用户输入的子串
        if p in u:
            return 0.85

        # 4. 关键词重叠
        return self._keyword_score(u, p)

    # 标点字符表（bypass Python 3.14 regex strictness）
    _PUNCT_CHARS = frozenset(
        ' -*/\\.,，。、；：！？（）()[]【】""''\t\n\r'
        '           '
        '​‌‍‎‏  ‪‫‬'
        '‭‮  　﻿'
        '《》〈〉「」『』【】〔〕〖〗〘〙〚〛'
    )

    @classmethod
    def _strip_punct(cls, s: str) -> str:
        return ''.join(ch for ch in s if ch not in cls._PUNCT_CHARS)

    def _keyword_score(self, u: str, p: str) -> Optional[float]:
        """基于 bigram 的关键词匹配"""
        def bigrams(s: str) -> set:
            cleaned = self._strip_punct(s)
            return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}

        u_bg = bigrams(u)
        p_bg = bigrams(p)

        if not u_bg or not p_bg:
            return None

        intersection = u_bg & p_bg
        if not intersection:
            return None

        # Jaccard 相似度
        jaccard = len(intersection) / len(u_bg | p_bg)

        # 单字关键词命中
        single_chars = set(self._strip_punct(u))
        p_chars = set(self._strip_punct(p))
        char_hit = len(single_chars & p_chars) / max(len(single_chars), 1)

        # 综合得分：bigram Jaccard 权重 0.7 + 单字命中 权重 0.3
        score = 0.7 * jaccard + 0.3 * char_hit

        if score < 0.3:
            return None

        # 映射到合理区间
        return 0.5 + 0.3 * min(score, 1.0)  # 0.5 ~ 0.8


# ── 全局单例 ──────────────────────────────────────────────
_matcher: Optional[ProjectMatcher] = None


def get_project_matcher() -> ProjectMatcher:
    global _matcher
    if _matcher is None:
        _matcher = ProjectMatcher()
    return _matcher
