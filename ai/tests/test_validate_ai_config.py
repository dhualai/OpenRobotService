"""validate_ai_config 连通检查硬超时（1015 部署失败排查）。

事故：deepseek 官方崩时，lifespan 的连通检查发真实 LLM 请求（complete 内层
3 次重试 × 30s 读超时 ≈ 95s+），把 FastAPI 启动拖过部署健康检查窗口（原
60s）→ 部署失败。修复：三项检查各包 _HEALTHCHECK_TIMEOUT 硬超时 + 并发执行，
检查只为报状态，不允许拖启动。
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import ai.config as ai_config  # noqa: E402
from ai.config import validate_ai_config  # noqa: E402


def _patch_deps(deepseek_sleep: float, deepseek_ok: bool = False):
    """mock 三项依赖：deepseek 可控慢/好，qdrant/redis 恒好。"""
    async def _slow_complete(*a, **kw):
        await asyncio.sleep(deepseek_sleep)
        if not deepseek_ok:
            raise RuntimeError("simulated deepseek down")
        return "ok"

    llm = AsyncMock()
    llm.complete = _slow_complete
    qdrant = AsyncMock()
    qdrant.list_collections = AsyncMock(return_value=["c1"])
    mem = AsyncMock()
    mem.health_check = AsyncMock(return_value=True)
    return [
        patch.object(ai_config, "_HEALTHCHECK_TIMEOUT", 0.3),
        patch("ai.core.get_llm_client", AsyncMock(return_value=llm)),
        patch("ai.core.QdrantClientWrapper.from_config", AsyncMock(return_value=qdrant)),
        patch("ai.core.get_memory_manager", AsyncMock(return_value=mem)),
    ]


async def test_deepseek_hang_does_not_block_startup():
    """deepseek 挂起（长睡）：整体在硬超时内返回，deepseek 报超时错误，
    qdrant/redis 不受牵连——启动不再被单点拖穿健康检查窗口。"""
    patches = _patch_deps(deepseek_sleep=30)
    for p in patches:
        p.start()
    try:
        t0 = asyncio.get_event_loop().time()
        results = await validate_ai_config()
        dt = asyncio.get_event_loop().time() - t0
    finally:
        for p in patches:
            p.stop()
    assert dt < 3, f"连通检查总耗时 {dt:.1f}s，必须被硬超时兜住"
    assert results["deepseek"]["status"] == "error"
    assert "超时" in results["deepseek"]["message"]
    assert results["qdrant"]["status"] == "ok"
    assert results["redis"]["status"] == "ok"


async def test_all_healthy_reports_ok():
    """三项全好：全部 ok（回归：并发化不破坏正常路径）"""
    patches = _patch_deps(deepseek_sleep=0.01, deepseek_ok=True)
    for p in patches:
        p.start()
    try:
        results = await validate_ai_config()
    finally:
        for p in patches:
            p.stop()
    assert all(v["status"] == "ok" for v in results.values()), results
