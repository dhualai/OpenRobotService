"""CodeSkill 检索器 — 语义搜索 + 调用图展开"""

from typing import List, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.code_skill.indexer import CodeIndexer
from ai.agents.AiTaskPlatform.code_skill.schemas import CodeSearchResult, FunctionRef

logger = get_logger("TASK_AGENT")


class CodeRetriever:
    """代码检索：语义召回(embedding) + 关键词召回 融合 → 调用图展开

    检索策略（按可用性分级）：
      1. 语义召回（语义索引就绪时启）：query → embedding → 余弦相似度，
         对「上轨」这类中文领域词/口语化表达召回远好于 substring 匹配。
      2. 关键词召回（IDF 加权）：函数名/注释/路径 substring 命中，作为
         精确词兜底与语义召回融合（如函数名含 query 术语时）。
      3. 调用图展开：对 top 结果沿 calls/called_by 展开上下游。
    """

    def __init__(self, indexer: CodeIndexer):
        self._indexer = indexer

    async def search(self, query: str, top_k: int = 5) -> CodeSearchResult:
        """主入口：语义 + 关键词融合召回，再展开调用图"""
        result = CodeSearchResult(query=query)

        # 候选池：(FunctionRef, 语义分, 关键词IDF分)
        pool: List[tuple] = []
        key_of = {}   # fingerprint-key → FunctionRef（去重用）

        # ── 1. 语义召回（embedding）──
        semantic_ready = self._indexer.semantic is not None and self._indexer.semantic.is_ready
        if semantic_ready:
            try:
                hits = await self._indexer.semantic.search(query, top_k=max(top_k, 8))
                for score, fp in hits:
                    m = self._match_function(fp)
                    if m is None:
                        continue
                    key = (m.name, m.file_path, m.line_start)
                    if key not in key_of:
                        key_of[key] = m
                        pool.append((m, float(score), 0.0))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[search] 语义召回失败，回退关键词: {e}")
                semantic_ready = False

        # ── 2. 关键词召回（IDF 加权），与语义分融合 ──
        keywords = _extract_code_keywords(query)
        kw_freq: dict = {}
        for kw in keywords:
            kw_freq[kw] = len(self._indexer.search_by_keyword(kw))
        idf_score: dict = {}
        for kw in keywords:
            n = max(kw_freq.get(kw, 1), 1)
            for m in self._indexer.search_by_keyword(kw):
                key = (m.name, m.file_path, m.line_start)
                idf_score[key] = idf_score.get(key, 0.0) + (1.0 / n)
                if key not in key_of:
                    key_of[key] = m
                    pool.append((m, 0.0, 0.0))

        # 兜底：语义+关键词都空 → 按文件路径名搜整句
        if not pool:
            for m in self._indexer.search_by_keyword(query):
                key = (m.name, m.file_path, m.line_start)
                if key not in key_of:
                    key_of[key] = m
                    pool.append((m, 0.0, 0.0))

        # ── 3. 融合排序：语义分 + 关键词IDF分（两者归一化后加权）──
        #   - 语义分（余弦相似度）通常 0.35~0.75，关键词 IDF 分 0~若干。
        #   - 取「若有语义分则语义主导，关键词作精确词兜底」的折中：
        #     综合分 = max(语义分, 0) * W_sem + min(idf, 1.0) * W_kw
        def _combine(m, s, kw):
            w_sem = 1.0
            w_kw = 0.4 if s > 0 else 1.0   # 无语义分时关键词分主导
            return s * w_sem + min(kw, 1.0) * w_kw

        ranked = sorted(
            pool,
            key=lambda it: _combine(it[0], it[1], idf_score.get((it[0].name, it[0].file_path, it[0].line_start), 0.0)),
            reverse=True,
        )[:top_k]
        result.matches = [it[0] for it in ranked]

        # 4. 调用图展开 Top 3
        for m in result.matches[:3]:
            up, down = self._indexer.expand_call_graph(m)
            for u in up:
                if u not in result.upstream and u.name != m.name:
                    result.upstream.append(u)
            for d in down:
                if d not in result.downstream and d.name != m.name:
                    result.downstream.append(d)

        return result

    def _match_function(self, fp: dict) -> Optional[FunctionRef]:
        """按语义指纹 (name/file_path/line_start) 在索引里定位 FunctionRef。"""
        name = fp.get("name") or ""
        fpath = fp.get("file_path") or ""
        line = int(fp.get("line_start") or 0)
        for i in self._indexer._name_index.get(name, []):
            f = self._indexer._functions[i]
            if f.file_path == fpath and f.line_start == line:
                return f
        return None


# ── 关键词提取 ──

_CODE_SIGNAL_WORDS = [
    "diagnose", "discuss", "summarize", "submit", "upload", "analyze",
    "task", "ticket", "comment", "status", "assign",
    "MAPF", "path", "plan", "route", "robot",
    "diagnost", "retrieve", "index", "search", "query",
    "update", "create", "delete", "patch", "get",
    "chat", "stream", "SSE", "sse",
    "log", "parse", "extract", "build", "index",
    "image", "vision", "minio", "upload", "download",
    "worker", "service", "pipeline", "agent", "platform",
]


# 中文单字虚词/语气词——作为术语的一部分时仍有实义（如「上轨」的「上」、
# 「下料」的「下」），故**只**过滤纯虚词单字组合，避免误伤技术词。
_CJK_STOP_CHARS = set("的了和与或在是不是被把让据对从这那呢吗吧啊么就都及或并于各该按若则且及")


def _extract_code_keywords(query: str) -> List[str]:
    """从用户问题中提取代码相关关键词

    优先命中英文信号词；否则对纯中文 query 做「中文二字切分(bi-gram)」，
    让「查找一下代码中上轨的逻辑」能切出「上轨」「轨迹」「逻辑」等可检索单元，
    避免把整句拼音/整句当作一个 substring 去匹配（否则「上轨」永远搜不到）。
    """
    found = []
    q_lower = query.lower()
    for w in _CODE_SIGNAL_WORDS:
        if w.lower() in q_lower:
            found.append(w)
    # 没有命中英文信号词 → 尝试中文二字切分
    if not found:
        found = _extract_cjk_bigrams(query)
    # 仍无命中 → 用原始 query 的前几个词
    if not found:
        words = query.split()
        found = [w for w in words[:5] if len(w) > 2]
    return found[:8]


def _extract_cjk_bigrams(text: str) -> List[str]:
    """提取 query 中的中文二字词（bi-gram 滑窗）作为检索关键词。

    规则：
      - 对连续 CJK 字符段做长度为 2 的滑窗；要求窗口内至少有一个非虚词汉字
        （虚词单字如「的/了/和」不参与，纯虚词组合丢弃），避免「了和」「的从」等废话；
      - 纯实义词组合（如「上轨」「代码」「路径」）排在含虚词的组合前面，
        优先作为检索词；
      - 保底：若窗口段只有一个汉字（如「轨」），也返回该单字便于子串命中。
    """
    def is_cjk(ch: str) -> bool:
        o = ord(ch)
        return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF)

    grams: List[str] = []
    # 按连续 CJK 段切分
    segments: List[str] = []
    buf = []
    for ch in text:
        if is_cjk(ch):
            buf.append(ch)
        else:
            if buf:
                segments.append("".join(buf))
                buf = []
    if buf:
        segments.append("".join(buf))

    solid: List[str] = []          # 不含虚词单字的实义组合（优先）
    loose: List[str] = []          # 含虚词单字的组合（靠后）
    for seg in segments:
        if len(seg) >= 2:
            for i in range(len(seg) - 1):
                gram = seg[i:i + 2]
                g0, g1 = gram[0], gram[1]
                # 两个都是虚词单字 → 纯废话字组，丢弃（如「的了」「和与」）
                if g0 in _CJK_STOP_CHARS and g1 in _CJK_STOP_CHARS:
                    continue
                target = solid if (g0 not in _CJK_STOP_CHARS and g1 not in _CJK_STOP_CHARS) else loose
                if gram not in target:
                    target.append(gram)
        elif len(seg) == 1 and seg not in _CJK_STOP_CHARS:
            if seg not in solid:
                solid.append(seg)
    return (solid + loose)[:8]
