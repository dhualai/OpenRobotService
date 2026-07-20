"""Assigner 数据模型（工单上下文、工程师画像、派单结果）"""

from typing import Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class TicketContext(BaseModel):
    """提单 Agent 注入的工单上下文（已与提单 Agent 字段对齐）"""

    model_config = ConfigDict(extra="ignore")

    # === 核心字段（通用）===
    id: Union[str, int] = Field(..., description="工单唯一标识")
    title: str = Field(..., description="标题")
    problem_description: str = Field(..., description="问题描述")
    status: str = Field(..., description="工单状态")
    priority: Optional[str] = Field(None, description="紧急/高/中/低")
    ticket_type: Optional[str] = Field(None, description="缺陷/功能/问题/支持/其他")
    created_at: Optional[Union[str, int]] = Field(None, description="创建时间")

    # === 提单 Agent 专属字段 ===
    session_id: Optional[str] = Field(None, description="会话ID")
    source: Optional[str] = Field(None, description="来源: ai_agent / 人工 / 系统")

    # === 人员/项目信息 ===
    project_name: Optional[str] = Field(None, description="项目名称")
    creator: Optional[str] = Field(None, description="发起人")
    assignee: Optional[str] = Field(None, description="当前接单人")
    contact: Optional[str] = Field(None, description="联系人")

    # === 问题/故障专属 ===
    location: Optional[str] = Field(None, description="现场位置")
    robot_type: Optional[str] = Field(None, description="机器人类型")
    fault_code: Optional[str] = Field(None, description="故障码（Agent 诊断提取）")
    special_notes: Optional[str] = Field(None, description="特殊说明")

    # === Agent 诊断信息（可用于派单增强）===
    diagnosis_hypotheses: Optional[List[str]] = Field(None, description="Agent 推断的可能原因")
    diagnosis_ruled_out: Optional[List[str]] = Field(None, description="Agent 已排除的原因")
    diagnosis_collected_info: Optional[Dict[str, str]] = Field(None, description="Agent 收集的上下文")
    diagnosis_rounds: Optional[int] = Field(None, description="诊断轮数")

    # === Bug 专属 ===
    severity: Optional[str] = Field(None, description="严重程度: 阻塞/主要/次要/轻微")
    version: Optional[str] = Field(None, description="软件版本")
    steps_to_reproduce: Optional[str] = Field(None, description="复现步骤")
    expected_result: Optional[str] = Field(None, description="预期结果")
    actual_result: Optional[str] = Field(None, description="实际结果")

    # === Feature 专属 ===
    scenario: Optional[str] = Field(None, description="需求场景")
    expected_effect: Optional[str] = Field(None, description="预期效果")

    # === Support 专属 ===
    support_type: Optional[str] = Field(None, description="支持类型")
    preferred_response: Optional[str] = Field(None, description="期望响应方式: 电话/现场/线上")

    # === 派单辅助字段 ===
    required_skills: Optional[List[str]] = Field(None, description="所需技能（Agent/系统推断）")
    required_parts: Optional[List[str]] = Field(None, description="所需配件")
    attachments: Optional[List[str]] = Field(None, description="附件路径列表")

    # === 其他 ===
    updated_at: Optional[str] = Field(None, description="修改时间")
    planned_finish_at: Optional[str] = Field(None, description="计划完成时间")


class EngineerProfile(BaseModel):
    """工程师画像"""

    id: str = Field(..., description="工程师唯一标识")
    name: str = Field(..., description="工程师姓名")
    level: int = Field(
        default=1,
        description="工程师层级: 1=一线工程师, 2=上级/管理者",
    )
    responsibility_modules: List[str] = Field(
        default_factory=list,
        description="责任模块，如 [车端, 地图, 调度]",
    )
    skills: List[str] = Field(
        default_factory=list,
        description="技能标签，如 [潜伏车, 定位, 导航]",
    )
    duty_text: Optional[str] = Field(
        None, description="原始职责画像文本，供 LLM 参考"
    )


class AssignmentResult(BaseModel):
    """智能派单结果"""

    engineer_id: str = Field(..., description="推荐工程师 ID")
    engineer_name: str = Field(..., description="推荐工程师姓名")
    confidence_score: float = Field(..., description="置信度分数（0~1）")
    reasoning: str = Field(..., description="推荐理由，用于日志或前端展示")
    decision_type: str = Field(
        ...,
        description="决策类型: auto(直接拍板) / recommend(建议确认) / fallback(兜底派单)"
    )
