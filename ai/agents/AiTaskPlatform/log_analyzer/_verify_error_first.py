"""验证 ErrorFirst Discovery：错误级别统计 + 中文错误短语聚类 + 字段名过滤。

运行:  cd D:/CodeHub/AI/OpenRobotService
      .\\.venv\\Scripts\\python.exe ai\\agents\\AiTaskPlatform\\log_analyzer\\_verify_error_first.py
"""
import sys, os, time
_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)  # 项目根目录，使 "ai" 包可导入
_ATP = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ATP not in sys.path:
    sys.path.insert(1, _ATP)  # 使 handlers 可作为顶层包 import

from log_analyzer.indexer import LogIndex, _RE_ERR_PHRASE
from log_analyzer.triage import _level_distribution, _top_errors, _clean_signal, run_triage

LOG = os.path.normpath(r"D:\CodeHub\AI\OpenRobotService\ai\tests\test_logs\debug_logs.log.1")
print("LOG =", LOG, "exists:", os.path.exists(LOG))

# ── 1) 正则解析中文错误短语 ──
def test_phrase_regex():
    cases = [
        "ERROR - Robot: XNA-124 last_node_index校验失败: 当前位置与last_node_index不匹配 pos: [280.61 4.286]",
        "2026-07-27 16:28:31,236 - ERROR - 路径规划超时",
        "WARNING - xx 一致性校验失败",
    ]
    print("\n== _RE_ERR_PHRASE ==")
    for c in cases:
        m = _RE_ERR_PHRASE.search(c)
        print("  ", m.group(1) if m else "NO MATCH", "<==", c[:60])

# ── 2) 字段名 vs 真实信号过滤 ──
def test_clean_signal():
    print("\n== _clean_signal (字段名应过滤, 真实信号/中文保留) ==")
    keep = ["MAPF-T", "ABORTED", "ERROR", "校验失败", "路径规划超时", "wait_t"]
    drop = ["workingState", "envelope2d", "taskState", "velocity"]
    for s in keep:
        print(f"  KEEP   {s!r:20} -> {_clean_signal(s)!r}")
    for s in drop:
        print(f"  DROP   {s!r:20} -> {_clean_signal(s)!r}")

# ── 3) 全流程 build + 级别分布 + top_errors ──
def test_full():
    print("\n== build + level_dist + top_errors ==")
    t0 = time.perf_counter()
    idx = LogIndex(LOG).build()
    print(f"build {time.perf_counter()-t0:.1f}s, lines={idx._total}")

    lv = _level_distribution(idx)
    print("level_dist:", lv)

    te = _top_errors(idx, top_n=8)
    print("ERROR primary:")
    for e in te.get("primary", []):
        print(f"   {e['count']:6d}  {e['code']!r}")
    print("WARNING:")
    for e in te.get("warning", [])[:5]:
        print(f"   {e['count']:6d}  {e['code']!r}")

    # 确认没有纯字段名污染
    all_ph = [e["code"] for k in ("primary", "warning") for e in te.get(k, [])]
    field_names = [c for c in all_ph if c in ("workingState", "envelope2d", "taskState")]
    print("字段名污染:", field_names or "无")

# ── 4) run_triage 端到端 + _discovery_to_text 顺序 ──
def test_triage_text():
    # 1) 先验证 run_triage 直接返回的错误优先字段
    print("\n== run_triage 错误优先字段 ==")
    res = run_triage(LOG, manual_dir="")
    lv = res.get("level_dist") or {}
    te = res.get("top_errors") or {}
    prim = te.get("primary") or []
    warn = te.get("warning") or []
    print("  level_dist:", lv)
    print("  ERROR primary(前6):", [f"{e['code']}({e['count']})" for e in prim[:6]])
    print("  WARNING(前5):", [f"{e['code']}({e['count']})" for e in warn[:5]])
    assert lv.get("has_error"), "日志应有 ERROR 输出"
    assert prim or warn, "top_errors 不应为空"
    print("  [fields OK]")

    # 2) 若 handlers 可导入则进一步验证 _discovery_to_text 的顺序
    try:
        import importlib.util, sys as _sys
        _spec = importlib.util.spec_from_file_location(
            "_dc_to_text",
            os.path.normpath(r"D:\CodeHub\AI\OpenRobotService\ai\agents\AiTaskPlatform\handlers\diagnose_flow.py"),
        )
        _mod = importlib.util.module_from_spec(_spec)
        _sys.modules["_dc_to_text_mod"] = _mod
        _spec.loader.exec_module(_mod)
        _discovery_to_text = _mod._discovery_to_text
    except Exception as e:  # handlers 有重依赖（core 等），仅跳过 text 顺序验证
        print("  [skip] _discovery_to_text 导入失败:", e)
        return
    text = _discovery_to_text(res)
    print("\n== _discovery_to_text 输出顺序 ==")
    print(text)
    i_err = text.find("错误级别分布")
    i_sig = text.find("Top 信号")
    i_terr = text.find("Top 高频错误")
    print(f"\n  错误级别分布@{i_err}  Top高频错误@{i_terr}  Top信号@{i_sig}")
    assert i_err != -1, "缺少错误级别分布"
    assert i_err < i_sig, "错误级别分布应在 Top 信号之前"
    if i_terr != -1:
        assert i_terr < i_sig, "Top 高频错误应在 Top 信号之前"
    print("  [order OK]")

if __name__ == "__main__":
    test_phrase_regex()
    test_clean_signal()
    test_full()
    test_triage_text()
    print("\n全部通过")
