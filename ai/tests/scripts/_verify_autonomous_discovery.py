import sys, time
sys.path.insert(0, r"D:\CodeHub\AI\OpenRobotService")
from ai.agents.AiTaskPlatform.log_analyzer.indexer import LogIndex
from ai.agents.AiTaskPlatform.log_analyzer.triage import _top_errors, _level_distribution, run_triage
from ai.agents.AiTaskPlatform.handlers.diagnose_flow import _discovery_to_text

p = r"D:\CodeHub\AI\OpenRobotService\ai\tests\test_logs\live\debug_logs.log.29"
t0 = time.perf_counter(); idx = LogIndex(p); idx.build()
print("build", round(time.perf_counter() - t0, 1), "s lines", idx._total)
print("level_dist:", _level_distribution(idx))
print("TOP REAL ERRORS (自主发现):")
te = _top_errors(idx, top_n=12)
print("ERROR primary:")
for e in te.get("primary", []):
    print("  %6d  %s" % (e["count"], e["code"]))
print("WARNING:")
for e in te.get("warning", [])[:8]:
    print("  %6d  %s" % (e["count"], e["code"]))

print("\n=== Discovery 文本 ===")
triage = run_triage(p, user_question="", manual_dir="", index=idx)
print(_discovery_to_text(triage))
