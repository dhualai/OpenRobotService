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


def _extract_code_keywords(query: str) -> List[str]:
    """从用户问题中提取代码相关关键词"""
    found = []
    q_lower = query.lower()
    for w in _CODE_SIGNAL_WORDS:
        if w.lower() in q_lower:
            found.append(w)
    # 没有命中任何关键词 → 用原始 query 的前几个词
    if not found:
        words = query.split()
        found = [w for w in words[:5] if len(w) > 2]
    return found[:5]
