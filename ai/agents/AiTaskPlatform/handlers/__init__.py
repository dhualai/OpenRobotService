"""AiTaskAgent 功能流程 Mixin 集合（pipeline 拆分出的功能块）。

- SolutionFlow: analyze / analyze_stream / submit
- DiagnoseFlow: diagnose
- DiscussFlow:  discuss
- SummarizeFlow: summarize_batch / _summarize_one
"""
from ai.agents.AiTaskPlatform.handlers.solution_flow import SolutionFlow
from ai.agents.AiTaskPlatform.handlers.diagnose_flow import DiagnoseFlow
from ai.agents.AiTaskPlatform.handlers.discuss_flow import DiscussFlow
from ai.agents.AiTaskPlatform.handlers.summarize_flow import SummarizeFlow

__all__ = ["SolutionFlow", "DiagnoseFlow", "DiscussFlow", "SummarizeFlow"]
