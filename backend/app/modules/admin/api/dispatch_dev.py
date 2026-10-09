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
        description="后台「其他」中显示派单开发者模式（派单调试 / 界面图鉴 / 可达环境 / 长期记忆）",
    )


def _ai_url(path: str) -> str:
    return f"{settings.AI_SERVICE_URL.rstrip('/')}{path}"


async def _proxy(method: str, path: str, timeout: float, json: dict | None = None) -> dict:
    url = _ai_url(path)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=json)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI 服务响应超时，请稍后重试")
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else 502
        detail = e.response.text[:500] if e.response is not None else str(e)
        try:
            body = e.response.json()
            raw = body.get("detail")
            if isinstance(raw, str) and raw.strip():
                detail = raw.strip()
        except Exception:
            pass
        # 校验失败原样还给前端，不要包装成 502
        if status in (400, 404, 409, 422):
            raise HTTPException(status_code=status, detail=detail)
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


@router.post("/reassign-stats", response_model=DataResponse, summary="统计历史转派")
async def reassign_stats(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """按转派弹窗三个固定类型汇总指标。"""
    ensure_dispatch_dev_permission()
    data = await _proxy("POST", "/api/ai/assigner/debug/reassign-stats", timeout=600.0)
    return DataResponse(code=0, message="success", data=data)


@router.post("/reassign-review", response_model=DataResponse, summary="审核未标转派")
async def reassign_review(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy(
        "POST", "/api/ai/assigner/debug/reassign-review", timeout=30.0, json=payload or {},
    )
    return DataResponse(code=0, message="success", data=data)


@router.post("/clusters/params", response_model=DataResponse, summary="保存簇门槛并重建")
async def save_cluster_params(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy(
        "POST", "/api/ai/assigner/debug/clusters/params", timeout=180.0, json=payload or {},
    )
    return DataResponse(code=0, message="success", data=data)


@router.get("/test-branches", response_model=DataResponse, summary="派单测试分支树")
async def test_branches(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """返回派单所有分支 + 当前缓存的测试覆盖状态（秒级返回，不跑 pytest）。"""
    ensure_dispatch_dev_permission()
    data = await _proxy("GET", "/api/ai/assigner/debug/test-branches", timeout=30.0)
    return DataResponse(code=0, message="success", data=data)


@router.post("/test-branches/refresh", response_model=DataResponse, summary="手动刷新测试覆盖状态")
async def test_branches_refresh(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """手动触发 pytest 全量跑一次，结果写回缓存（5 分钟 TTL）。

    性能说明：跑一次大约 30~120 秒；前端要等接口返回才能继续。
    """
    ensure_dispatch_dev_permission()
    data = await _proxy(
        "POST", "/api/ai/assigner/debug/test-branches/refresh", timeout=180.0,
    )
    return DataResponse(code=0, message="success", data=data)


@router.post("/test-run", response_model=DataResponse, summary="模拟提单跑派单")
async def test_run(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission(PERM),
):
    """模拟提单信息，跑一次完整派单，返回结果 + 命中分支。"""
    ensure_dispatch_dev_permission()
    data = await _proxy(
        "POST", "/api/ai/assigner/debug/test-run", timeout=120.0, json=payload or {},
    )
    return DataResponse(code=0, message="success", data=data)


@router.get("/memories", response_model=DataResponse, summary="列出 U老师 长期记忆")
async def list_memories(
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy("GET", "/api/ai/task/memory", timeout=20.0)
    return DataResponse(code=0, message="success", data=data)


@router.put("/memories/{mem_id}", response_model=DataResponse, summary="改写一条长期记忆")
async def update_memory(
    mem_id: str,
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy(
        "PUT", f"/api/ai/task/memory/{mem_id}", timeout=30.0, json=payload or {},
    )
    return DataResponse(code=0, message="success", data=data)


@router.delete("/memories/{mem_id}", response_model=DataResponse, summary="删除一条长期记忆")
async def delete_memory(
    mem_id: str,
    current_user: Dict[str, Any] = require_permission(PERM),
):
    ensure_dispatch_dev_permission()
    data = await _proxy("DELETE", f"/api/ai/task/memory/{mem_id}", timeout=20.0)
    return DataResponse(code=0, message="success", data=data)
