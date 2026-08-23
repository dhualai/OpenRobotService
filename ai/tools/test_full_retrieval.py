"""全 KB 检索覆盖测试 — 验证每个子目录的内容都能检索到"""
import sys, asyncio
from pathlib import Path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))
from dotenv import load_dotenv
load_dotenv(_project_root / "ai" / ".env")

async def main():
    from ai.core.retrieval import get_retrieval_service
    from qdrant_client import QdrantClient
    from ai.config import get_ai_config, get_active_collection_for

    cfg = get_ai_config()
    team_col = get_active_collection_for("team")

    # 连接 Qdrant
    local = Path(cfg.qdrant_local_path)
    if not local.is_absolute():
        local = _project_root / local
    client = QdrantClient(path=str(local))

    print("=" * 60)
    print(f"team 活跃集合: {team_col}")
    print("=" * 60)

    # ── 统计各 sub_domain 的 chunk 分布 ──
    sub_domains = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=team_col,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        for p in points:
            sd = p.payload.get("sub_domain", "")
            if sd not in sub_domains:
                sub_domains[sd] = []
            sub_domains[sd].append(p)

    print(f"\n各 sub_domain chunk 分布:\n")
    for sd, pts in sorted(sub_domains.items()):
        print(f"  [{sd:35s}] {len(pts):4d} chunks")
    print(f"\n  合计: {sum(len(v) for v in sub_domains.values())} chunks")

    # ── 检索测试 ──
    test_cases = [
        # (query, expected_sub_domain, description)
        ("VDA5050 MQTT 机器人适配", "usp/overview", "机器人适配层"),
        ("流程模板 FlowInstance 实例化", "usp/overview", "流程编排"),
        ("AGV 仿真导航模拟", "usp/overview", "仿真平台"),
        ("数据指标 故障率 AGV利用率", "usp/overview", "数据分析平台"),
        ("库位 载具 仓储管理 StorageLocation", "usp/overview", "仓储管理平台"),
        ("License JWT 激活系统ID", "usp/overview", "许可证管理"),
        ("Core ORM TableManager 枚举", "usp/overview", "核心共享库"),
        ("地图预处理 导入 MapPlatform", "usp/overview", "地图平台"),
        ("异常事件 ExceptionEvent 监控平台", "usp/overview", "监控平台"),
        ("设备外设 电梯 自动门 DevicePlatform", "usp/overview", "设备平台"),
        ("地图编辑器 MapEditor 路网", "usp/overview", "地图编辑器"),
        ("车辆离线排查 连接不上", "usp/diagnosis", "诊断-车辆运行异常"),
        ("充电失败 充电桩", "usp/diagnosis", "诊断-充电问题"),
        ("取放货 识别失败", "usp/diagnosis", "诊断-取放货"),
        ("任务路径规划失败", "usp/diagnosis", "诊断-任务路径"),
        ("地图定位异常", "usp/diagnosis", "诊断-地图定位"),
        ("USB 机器人 FAQ 常见问题", "usp/faq", "FAQ"),
        ("自研车 配置 问题", "usp/faq", "自研车FAQ"),
        ("USP 产品手册 操作指南", "usp/manual", "产品手册"),
        ("VDA5050 协议 state order", "usp/manual", "产品手册vda5050"),
        ("机器人搬运", "translation", "翻译"),
    ]

    print("\n" + "=" * 60)
    print("检索测试 (retrieve_domain)")
    print("=" * 60)

    retriever = await get_retrieval_service()
    ok, fail = 0, 0

    # 先测试：不用 sub_domain 过滤，直接搜 team domain
    for query, expected_sd, desc in test_cases:
        # 不用 sub_domain 过滤，看 top-3 结果
        results = await retriever.retrieve_domain(
            query=query,
            domain="team",
            top_k=3,
            sub_domain=None,
        )

        top_sd = results[0].sub_domain if results else "NO_RESULT"
        top_text = results[0].content[:80].replace('\n', ' ') if results else "N/A"

        # 检查是否命中期望 sub_domain
        matched = False
        for r in results[:3]:
            if expected_sd in (r.sub_domain or ""):
                matched = True
                break

        status = "[OK]" if matched else "[??]"
        if matched:
            ok += 1
        else:
            fail += 1

        print(f"  {status} {desc}")
        print(f"       查询: {query[:60]}")
        print(f"       期望 sd: {expected_sd}  实际 top sd: {top_sd}")
        print(f"       内容: {top_text}...")
        if not matched:
            print(f"       [DEBUG] top-3 sub_domains: {[r.sub_domain for r in results[:3]]}")
        print()

    print("=" * 60)
    print(f"结果: {ok}/{ok+fail} 通过, {fail} 未命中")
    print("=" * 60)

    # ── 带 sub_domain filter 的精确验证 ──
    print("\n精确 payload filter 验证 (sub_domain):")
    for sd in sorted(sub_domains.keys()):
        results = await retriever.retrieve_domain(
            query="test",
            domain="team",
            top_k=1,
            sub_domain=sd,
        )
        if results:
            title = results[0].title[:60]
            print(f"  [OK] {sd}: {title}")
        else:
            print(f"  [FAIL] {sd}: filter 返回空!")

    client.close()
    print("\nDone.")

if __name__ == "__main__":
    asyncio.run(main())
