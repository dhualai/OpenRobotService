"""admin 工单状态监测 API —— 代理 AI 服务的 tickets 接口。

数据来源：AI 服务（ai/run.py @8401）的 /api/ai/memory/tickets/all，
由 admin 模块以 HTTP 代理方式接入，前端统一走 /api/admin/tickets。
"""
from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional, Dict, List, Any
import httpx
import logging

from app.core.config import settings
from app.modules.admin.utils_das.config import security, DEBUG_MODE

logger = logging.getLogger("admin.tickets")

ticket_router = APIRouter(prefix="/tickets", tags=["admin-tickets"])

# AI 服务内部地址（注意：ai/run.py 实际端口是 8401，与 settings.AI_SERVICE_URL 默认值 8010 不同；
# 生产环境请通过环境变量 AI_SERVICE_URL 覆盖为正确的值，例如 http://ai-service:8401）
AI_SERVICE_URL = settings.AI_SERVICE_URL.rstrip("/")


def _build_ai_url(path: str) -> str:
    """拼接 AI 服务完整 URL。"""
    return f"{AI_SERVICE_URL}{path}"


async def _proxy_get(path: str, params: dict) -> dict:
    """通用 GET 代理：admin → AI 服务。"""
    url = _build_ai_url(path)
    logger.info(f"代理请求 AI 服务: GET {url} params={params}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params={k: v for k, v in params.items() if v})
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        logger.error(f"AI 服务超时: {url}")
        raise HTTPException(status_code=504, detail="AI 服务响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        logger.error(f"AI 服务返回错误: {url} status={e.response.status_code}")
        raise HTTPException(status_code=502, detail=f"AI 服务异常: {e.response.status_code}")
    except httpx.RequestError as e:
        logger.error(f"无法连接 AI 服务: {url} error={e}")
        raise HTTPException(status_code=502, detail="无法连接 AI 服务，请检查服务是否正常运行")


@ticket_router.get("/", summary="工单列表（代理 AI 服务）")
async def list_tickets(
    skip: int = Query(0, ge=0, description="分页偏移"),
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    status: str = Query("", description="按状态筛选: pending / dispatched / in_progress / resolved / closed"),
    type: str = Query("", description="按类型筛选: problem / bug / feature / support / other"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    """查询所有历史工单，支持按状态/类型筛选 + 分页。

    数据直接来自 AI 服务的 MySQL tickets 表（与「我要摇人」提单 Agent 共享同一数据源）。
    """
    ai_response = await _proxy_get("/api/ai/memory/tickets/all", {
        "skip": skip,
        "limit": limit,
        "status": status,
        "type": type,
    })

    if ai_response.get("code") != 0:
        raise HTTPException(status_code=502, detail=ai_response.get("data", {}).get("error", "AI 服务返回异常"))

    data = ai_response.get("data", {})
    return {
        "code": 0,
        "data": {
            "total": data.get("total", 0),
            "skip": data.get("skip", skip),
            "limit": data.get("limit", limit),
            "items": data.get("items", []),
        }
    }


@ticket_router.get("/stats", summary="工单状态统计")
async def ticket_stats(
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None),
) -> Dict[str, Any]:
    """按状态汇总工单数量，供看板顶部的统计卡片使用。"""
    all_statuses = ["pending", "dispatched", "in_progress", "resolved", "closed"]
    stats = {}
    for s in all_statuses:
        ai_response = await _proxy_get("/api/ai/memory/tickets/all", {
            "skip": 0, "limit": 1, "status": s,
        })
        total = 0
        if ai_response.get("code") == 0:
            total = ai_response.get("data", {}).get("total", 0)
        stats[s] = total

    # 全量总数
    ai_response = await _proxy_get("/api/ai/memory/tickets/all", {"skip": 0, "limit": 1})
    total_all = ai_response.get("data", {}).get("total", 0) if ai_response.get("code") == 0 else 0

    return {
        "code": 0,
        "data": {
            "total": total_all,
            "by_status": stats,
        }
    }
