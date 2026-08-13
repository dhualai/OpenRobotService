import sys, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('ai/.env')

from ai.agents.AiTaskPlatform.orchestrator import _parse_review_command, _build_review_context, LogOrchestrator

# 1. investigate 解析
raw1 = json.dumps({
    "intent": "investigate", "reasoning": "查XNA-169",
    "directive": {"time_start": "2026-08-11 11:00", "time_end": "2026-08-11 11:05",
                  "robot_filter": "XNA-169", "error_only": True, "max_results": 50}
}, ensure_ascii=False)
cmd1 = _parse_review_command(raw1)
assert cmd1 and cmd1["intent"] == "investigate" and cmd1["directive"]["robot_filter"] == "XNA-169", cmd1
print("investigate 解析 OK:", cmd1["directive"])

# 2. conclude（散文包裹）
raw2 = '根据分析 {"intent":"conclude","conclusion":"根因是一致性超阈值","fallback":false} 结束'
cmd2 = _parse_review_command(raw2)
assert cmd2 and cmd2["intent"] == "conclude" and "一致性" in cmd2["conclusion"], cmd2
print("conclude(散文包裹) 解析 OK")

# 3. review context 组装
ctx_text = _build_review_context(
    {"title": "车不动", "problem_summary": "XNA-169 11点不动", "hypotheses": ["一致性"]},
    "[工程师] 我抓了日志", "帮我看看",
    "【日志 Discovery 摘要】...时段 11:01~11:03",
    [{"line": 100, "summary": "一致性超过update阈值 42s"}],
    [{"round": 1, "matched": 50, "directive": {"robot_filter": "XNA-169"}}],
)
assert "用户问题" in ctx_text and "Discovery" in ctx_text and "已收集证据" in ctx_text
print("review context 组装 OK")

# 4. pipeline 聚合
import ai.agents.AiTaskPlatform.pipeline as p
a = p.AiTaskAgent()
assert hasattr(a, "discuss") and hasattr(a, "_extract_log_paths")
print("AiTaskAgent 聚合 discuss OK")

print("\n全部通过 ✓")
