"""能力注册表（Capability）

设计要点（见 TASK_AGENT_TARGET_ARCH.md §6b / §6c）：
  - D12 = `BaseCapability` 抽象基类（强类型约束，子类继承即自动注册）
  - D13 = 不跨 Agent 共享，只服务 AiTaskPlatform 内部
  - D14 = 本版不上 MCP，仅预留
  - 设计要点：① 子类自动注册 ② 错误态返回不中断 Agent
      ③ 输出 schema 化 ④ name 默认取类名 ⑤ is_available() 环境敏感能力（code_skill 服务器不可用）
  - F3 = 调度决策用 LLM 自主；F7 = TodoList 自我任务管理（本版加入，平铺）
  - Supervisor = 产品无关通用编排内核（F1/F6，日志多轮推理收敛为 LogSubAgent）

目录分层：
  - capabilities/core/   ← 能力基础设施（框架层，产品无关）：base / registry / supervisor / supervisor_todo / router / evaluator
  - capabilities/tools/  ← 具体能力（可被 Supervisor 调度的 worker）：log_analyze / retrieve_history / retrieve_troubleshooting / code_search / image_analyze / attachment_parse / ticket_ref

公开导出（保持外部 from ...capabilities import xxx 不变）：
  - BaseCapability / CapabilityResult          # from ...capabilities.core.base
  - CapabilityRegistry / get_capability         # from ...capabilities.core.registry
  - TodoList / TodoItem                          # from ...capabilities.core.supervisor_todo
  - Supervisor / SupervisorDecision             # from ...capabilities.core.supervisor
  - Router                                        # from ...capabilities.core.router
  - Evaluator                                     # from ...capabilities.core.evaluator
  - LogAnalyzeCapability                       # from ...capabilities.tools.log_analyze
  - RetrieveHistoryCapability / CodeSearchCapability / ImageAnalyzeCapability
  - RetrieveTroubleshootingCapability / AttachmentParseCapability / TicketRefCapability
"""

from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry, get_capability
from ai.agents.AiTaskPlatform.capabilities.core.supervisor_todo import TodoList, TodoItem
from ai.agents.AiTaskPlatform.capabilities.core.supervisor import Supervisor, SupervisorDecision
from ai.agents.AiTaskPlatform.capabilities.core.evaluator import Evaluator
from ai.agents.AiTaskPlatform.capabilities.core.router import Router
from ai.agents.AiTaskPlatform.capabilities.tools.log_analyze import LogAnalyzeCapability
from ai.agents.AiTaskPlatform.capabilities.tools.retrieve_history import RetrieveHistoryCapability
from ai.agents.AiTaskPlatform.capabilities.tools.retrieve_kb import RetrieveKbCapability
from ai.agents.AiTaskPlatform.capabilities.tools.code_search import CodeSearchCapability
from ai.agents.AiTaskPlatform.capabilities.tools.image_analyze import ImageAnalyzeCapability
from ai.agents.AiTaskPlatform.capabilities.tools.retrieve_troubleshooting import RetrieveTroubleshootingCapability
from ai.agents.AiTaskPlatform.capabilities.tools.attachment_parse import AttachmentParseCapability
from ai.agents.AiTaskPlatform.capabilities.tools.ticket_ref import TicketRefCapability

__all__ = [
    "BaseCapability",
    "CapabilityResult",
    "CapabilityRegistry",
    "get_capability",
    "TodoList",
    "TodoItem",
    "Supervisor",
    "SupervisorDecision",
    "Evaluator",
    "Router",
    "LogAnalyzeCapability",
    "RetrieveHistoryCapability",
    "CodeSearchCapability",
    "ImageAnalyzeCapability",
    "RetrieveTroubleshootingCapability",
    "AttachmentParseCapability",
    "TicketRefCapability",
]
