"""call 我的任务 API（请求方视角查看自己的工单）。

MIGRATION.md 阶段 3：从 fqa/ticket 提取"我的工单"视角，
提供请求方查看自己创建或参与的任务列表。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from app.core.database import get_async_db as get_db
from app.modules.tasks.schemas.ticket import (
    TicketListResponse, TicketQueryParams, TicketResponse
)
from app.modules.tasks.models.ticket import TicketStatus, TicketPriority, TicketType
from app.modules.tasks.services.ticket_service import TicketService

router = APIRouter(prefix="/my-tasks", tags=["call-my-tasks"])


@router.get("/", response_model=TicketListResponse, summary="获取我的任务列表")
async def get_my_tasks(
    request: Request,
    page: int = Query(1, ge=1, description="页码"),
    size: int = Query(10, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="任务状态"),
    priority: Optional[str] = Query(None, description="任务优先级"),
    db: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    
    if not token:
        raise HTTPException(status_code=401, detail="未授权")
    
    from app.modules.admin.utils_das.security import decode_token
    decoded = decode_token(token)
    if not decoded:
        raise HTTPException(status_code=401, detail="无效的token")
    
    username = decoded.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="无法获取用户信息")
    
    priority_enum = TicketPriority(priority) if priority else None
    
    query_params = TicketQueryParams(
        page=page,
        size=size,
        status=status,
        priority=priority_enum,
        created_by=username,
    )
    
    result = await TicketService.get_tickets(db, query_params, token)
    return result


@router.get("/{task_id}", response_model=TicketResponse, summary="获取我的任务详情")
async def get_my_task(
    request: Request,
    task_id: int,
    load_comments: bool = Query(False, description="是否加载评论"),
    db: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    
    if not token:
        raise HTTPException(status_code=401, detail="未授权")
    
    ticket = await TicketService.get_ticket_by_id(db, task_id, load_comments, token)
    if not ticket:
        raise HTTPException(status_code=404, detail="任务未找到")
    
    from app.modules.admin.utils_das.security import decode_token
    decoded = decode_token(token)
    username = decoded.get("sub")
    
    if username not in [ticket.created_by, ticket.assigned_to, ticket.customer]:
        raise HTTPException(status_code=403, detail="无权限查看此任务")
    
    return ticket


@router.post("/", response_model=TicketResponse, summary="创建任务（报障提单）")
async def create_my_task(
    request: Request,
    ticket_data,
    db: AsyncSession = Depends(get_db)
):
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    
    if not token:
        raise HTTPException(status_code=401, detail="未授权")
    
    from app.modules.admin.utils_das.security import decode_token
    decoded = decode_token(token)
    username = decoded.get("sub")
    
    try:
        from app.modules.tasks.schemas.ticket import TicketCreate
        ticket_create = TicketCreate(**ticket_data)
        comment_attachment_map = {}
        ticket = await TicketService.create_ticket(db, ticket_create, username, comment_attachment_map, token)
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建任务失败: {str(e)}")