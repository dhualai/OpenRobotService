"""Assigner（智能派单）配置热更新 API。

供后端在保存责任模块树 / 变更用户画像后调用，
让运行中的派单流水线重新加载模块树配置、失效召回与画像缓存。
"""
from fastapi import APIRouter, HTTPException
from ai.core.logging import get_logger

logger = get_logger("ASSIGNER_API")

assigner_router = APIRouter(prefix="/api/ai/assigner", tags=["Assigner配置"])


@assigner_router.post("/reload")
async def reload_config():
    """热更新派单配置：重载模块树 + 失效画像缓存。
    - flow.reload_config()：从 DB 重载模块树（module_tree / classify / keywords / anchors）
      并失效召回缓存；
    - invalidate_personnel_cache()：置空工程师画像缓存，下次派单懒加载时重拉最新画像。
    失败返回 500（而非 200），供后端 _notify_ai_reload 正确感知热更新是否成功。
    """
    try:
        from ai.agents.AiDiagnosisPlatform.assigner import (
            ensure_dispatch_ready,
            invalidate_personnel_cache,
        )
        flow = ensure_dispatch_ready()
        flow.reload_config()
        invalidate_personnel_cache()
        logger.info("Assigner 模块树配置与工程师画像已热更新")
        return {"status": "ok", "message": "assigner 模块树与画像已刷新"}
    except Exception as e:
        logger.exception("Assigner 配置热更新失败: %s", e)
        raise HTTPException(status_code=500, detail=f"热更新失败: {e}")
