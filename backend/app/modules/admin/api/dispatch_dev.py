"""开发者模式：代理 AI 派单调试接口（簇 / 历史索引）。"""
from typing import Any, Dict

import httpx
from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.modules.admin.api.auth import require_permission
from app.modules.admin.schemas.response import DataResponse
from app.services.identity_service import IdentityService

PERM = "frontend:admin:dispatch-dev:show"
router = APIRouter(prefix="/dispatch-dev", tags=["admin-dispatch-dev"])


def ensure_dispatch_dev_permission() -> None:
    """权限管理里能勾到这一项；已存在则跳过。"""
    IdentityService.add_permission(
        permission_id="perm_frontend_admin_dispatch-dev_show",
        code=PERM,
        name="显示开发者模式",
        resource_type="frontend",
        action="show",
        description="后台「其他」中显示派单开发者模式（看簇 / 重建簇 / 补索引）",
    )


def _ai_url(path: str) -> str:
    return f"{settings.AI_SERVICE_URL.rstrip('/')}{path}"


async def _proxy(method: str, path: str, timeout: float) -> dict:
    url = _ai_url(path)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI 服务响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300] if e.response is not None else str(e)
        raise HTTPException(status_code=502, detail=f"AI 服务异常({url}): {detail}")
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接 AI 服务({url})，请确认 ai/run.py 已启动（默认 8401）: {e}",
        )
    if payload.get("code") not in (0, None):
        raise HTTPException(status_code=502, detail=payload.get("detail") or "AI 服务返回异常")
    return payload.get("data", payload)


@router.get("/overview", response_model=DataResponse, summary="派单开发者概览")
async def overview(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy("GET", "/api/ai/assigner/debug/overview", timeout=30.0)
    return DataResponse(code=0, message="success", data=data)


@router.post("/clusters/rebuild", response_model=DataResponse, summary="重建问题簇")
async def rebuild_clusters(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy("POST", "/api/ai/assigner/debug/clusters/rebuild", timeout=180.0)
    return DataResponse(code=0, message="success", data=data)


@router.post("/history/reindex", response_model=DataResponse, summary="一键补索引")
async def reindex_history(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy("POST", "/api/ai/assigner/debug/history/reindex", timeout=600.0)
    return DataResponse(code=0, message="success", data=data)
