"""chart_builder 单元测试脚本（无需 LLM / 数据库）

测试内容：
  1. build_charts：分布 → 饼图/柱状图、趋势 → 折线图、单值 → 指标卡片
  2. localize_collected_data：采集结果中文 key 化（无英文字段名）
  3. sanitize_field_names：回答兜底替换，不误伤正常英文单词

运行：python ai/tests/test_chart_builder.py
"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def _load_platform_modules():
    """直接从文件加载 schemas/metric_registry/chart_builder，绕过包 __init__ 导入链。"""
    import importlib.util
    import types

    pkg_name = "ai.agents.AiDataAnalysisPlatform"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(_project_root / "ai" / "agents" / "AiDataAnalysisPlatform")]
        sys.modules[pkg_name] = pkg

    loaded = {}
    for mod_name, rel_path in [
        (f"{pkg_name}.schemas", "agents/AiDataAnalysisPlatform/schemas.py"),
        (f"{pkg_name}.metric_registry", "agents/AiDataAnalysisPlatform/metric_registry.py"),
        (f"{pkg_name}.chart_builder", "agents/AiDataAnalysisPlatform/chart_builder.py"),
    ]:
        if mod_name in sys.modules:
            loaded[mod_name.split(".")[-1]] = sys.modules[mod_name]
            continue
        spec = importlib.util.spec_from_file_location(mod_name, _project_root / "ai" / rel_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        loaded[mod_name.split(".")[-1]] = mod
    return loaded


def _sample_collected() -> dict:
    """构造与 collect_by_plan 输出结构一致的采样数据。"""
    return {
        "date_range": "2026-09-08 ~ 2026-09-14",
        "ticket": {
            "total": 120,
            "new_count": 35,
            "resolve_rate": 85.0,
            "by_status": {"新建": 10, "处理中": 20, "已解决": 90},
            "by_priority": {"低": 10, "中": 40, "高": 30, "紧急": 5, "特急": 2, "观察": 1, "待定": 3},
            "new_by_day": {"2026-09-08": 5, "2026-09-09": 3, "2026-09-10": 8},
            "overdue_list": [{"工单ID": 1, "标题": "示例工单"}],
        },
        "risk": {"total": 30, "by_level": {"高": 5, "中": 10, "低": 15}},
        "project": {"total": 8, "active_count": 3},
    }


def test_build_charts(mods) -> bool:
    print("\n" + "=" * 60)
    print("  测试 1：build_charts（图表/卡片生成）")
    print("=" * 60)
    build_charts = mods["chart_builder"].build_charts
    collected = _sample_collected()
    ok = True

    # 1a. 单值 → 卡片
    charts, cards = build_charts(
        ["ticket.total", "ticket.resolve_rate", "ticket.new_count"], collected
    )
    assert not charts, "单值指标不应产出图表"
    assert len(cards) == 3, f"期望 3 张卡片，实际 {len(cards)}"
    by_label = {c.label: c for c in cards}
    assert by_label["工单总数"].value == "120" and by_label["工单总数"].kind == "count"
    assert by_label["工单解决率"].value == "85" and by_label["工单解决率"].unit == "%"
    assert by_label["工单解决率"].kind == "metric"
    print("  [OK] 单值指标 → 卡片（计数/百分比类型区分正确）")

    # 1b. 分布 ≤6 类 → 饼图
    charts, cards = build_charts(["ticket.by_status"], collected)
    assert len(charts) == 1 and charts[0].chart_type == "pie"
    assert charts[0].title == "工单状态分布"
    assert charts[0].option["series"][0]["type"] == "pie"
    print("  [OK] 分布（3 类）→ 饼图，标题=注册表中文名")

    # 1c. 分布 >6 类 → 柱状图
    charts, cards = build_charts(["ticket.by_priority"], collected)
    assert len(charts) == 1 and charts[0].chart_type == "bar"
    assert charts[0].option["series"][0]["type"] == "bar"
    print("  [OK] 分布（7 类）→ 柱状图")

    # 1d. 趋势 → 折线图（x 轴按日期升序）
    charts, cards = build_charts(["ticket.new_by_day"], collected)
    assert len(charts) == 1 and charts[0].chart_type == "line"
    x_data = charts[0].option["xAxis"]["data"]
    assert x_data == sorted(x_data), "折线图 x 轴应按日期升序"
    print("  [OK] 趋势 → 折线图，日期升序")

    # 1e. 明细列表不配图不配卡
    charts, cards = build_charts(["ticket.overdue_list"], collected)
    assert not charts and not cards, "明细列表不应产出图表/卡片"
    print("  [OK] 明细列表（LIST）不配图不配卡")

    # 1f. 未知指标 key 跳过
    charts, cards = build_charts(["ticket.not_exist", "risk.total"], collected)
    assert len(cards) == 1 and cards[0].label == "风险总数"
    print("  [OK] 未知指标 key 跳过，已知 key 正常产出")
    return ok


def test_localize(mods) -> bool:
    print("\n" + "=" * 60)
    print("  测试 2：localize_collected_data（数据中文化）")
    print("=" * 60)
    import json

    localize = mods["chart_builder"].localize_collected_data
    localized = localize(_sample_collected())

    assert localized["统计周期"] == "2026-09-08 ~ 2026-09-14"
    assert localized["工单"]["新增数"] == 35
    assert localized["工单"]["状态分布"]["已解决"] == 90
    assert localized["工单"]["每日新增"]["2026-09-08"] == 5
    assert localized["风险"]["等级分布"]["高"] == 5
    assert localized["项目"]["活跃数"] == 3

    raw = json.dumps(localized, ensure_ascii=False)
    for en_key in ("new_count", "resolve_rate", "by_status", "new_by_day",
                   "active_count", "by_level", "date_range", "ticket", "project", "risk"):
        assert en_key not in raw, f"中文化后仍存在英文字段名 {en_key}"
    # 明细列表内的中文 key 原样保留
    assert localized["工单"]["逾期明细"][0]["工单ID"] == 1
    print("  [OK] 所有英文字段名已替换为中文，明细中文 key 原样保留")
    return True


def test_sanitize(mods) -> bool:
    print("\n" + "=" * 60)
    print("  测试 3：sanitize_field_names（回答兜底替换）")
    print("=" * 60)
    sanitize = mods["chart_builder"].sanitize_field_names

    text = "近7天 new_count 为 35 条，resolve_rate 达到 85%，by_status 显示已解决最多。"
    cleaned = sanitize(text)
    assert "new_count" not in cleaned and "新增数" in cleaned
    assert "resolve_rate" not in cleaned and "解决率" in cleaned
    assert "by_status" not in cleaned and "状态分布" in cleaned
    print("  [OK] 含下划线的字段名全部替换为中文")

    # 不误伤正常英文：单词字段（total/items/risk）不参与替换
    safe = "本次 AI 分析 total 结果如下，risk 评估 items 明细完整。"
    cleaned_safe = sanitize(safe)
    assert cleaned_safe == safe, f"正常英文被误伤: {cleaned_safe}"
    print("  [OK] 单词字段（total/items/risk）与正常英文不受影响")
    return True


def main():
    print("=" * 60)
    print("  chart_builder 单元测试（无需 LLM / 数据库）")
    print("=" * 60)
    mods = _load_platform_modules()
    ok = all([
        test_build_charts(mods),
        test_localize(mods),
        test_sanitize(mods),
    ])
    print("\n" + "=" * 60)
    print("  测试完成：" + ("全部通过" if ok else "存在失败"))
    print("=" * 60)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
