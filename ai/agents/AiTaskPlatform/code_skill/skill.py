"""CodeSkill 主入口 — 单例 + 懒加载索引"""

import os
import time
from pathlib import Path
from typing import Optional

from ai.config import get_ai_config
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.code_skill.indexer import CodeIndexer
from ai.agents.AiTaskPlatform.code_skill.retriever import CodeRetriever
from ai.agents.AiTaskPlatform.code_skill.schemas import CodeSearchResult

logger = get_logger("TASK_AGENT")

# 索引缓存文件（项目根目录下）
_INDEX_CACHE = Path(__file__).resolve().parent.parent.parent.parent / "code_index.json"


class CodeSkill:
    """代码检索能力单元

    Usage:
        skill = CodeSkill()
        skill.ensure_index()          # 首次调用时建索引
        result = await skill.search("MAPF是怎么被调用的")
        prompt_text = result.to_prompt_text()
    """

    def __init__(self):
        self._indexer: Optional[CodeIndexer] = None
        self._retriever: Optional[CodeRetriever] = None
        self._indexed = False

    def ensure_index(self):
        """确保索引已构建（从缓存加载或新建）"""
        if self._indexed:
            return

        cfg = get_ai_config()

        # 优先从缓存加载
        if _INDEX_CACHE.exists():
            t0 = time.perf_counter()
            self._indexer = CodeIndexer.load(str(_INDEX_CACHE))
            logger.info(f"CodeSkill: 从缓存加载 {self._indexer.function_count} 个函数 ({time.perf_counter()-t0:.1f}s)")
            self._indexed = True
            self._retriever = CodeRetriever(self._indexer)
            return

        # 新建索引
        paths = self._get_root_paths(cfg)
        self._indexer = CodeIndexer(paths).build()

        # 保存缓存
        try:
            self._indexer.save(str(_INDEX_CACHE))
        except Exception:
            pass

        self._indexed = True
        self._retriever = CodeRetriever(self._indexer)

    @staticmethod
    def _get_root_paths(cfg) -> list[str]:
        """从配置获取代码索引根目录"""
        paths_str = cfg.code_skill_paths
        if paths_str:
            paths = [p.strip() for p in paths_str.split(",") if p.strip()]
        else:
            # 默认：项目根目录下 ai/ + backend/ + frontend/
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            paths = [
                str(project_root / "ai"),
                str(project_root / "backend"),
                str(project_root / "frontend"),
            ]
        return paths

    async def search(self, query: str) -> CodeSearchResult:
        """代码检索入口"""
        if not self._indexed:
            self.ensure_index()
        return await self._retriever.search(query)


# ── 模块级单例 ──

_code_skill: Optional[CodeSkill] = None


def get_code_skill() -> CodeSkill:
    global _code_skill
    if _code_skill is None:
        _code_skill = CodeSkill()
    return _code_skill
