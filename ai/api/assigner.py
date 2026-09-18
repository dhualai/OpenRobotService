"""Assigner（智能派单）配置热更新 API。

供后端在保存责任模块树 / 变更用户画像后调用，
让运行中的派单流水线重新加载模块树配置、失效召回与画像缓存。
"""
from fastapi import APIRouter, Body, HTTPException
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


def _ok(data):
    return {"code": 0, "data": data}


@assigner_router.get("/debug/overview")
async def debug_overview():
    """开发者模式：当前簇缓存 + 历史工单/索引概况。不触发向量化。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_overview as _overview
        return _ok(await _overview())
    except Exception as e:
        logger.exception("派单调试概览失败: %s", e)
        raise HTTPException(status_code=500, detail=f"概览失败: {e}")


@assigner_router.post("/debug/clusters/rebuild")
async def debug_rebuild_clusters():
    """开发者模式：清缓存并重新自动聚簇。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_rebuild_clusters as _rebuild
        return _ok(await _rebuild())
    except Exception as e:
        logger.exception("重建问题簇失败: %s", e)
        raise HTTPException(status_code=500, detail=f"重建簇失败: {e}")


@assigner_router.post("/debug/history/reindex")
async def debug_reindex_history():
    """开发者模式：把已解决/已关闭工单按四栏模板写入 Qdrant。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import debug_reindex as _reindex
        return _ok(await _reindex())
    except Exception as e:
        logger.exception("补索引失败: %s", e)
        raise HTTPException(status_code=500, detail=f"补索引失败: {e}")


@assigner_router.post("/debug/reassign-stats")
async def debug_reassign_stats():
    """开发者模式：按转派弹窗三个固定类型汇总指标（不猜、不进学习）。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_reassign_stats as _stats,
        )
        return _ok(await _stats())
    except Exception as e:
        logger.exception("转派统计失败: %s", e)
        raise HTTPException(status_code=500, detail=f"转派统计失败: {e}")


@assigner_router.post("/debug/reassign-review")
async def debug_reassign_review(payload: dict = Body(...)):
    """开发者模式：人工给未标转派点选类型。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_review_reassign as _review,
        )
        return _ok(_review(payload.get("log_id"), payload.get("kind") or ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("转派审核失败: %s", e)
        raise HTTPException(status_code=500, detail=f"转派审核失败: {e}")


@assigner_router.post("/debug/clusters/params")
async def debug_cluster_params(payload: dict = Body(...)):
    """开发者模式：保存簇门槛并按新值重建簇。"""
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.debug_views import (
            debug_save_cluster_params as _save,
        )
        return _ok(await _save(payload or {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("保存簇门槛失败: %s", e)
        raise HTTPException(status_code=500, detail=f"保存簇门槛失败: {e}")

