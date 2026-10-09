"""工单「问题文档」AI 生成 API。

路由（挂在 /api/tasks 下）：
- POST /problem-doc/ai-generate   会话 / 讨论内容 → 结构化问题文档（补充段正文）

设计：接口只吃「发言列表 + 项目名」，不绑定 task_id —— 提单阶段工单还不存在，
本期（AI 会话 → 文档）与后续（讨论区 → 文档）共用同一接口。

安全：入参条数与长度上限见 problem_doc_service（超出从最早发言开始丢弃）；
     需登录；只返回文本、不落库不写文件；日志不记录正文内容。
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth_routes import get_current_active_user_from_token
from app.modules.tasks.schemas.problem_doc import (
    ProblemDocGenerateRequest,
    ProblemDocGenerateResponse,
)
from app.modules.tasks.services.problem_doc_service import generate_problem_doc

router = APIRouter(tags=["task-problem-doc"])
logger = logging.getLogger(__name__)

# 非 conversation / discussion 的场景值一律回落为会话（不反射到提示词，避免提示词注入面扩大）
_ALLOWED_SCENES = {"conversation", "discussion"}


@router.post("/problem-doc/ai-generate", response_model=ProblemDocGenerateResponse)
async def ai_generate_problem_doc(
    payload: ProblemDocGenerateRequest,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """会话 / 讨论内容 → 结构化问题文档。

    只返回正文，不写库：前端先给用户预览、确认后再自行写入（提单走 overrides.spec_doc，
    已建工单走 PUT /{task_id}/spec-doc），避免 AI 结果直接覆盖人写的内容。
    """
    scene = payload.scene if payload.scene in _ALLOWED_SCENES else "conversation"
    try:
        result = await generate_problem_doc(
            [item.model_dump() for item in payload.items],
            project_name=payload.project_name or "",
            scene=scene,
        )
    except ValueError as exc:  # 没有可整理的内容等业务性错误
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:  # AI 未配置 / 调用失败
        raise HTTPException(status_code=503, detail=str(exc))
    return ProblemDocGenerateResponse(**result)
