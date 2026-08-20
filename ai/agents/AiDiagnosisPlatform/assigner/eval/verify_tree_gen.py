"""验证「产品→界面→功能」树自动生成链路（不修改生产 config）。

用最小骨架树走完整链路：_build_from_tree → AssignerConfig 各属性，
确认生成的三配置可供下游(module_router / semantic_recall)使用。
"""
import sys, os
sys.path.insert(0, os.path.abspath('.'))

MINI_TREE = {
    "调度USP": {
        "interfaces": [
            {
                "key": "task",
                "name": "任务管理",
                "functions": [
                    {"key": "task_dispatch", "name": "任务下发",
                     "keywords": ["任务下发", "任务分配", "派工"],
                     "anchor": "任务下发与分配、派工、任务连续下发"},
                    {"key": "task_template", "name": "任务模板",
                     "keywords": ["任务模版", "任务模板"],
                     "anchor": "任务模板创建与复用"},
                    {"key": "task_simulator", "name": "任务模拟器",
                     "keywords": ["模拟器", "仿真", "任务模拟器"],
                     "anchor": "任务模拟器下发、仿真模拟、机器人适配"},
                ],
            },
            {
                "key": "map",
                "name": "地图管理",
                "functions": [
                    {"key": "map_edit", "name": "地图编辑",
                     "keywords": ["地图编辑", "地图导入导出"],
                     "anchor": "地图编辑与导入导出"},
                ],
            },
        ],
    },
}

def main():
    from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig

    classify, keywords, anchors = AssignerConfig._build_from_tree(MINI_TREE)
    print("=== 生成的 module_classify ===")
    for p, m in classify.items():
        print(f"  {p}: {m}")
    print("\n=== 生成的 module_keywords ===")
    for k, v in keywords.items():
        print(f"  {k}: {v}")
    print("\n=== 生成的 module_anchor_texts ===")
    for k, v in anchors.items():
        print(f"  {k}: {v}")

    print("\n=== 断言 ===")
    assert classify["调度USP"]["task_simulator"] == "task", "模拟器应归到 task 界面"
    assert "调度USP-task" in keywords, "task 界面关键词应生成"
    assert "模拟器" in keywords["调度USP-task"], "task 界面应含模拟器关键词"
    assert "调度USP-map" in anchors, "map 界面锚应生成"
    print("全部断言通过 ✓")

if __name__ == "__main__":
    main()
