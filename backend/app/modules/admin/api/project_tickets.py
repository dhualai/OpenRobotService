"""项目工单卡 API（项目详情页）—— 工单概览 + 「配置阻滞权重」AI 入口。

路由前缀 /project-tickets，挂载到 admin_router 后实际路径为
/api/admin/project-tickets/projects/{project_id}/...：

- GET  /projects/{project_id}/overview         工单概览（状态计数 / 近 8 周新建趋势 / 核心阻滞工单）
- POST /projects/{project_id}/blocking-config  配置阻滞权重（仅管理员及超级管理员）

读接口沿用 admin 只读接口现状（路由级登录管控）；写接口——配置阻滞权重要调大模型
并覆盖全项目的展示口径，强制管理员身份（与 /info-nodes/template 同一判据）。

错误约定：ValueError → 400（提示词为空/项目无工单等业务性错误）；
LookupError → 404（项目不存在）；RuntimeError → 503（AI 未配置 / 调用失败 / 输出无法解析）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Dict, Optional

from app.core.database import get_async_db as get_db
from app.modules.admin.api.permissions import get_current_admin_user
from app.modules.admin.services.project_tickets_service import project_tickets_service

project_tickets_router = APIRouter(prefix="/project-tickets", tags=["admin-project-tickets"])


class BlockingConfigRequest(BaseModel):
    prompt: str = Field(..., description="阻滞判定提示词（筛选/权重说明，由管理员输入）")


@project_tickets_router.get("/projects/{project_id}/overview", response_model=Dict[str, Any],
                            summary="项目工单概览（状态计数 + 周趋势 + 核心阻滞）")
async def get_project_tickets_overview(
    project_id: str,
    db: AsyncSession = Depends(get_db),
):
    """项目详情页「项目工单」卡的数据源。

    响应结构：
    {
        "code": 0,
        "data": {
            "total": 10,
            "by_status": {"new": 1, "in_progress": 2, ...},   # 前端状态 key 口径（与仪表盘一致）
            "pending_count": 3, "overdue_count": 1, "resolved_rate": 0.5,
            "weekly": [{"week_start": "2026-07-27", "count": 2}, ...],   # 近 8 周（周一为起点）
            "blocking": {
                "mode": "ai" | "default",
                "tickets": [工单条目...],
                "prompt": "…", "summary": "…", "reasons": {"12": "…"},
                "updated_by_name": "…", "updated_at": "…"
            }
        }
    }
    """
    data = await project_tickets_service.get_overview(db, project_id)
    return {"code": 0, "data": data}


@project_tickets_router.post("/projects/{project_id}/blocking-config", response_model=Dict[str, Any],
                             summary="配置阻滞权重（AI 判定核心阻滞工单，仅管理员）")
async def configure_project_blocking(
    project_id: str,
    payload: BlockingConfigRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Dict[str, Any] = Depends(get_current_admin_user),
):
    """把项目基础字段 + 该项目工单基础数据 + 管理员提示词交给大模型，判定核心阻滞工单。

    判定结果落 project_blocking_config（每项目一行，重新配置即覆盖），
    响应返回更新后的 blocking 板块（与 overview 内结构一致，前端直接替换展示）。
    """
    try:
        blocking = await project_tickets_service.configure_blocking(
            db,
            project_id,
            payload.prompt,
            operator=current_user.get("username"),
            operator_name=current_user.get("name"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"code": 0, "data": blocking}
