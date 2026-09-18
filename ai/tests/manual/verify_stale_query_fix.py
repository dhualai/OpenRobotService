"""复现生产 bug：diagnosing 会话中问新问题，检索词被 state.original_query 旧问题顶掉。
修复后应输出：三路域检索 query=如何使用AI绘制地图 且 5.15 进 prompt。
"""
import asyncio, io, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform as AgentCls, AgentState

    agent = AgentCls()
    await agent._ensure_clients()
    # 预热：首次调用会懒加载 embedding 模型，冷启动 2s+ 会撞检索超时（生产常驻进程无此问题）
    await agent._retriever.retrieve_domain_dual("预热", "team", top_k=1)
    # 生产同款残留状态：上一轮问库位操作高度时进入 diagnosing
    state = AgentState(
        session_id="test_stale_state",
        phase="diagnosing",
        original_query="库位改操作高度不生效怎么办？",
        problem_summary="库位改操作高度不生效",
    )
    # 修复后的调用方式：query_override 传本轮 query
    docs = await agent._retrieve_with_context(
        "test_stale_state", state,
        context_turns=[{"role": "user", "content": "库位改操作高度不生效怎么办？"},
                       {"role": "assistant", "content": "打开库位管理页面检查操作高度配置。"}],
        query_override="如何使用AI绘制地图？",
    )
    print("\n═══ 修复验证 ═══")
    hit = "5.15 AI绘制地图" in docs or "AI 地图生成" in docs or "AI绘制地图" in docs
    print(f"检索文档含 AI绘制地图 内容: {hit}")
    print(docs[:600])


asyncio.run(main())
