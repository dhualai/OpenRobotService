"""验证 _discovery_to_text 纯函数 + run_triage 可用性（不连 DB/LLM）。"""
import sys, asyncio
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
from dotenv import load_dotenv
load_dotenv(_ROOT / "ai" / ".env")

from ai.agents.AiTaskPlatform.handlers.diagnose_flow import _discovery_to_text


def test_format():
    print("== 1. _discovery_to_text 格式转换 ==")
    mock = {
        "module": {"name": "dynamic_map", "detected": True},
        "guide": {"routed": {"category": {"name": "algorithm"}, "selected": ["usp-services.md", "usp-algorithm-dpp.md"]}},
        "scenario": {"name": "CONSISTENCY_REPLAN", "confidence": 0.9,
                     "template_hint": "一致性超阈值→重新规划。模板: 车型+窄时间窗"},
        "signals": [{"name": "一致性超过update阈值", "count": 64},
                    {"name": "WAIT-T", "count": 41}],
        "hot_windows": [{"start": "2026-08-11 11:01", "end": "2026-08-11 11:03", "count": 87}],
        "entities": {"robots": [{"id": "XNA-169", "count": 23}, {"id": "XNA-171", "count": 5}]},
        "facts": {"time_start": "2026-08-11 11:16", "time_end": "2026-08-12 10:16"},
    }
    text = _discovery_to_text(mock)
    print(text)
    assert "Discovery" in text
    assert "XNA-169" in text
    assert "11:01" in text and "11:03" in text
    assert "CONSISTENCY_REPLAN" in text
    print("\n  ✓ 格式转换正确")

    # 空输入应返回空串
    assert _discovery_to_text({}) == ""
    assert _discovery_to_text(None) == ""
    print("  ✓ 空输入处理正确")


def test_run_triage_importable():
    print("\n== 2. run_triage 可导入 + Discover_facts 骨架 ==")
    try:
        from ai.agents.AiTaskPlatform.log_analyzer.triage import run_triage, ManualGuide
        m = ManualGuide()  # 从配置 LOG_MANUAL_DIR 解析
        print(f"  手册目录: {m.manual_dir}")
        print(f"  加载 {len(m.loaded_files)} 份")
        assert m.manual_dir, "应能解析出手册目录"
    except Exception as e:
        import traceback; traceback.print_exc()
        raise
    print("  ✓ ManualGuide 配置解析正常")


if __name__ == "__main__":
    test_format()
    test_run_triage_importable()
    print("\n验证通过 ✓")
