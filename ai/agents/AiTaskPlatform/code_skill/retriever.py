"""CodeSkill 检索器 — 语义搜索 + 调用图展开"""

from typing import List, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.code_skill.indexer import CodeIndexer
from ai.agents.AiTaskPlatform.code_skill.schemas import CodeSearchResult, FunctionRef

logger = get_logger("TASK_AGENT")


class CodeRetriever:
    """代码检索：语义搜索 → 调用图展开 → 返回结构化结果"""

    def __init__(self, indexer: CodeIndexer):
        self._indexer = indexer

    async def search(self, query: str, top_k: int = 5) -> CodeSearchResult:
        """主入口：语义搜索 + 调用图展开"""
        result = CodeSearchResult(query=query)

        # 1. 关键词搜索（快速命中函数名）
        keywords = _extract_code_keywords(query)
        for kw in keywords:
            matches = self._indexer.search_by_keyword(kw)
            for m in matches:
                if m not in result.matches:
                    result.matches.append(m)

        # 2. 没命中关键词 → 按文件路径名搜
        if not result.matches:
            result.matches = self._indexer.search_by_keyword(query)

        # 3. 调用图展开 Top 3 结果
        result.matches = result.matches[:top_k]
        for m in result.matches[:3]:
            up, down = self._indexer.expand_call_graph(m)
            for u in up:
                if u not in result.upstream and u.name != m.name:
                    result.upstream.append(u)
            for d in down:
                if d not in result.downstream and d.name != m.name:
                    result.downstream.append(d)

        return result


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
