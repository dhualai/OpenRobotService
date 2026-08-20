"""Assigner（智能派单）配置热更新 API。

供后端（admin 责任模块树维护）在写回 module_tree 后调用，
让运行中的派单流水线（DispatchFlow）重新加载配置、失效召回缓存。
"""
from fastapi import APIRouter
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER_API")

assigner_router = APIRouter(prefix="/api/ai/assigner", tags=["Assigner配置"])


@assigner_router.post("/reload")
async def reload_config():
    """重新加载 Assigner 配置 + 失效召回缓存（模块树改完后调用）。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner import ensure_dispatch_ready
        flow = ensure_dispatch_ready()
        flow.reload_config()
        logger.info("Assigner 配置已热更新（module_tree 变更后加载）")
        return {"status": "ok", "message": "assigner 配置已刷新"}
    except Exception as e:
        logger.exception("Assigner 配置热更新失败: %s", e)
        return {"status": "error", "message": f"热更新失败: {e}"}
