"""任务 Agent 运行时（进程内）：本轮强插邮箱等短生命周期状态。"""

from ai.agents.AiTaskPlatform.runtime.inject_mailbox import drain, format_block, put

__all__ = ["put", "drain", "format_block"]
