"""retrieval — 检索/输出/规则域：检索结果格式化、方案解析、触发规则。"""
from ai.agents.AiTaskPlatform.retrieval.solution_io import (
    parse_solution_with_status,
)
from ai.agents.AiTaskPlatform.retrieval.retrieval_utils import format_retrieval_results
from ai.agents.AiTaskPlatform.retrieval import rules

__all__ = [
    "parse_solution_with_status",
    "format_retrieval_results", "rules",
]
