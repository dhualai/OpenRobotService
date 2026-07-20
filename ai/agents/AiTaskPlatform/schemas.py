"""任务 Agent 数据模型"""

from typing import Optional, List, Dict
from pydantic import BaseModel, Field


# ============================================================
# 请求模型
# ============================================================

class TaskAnalyzeRequest(BaseModel):
    """分析工单请求"""
    task_id: str = Field(..., description="工单 ID")
    session_id: str = Field(..., description="对话 session，多轮分析共享")


class TaskListRequest(BaseModel):
    """列出待处理工单请求"""
    username: str = Field(..., description="当前用户（从 token 解析）")


class TaskSubmitRequest(BaseModel):
    """方案提交请求"""
    task_id: str = Field(..., description="工单 ID")
    session_id: str = Field(..., description="对话 session")
    final_solution: "SolutionDraft" = Field(..., description="工程师编辑后的最终方案")
    resolution: str = Field(default="resolved", description="resolved | escalated | needs_review")


# ============================================================
# 核心数据模型
# ============================================================

class SolutionDraft(BaseModel):
    """解决方案草稿（LLM 生成 + 工程师可编辑）"""
    root_cause_analysis: str = Field(
        default="", description="根因分析：一句话结论 + 推理链"
    )
    suggested_actions: List[str] = Field(
        default_factory=list, description="建议步骤，优先级排序，每步具体可执行"
    )
    references: List[str] = Field(
        default_factory=list, description="参考来源（排查树节点 / 历史工单 ID）"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="置信度 (0~1)"
    )
    needs_more_info: bool = Field(
        default=False, description="是否真的需要更多信息才能出方案"
    )


class AttachmentAnalysis(BaseModel):
    """附件分析摘要"""
    has_logs: bool = False
    log_summary: str = ""
    has_replay: bool = False
    replay_summary: str = ""
    has_screenshots: bool = False


class TaskContext(BaseModel):
    """工单完整上下文（只读，从后端 REST API + diagnosis JSON 组装）

    注意：此模型不重新诊断。hypotheses/ruled_out/collected_info 来自提单 Agent。
    """
    # ── 来自 tasks 表（GET /api/tasks/{task_id}）──
    task_id: str = Field(..., description="工单 ID")
    title: str = ""
    description: str = ""
    task_type: str = ""          # problem / bug / feature / support / other
    priority: str = ""           # low / medium / high / urgent
    status: str = ""             # new / in_progress / pending / resolved / closed
    source: str = ""             # manual / zentao / ai_agent / ...
    assigned_to: Optional[str] = None
    project_name: Optional[str] = None
    attachments: List[dict] = Field(default_factory=list)
    metadata_info: Optional[dict] = None

    # ── 来自 diagnosis JSON（提单 Agent 交付）──
    problem_summary: str = ""
    hypotheses: List[str] = Field(default_factory=list)
    ruled_out: List[str] = Field(default_factory=list)
    collected_info: Dict[str, str] = Field(default_factory=dict)
    fault_code: str = ""
    robot_type: str = ""
    location: str = ""
    diagnosis_rounds: int = 0


# ============================================================
# 响应模型
# ============================================================

class TaskBrief(BaseModel):
    """工单摘要（用于列表展示）"""
    task_id: str
    title: str
    description: str = ""
    priority: str = ""
    status: str = ""
    project_name: Optional[str] = None
    created_at: Optional[str] = None
    has_attachments: bool = False


class TaskListResponse(BaseModel):
    """工单列表响应"""
    code: int = 0
    data: dict = Field(default_factory=dict)


class TaskSubmitResponse(BaseModel):
    """方案提交响应"""
    code: int = 0
    data: dict = Field(default_factory=dict)


class TaskHealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    service: str = "ai-task-agent"
