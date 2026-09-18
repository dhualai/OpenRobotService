"""AiTaskPlatform — 任务 Agent（供给视角 - 系统任务）

面向接单工程师的 AI 助手：基于工单已有诊断信息，检索排查树结论和历史工单方案，
生成结构化解决方案草稿，人工校准后提交完成。

快捷入口：
    from ai.agents.AiTaskPlatform import get_task_agent

    agent = await get_task_agent()
    report = await agent.diagnose("44946")
    logger.info(f"diagnose: {report.get("root_cause_analysis","")[:80]}")
"""

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

from ai.agents.AiTaskPlatform.pipeline import AiTaskAgent, get_task_agent
from ai.agents.AiTaskPlatform.log_analyzer import LogSubAgent, LogAnalysisResult
from ai.agents.AiTaskPlatform.attachments import parse_attachments
from ai.agents.AiTaskPlatform.schemas import (
    TaskAnalyzeRequest,
    TaskContext,
    SolutionDraft,
    TaskListRequest,
    TaskBrief,
    TaskListResponse,
    TaskHealthResponse,
    AttachmentAnalysis,
)

__all__ = [
    # 核心类
    "AiTaskAgent",
    "get_task_agent",
    # 请求模型
    "TaskAnalyzeRequest",
    "TaskListRequest",
    # 数据模型
    "TaskContext",
    "SolutionDraft",
    "TaskBrief",
    "AttachmentAnalysis",
    # 响应模型
    "TaskListResponse",
    "TaskHealthResponse",
]
