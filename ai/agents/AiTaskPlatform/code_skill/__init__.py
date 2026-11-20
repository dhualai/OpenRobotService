"""CodeSkill — 代码检索能力单元

作用:
  - 扫描项目代码 → 建调用图索引
  - 语义搜索 + 沿调用图展开上下游
  - 结果注入 LLM Prompt 解读

两个入口:
  1. discuss() 关键词触发: "代码" "源码" "怎么实现" "find"
  2. LogSubAgent 兜底: 日志分析无果时自动读代码查机制

用法:
  from ai.agents.AiTaskPlatform.code_skill import CodeSkill
  skill = CodeSkill()
  result = await skill.search("MAPF是怎么被调用的")
"""

from ai.agents.AiTaskPlatform.code_skill.indexer import CodeIndexer
from ai.agents.AiTaskPlatform.code_skill.retriever import CodeRetriever
from ai.agents.AiTaskPlatform.code_skill.schemas import CodeSearchResult

__all__ = ["CodeIndexer", "CodeRetriever", "CodeSearchResult"]
