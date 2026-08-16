"""能力注册表（Capability）

设计要点（见 TASK_AGENT_TARGET_ARCH.md §6b / §6c）：
  - D12 = `BaseCapability` 抽象基类（强类型约束，子类继承即自动注册）
  - D13 = 不跨 Agent 共享，只服务 AiTaskPlatform 内部
  - D14 = 本版不上 MCP，仅预留
  - 借鉴主流框架（LangChain/CrewAI/Claude/MetaGPT）已验证的思想：
      ① 子类自动注册（CrewAI __init_subclass__）
      ② 错误态返回不中断 Agent（LangChain handle_tool_error）
      ③ 输出 schema 化（CrewAI result_schema）
      ④ name 默认取类名（MetaGPT）
      ⑤ is_available() 环境敏感能力（code_skill 服务器不可用）
  - F3 = 调度决策用 LLM 自主；F7 = TodoList 自我任务管理（本版加入，平铺）
  - Supervisor = 产品无关通用编排内核（F1/F6，LogOrchestrator 将降为领域 worker）

公开导出：
  - BaseCapability / CapabilityResult          # from ...capabilities.base
  - CapabilityRegistry / get_capability         # from ...capabilities.registry
  - TodoList / TodoItem                          # from ...capabilities.supervisor_todo
  - Supervisor / SupervisorDecision             # from ...capabilities.supervisor
  - LogAnalyzeCapability                       # from ...capabilities.log_analyze（真实能力，F1）
  - RetrieveHistoryCapability / CodeSearchCapability / ImageAnalyzeCapability  # 方案甲收敛的简单能力
  - RetrieveTroubleshootingCapability / AttachmentParseCapability               # 补齐能力
  - Evaluator                                                                    # 自评闭环 C(G4)
  - Router                                                                        # 意图路由 A(G1)
"""

from ai.agents.AiTaskPlatform.capabilities.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.registry import CapabilityRegistry, get_capability
from ai.agents.AiTaskPlatform.capabilities.supervisor_todo import TodoList, TodoItem
from ai.agents.AiTaskPlatform.capabilities.supervisor import Supervisor, SupervisorDecision
from ai.agents.AiTaskPlatform.capabilities.evaluator import Evaluator
from ai.agents.AiTaskPlatform.capabilities.router import Router
from ai.agents.AiTaskPlatform.capabilities.log_analyze import LogAnalyzeCapability
from ai.agents.AiTaskPlatform.capabilities.retrieve_history import RetrieveHistoryCapability
from ai.agents.AiTaskPlatform.capabilities.code_search import CodeSearchCapability
from ai.agents.AiTaskPlatform.capabilities.image_analyze import ImageAnalyzeCapability
from ai.agents.AiTaskPlatform.capabilities.retrieve_troubleshooting import RetrieveTroubleshootingCapability
from ai.agents.AiTaskPlatform.capabilities.attachment_parse import AttachmentParseCapability
from ai.agents.AiTaskPlatform.capabilities.ticket_ref import TicketRefCapability

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
