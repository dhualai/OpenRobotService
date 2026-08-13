"""Assigner 数据模型（工单上下文、工程师画像、派单结果）

字段对应关系总览（供排查/联调使用）：
- TicketContext    ↔ 后端 tasks 表（backend/app/models/task.py::Task）
- EngineerProfile  ↔ 后端 users 表（backend/app/models/identity.py::UserDB）
- AssignmentResult ↔ 派单结果，落库时写回 tasks.assigned_to / metadata_info
"""

from typing import Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class TicketContext(BaseModel):
    """提单 Agent 注入的工单上下文（已与提单 Agent 字段对齐）

    对应后端 tasks 表字段（见 models/task.py::Task）：
    - id / title / description / status / priority / created_by / assigned_to
    - project_name / customer / source / attachments / metadata_info(JSON)
    """

    model_config = ConfigDict(extra="ignore")

    # === 核心字段（通用）===
    id: Union[str, int] = Field(..., description="工单唯一标识 ↔ tasks.id")
    title: str = Field(..., description="标题 ↔ tasks.title")
    problem_description: str = Field(..., description="问题描述 ↔ tasks.description")
    status: str = Field(..., description="工单状态 ↔ tasks.status (new/in_progress/pending/resolved/closed)")
    priority: Optional[str] = Field(None, description="紧急/高/中/低 ↔ tasks.priority (urgent/high/medium/low)")
    ticket_type: Optional[str] = Field(None, description="缺陷/功能/问题/支持/其他 ↔ tasks.task_type (bug/feature/problem/support/other)")
    created_at: Optional[Union[str, int]] = Field(None, description="创建时间 ↔ tasks.created_at")

    # === 提单 Agent 专属字段 ===
    session_id: Optional[str] = Field(None, description="会话ID ↔ tasks.metadata_info.session_id")
    source: Optional[str] = Field(None, description="来源: ai_agent / 人工 / 系统 ↔ tasks.source (manual/zentao/...)")

    # === 人员/项目信息 ===
    project_name: Optional[str] = Field(None, description="项目名称 ↔ tasks.project_name")
    creator: Optional[str] = Field(None, description="发起人 ↔ tasks.created_by")
    assignee: Optional[str] = Field(None, description="当前接单人 ↔ tasks.assigned_to")
    contact: Optional[str] = Field(None, description="联系人 ↔ tasks.customer")

    # === 问题/故障专属（来源为 Agent 诊断，落库存 metadata_info）===
    location: Optional[str] = Field(None, description="现场位置 ↔ tasks.metadata_info.location")
    robot_type: Optional[str] = Field(None, description="机器人类型 ↔ tasks.metadata_info.robot_type")
    fault_code: Optional[str] = Field(None, description="故障码（Agent 诊断提取）↔ tasks.metadata_info.fault_code")
    special_notes: Optional[str] = Field(None, description="特殊说明 ↔ tasks.metadata_info.special_notes")

    # === Agent 诊断信息（可用于派单增强，落库存 metadata_info）===
    diagnosis_hypotheses: Optional[List[str]] = Field(None, description="Agent 推断的可能原因 ↔ tasks.metadata_info.diagnosis_hypotheses")
    diagnosis_ruled_out: Optional[List[str]] = Field(None, description="Agent 已排除的原因 ↔ tasks.metadata_info.diagnosis_ruled_out")
    diagnosis_collected_info: Optional[Dict[str, str]] = Field(None, description="Agent 收集的上下文 ↔ tasks.metadata_info.diagnosis_collected_info")
    diagnosis_rounds: Optional[int] = Field(None, description="诊断轮数 ↔ tasks.metadata_info.diagnosis_rounds")

    # === Bug 专属 ===
    severity: Optional[str] = Field(None, description="严重程度: 阻塞/主要/次要/轻微 ↔ tasks.metadata_info.severity")
    version: Optional[str] = Field(None, description="软件版本 ↔ tasks.metadata_info.version")
    steps_to_reproduce: Optional[str] = Field(None, description="复现步骤 ↔ tasks.metadata_info.steps_to_reproduce")
    expected_result: Optional[str] = Field(None, description="预期结果 ↔ tasks.metadata_info.expected_result")
    actual_result: Optional[str] = Field(None, description="实际结果 ↔ tasks.metadata_info.actual_result")

    # === Feature 专属 ===
    scenario: Optional[str] = Field(None, description="需求场景 ↔ tasks.metadata_info.scenario")
    expected_effect: Optional[str] = Field(None, description="预期效果 ↔ tasks.metadata_info.expected_effect")

    # === Support 专属 ===
    support_type: Optional[str] = Field(None, description="支持类型 ↔ tasks.metadata_info.support_type")
    preferred_response: Optional[str] = Field(None, description="期望响应方式: 电话/现场/线上 ↔ tasks.metadata_info.preferred_response")

    # === 派单辅助字段 ===
    required_skills: Optional[List[str]] = Field(None, description="所需技能（Agent/系统推断）↔ tasks.metadata_info.required_skills")
    required_parts: Optional[List[str]] = Field(None, description="所需配件 ↔ tasks.metadata_info.required_parts")
    attachments: Optional[List[str]] = Field(None, description="附件路径列表 ↔ tasks.attachments")

    # === 其他 ===
    updated_at: Optional[str] = Field(None, description="修改时间 ↔ tasks.updated_at")
    planned_finish_at: Optional[str] = Field(None, description="计划完成时间 ↔ tasks.deadline_at")


class EngineerProfile(BaseModel):
    """工程师画像（数据源自后端 users 表 + 公司/部门主数据表）

    对应后端字段：
    - id / name ↔ users.username / users.name
    - company / department ↔ 通过 company_id / department_id 关联主数据表取名称
    数据同步入口：assigner/sync/engineers_sync.py::load_engineers()
    """

    id: str = Field(..., description="工程师唯一标识 ↔ users.username（真实环境为 wechat_ 前缀，与 tasks.created_by/assigned_to 一致）")
    name: str = Field(..., description="工程师姓名 ↔ users.name")
    company: Optional[str] = Field(
        None, description="公司名称 ↔ users.company_id → companies.name"
    )
    department: Optional[str] = Field(
        None, description="部门/团队名称 ↔ users.department_id → departments.name"
    )
    responsibility_modules: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="按产品组织的责任模块 ↔ users.responsibility_modules(JSON)，"
                    "如 {'调度USP': ['车端','任务调度'], '摇人吧服务号': ['后端']}",
    )
    job_level: int = Field(
        default=1,
        description="职级 ↔ users.job_level，数值越高越不优先接单（1=一线, 2=管理/审核, 3=仅兜底...）",
    )
    duty_text: Optional[str] = Field(
        None, description="职责画像文本 ↔ users.duty_text，供 LLM 匹配参考"
    )

    def all_modules(self) -> List[str]:
        """返回所有产品下模块的扁平去重列表（供召回/排序使用）。"""
        seen = set()
        flat = []
        for mods in self.responsibility_modules.values():
            for m in mods:
                if m not in seen:
                    seen.add(m)
                    flat.append(m)
        return flat


class AssignmentResult(BaseModel):
    """智能派单结果

    落库对应（见 pipeline/worker.py 写回逻辑）：
    - engineer_id → tasks.assigned_to（统一为 users.username，无需反查）
    - engineer_name → 工程师姓名（users.name）
    - confidence_score / reasoning / decision_type → 建议存入 tasks.metadata_info 供日志/前端展示
    """

    engineer_id: str = Field(..., description="推荐工程师标识（users.username）→ 直接写入 tasks.assigned_to")
    engineer_name: str = Field(..., description="推荐工程师姓名（对应 users.name）")
    confidence_score: float = Field(..., description="置信度分数（0~1），建议存 tasks.metadata_info.confidence_score")
    reasoning: str = Field(..., description="推荐理由，用于日志或前端展示 → tasks.metadata_info.reasoning")
    decision_type: str = Field(
        ...,
        description="决策类型: auto(直接拍板) / recommend(建议确认) / fallback(兜底派单) → tasks.metadata_info.decision_type"
    )
