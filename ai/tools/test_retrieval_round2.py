"""Round 2 retrieval test — shortened cross-references. Compare to Round 1 (72%)."""
import sys, io, os, asyncio
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add project root
_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, 'ai', '.env'))

from ai.core.retrieval import get_retrieval_service

# Map expected module to Chinese title keywords
EXPECTED_TITLES = {
    "task_manager": "任务调度",
    "dynamic_map": "动态地图引擎",
    "decentralised_path_planning": "路径规划",
    "ai_map": "AI 地图生成",
    "data_structure": "算法核心数据",
    "algorithm_services": "算法服务层",
    "core": "核心共享库",
    "robot_adapter": "机器人适配层",
    "taskflow": "任务流程编排",
    "map_editor": "地图编辑器",
    "monitor_platform": "监控平台",
    "simulator": "仿真平台",
    "storage_platform": "存储平台",
}


async def main():
    service = await get_retrieval_service()

    QUERIES = [
        # 1-3: task_manager
        ("task_manager", "机器人不动了，明明有任务但就是不执行"),
        ("task_manager", "有几台车一直在充电，不干活"),
        ("task_manager", "有几台车特别忙，另外的一直闲着"),
        # 4-5: dynamic_map
        ("dynamic_map", "地图更新后机器人原地转圈找不到路"),
        ("dynamic_map", "机器人上报的位置和实际位置差很远"),
        # 6-7: dpp
        ("decentralised_path_planning", "机器人走着走着突然停了，报找不到路径"),
        ("decentralised_path_planning", "机器人在取货点对不准，反复尝试"),
        # 8-9: ai_map
        ("ai_map", "生成的地图有些路是断的，机器人过不去"),
        ("ai_map", "CAD图纸转出来的地图路网不全，有些地方没路"),
        # 10: data_structure
        ("data_structure", "两台机器人在路口堵住了谁都不走"),
        # 11: algorithm_services
        ("algorithm_services", "算法服务启动就报错，服务起不来"),
        # 12: core
        ("core", "系统升级后启动报错，提示找不到表"),
        # 13: robot_adapter
        ("robot_adapter", "MQTT连接失败，机器人连不上"),
        # 14: taskflow
        ("taskflow", "任务流程下发了但机器人不执行"),
        # 15: map_editor
        ("map_editor", "地图编辑器保存失败，一直转圈"),
        # 16: monitor_platform
        ("monitor_platform", "监控页面数据不更新了"),
        # 17: simulator
        ("simulator", "仿真车在线但不响应指令"),
        # 18: storage_platform
        ("storage_platform", "库位数据改了但机器人还在用旧配置"),
    ]

    total = len(QUERIES)
    hits = 0
    misses = []

    for expected, query in QUERIES:
        results = await service.retrieve_domain(query, domain='team', top_k=5)
        expected_title = EXPECTED_TITLES.get(expected, expected)
        hit = False
        for r in results:
            if expected_title in (r.title or ''):
                hit = True
                break
        status = '✅' if hit else '❌'
        if hit:
            hits += 1
        else:
            misses.append((expected, expected_title, query))
        top_titles = ' | '.join([f"#{i+1} {r.title or '?'}" for i, r in enumerate(results[:3])])
        print(f"{status} [{expected}] \"{query}\"")
        print(f"   Top-3: {top_titles}")
        print()

    print(f"\n{'='*60}")
    print(f"命中率: {hits}/{total} = {hits/total:.0%}")
    print(f"Round 1 基线: 13/18 = 72%")
    if misses:
        print(f"\n未命中 ({len(misses)}):")
        for exp, cn, q in misses:
            print(f"  ❌ [{exp}] 期望「{cn}」 ← \"{q}\"")


if __name__ == '__main__':
    asyncio.run(main())
