"""验证「产品→界面→功能」树自动生成链路（不修改生产 config）。

用最小骨架树走完整链路：_build_from_tree → AssignerConfig 各属性，
确认生成的三配置可供下游(module_router / semantic_recall)使用。

语义（2026-08-20 起）：工程师领取粒度与锚文本粒度均按**功能 name（中文）**，
- module_classify[产品][功能name] = 功能name
- module_anchor_texts[产品-功能name] = 该功能 anchor（每功能一条）
- module_keywords[产品-功能name]   = 该功能 keywords
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
    assert classify["调度USP"]["任务模拟器"] == "任务模拟器", "任务模拟器功能应自映射为功能名"
    assert "调度USP-任务下发" in keywords, "任务下发功能关键词应生成"
    assert "派工" in keywords["调度USP-任务下发"], "任务下发功能应含派工关键词"
    assert "调度USP-地图编辑" in anchors, "地图编辑功能锚应生成"
    assert anchors["调度USP-地图编辑"] == "地图编辑与导入导出", "锚应为该功能自身 anchor"
    # 语义锚 key 可直接被下游 semantic_recall 命中（工程师领取功能名时自洽）
    assert classify["调度USP"]["地图编辑"] and f"调度USP-{classify['调度USP']['地图编辑']}" in anchors
    print("全部断言通过 ✓")

if __name__ == "__main__":
    main()
