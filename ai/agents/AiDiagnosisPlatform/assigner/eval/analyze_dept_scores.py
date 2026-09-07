"""部门路由 R2/R3 分数语义分析（开发调试用）。

目的：搞清 R2(LLM) 与 R3(历史) 输出的分数范围/分布，为设计
"以 LLM 为基准、历史一致性增减" 的融合算法提供数据依据。

用法：
    uv run python ai/agents/AiDiagnosisPlatform/assigner/eval/analyze_dept_scores.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[5]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig
from ai.agents.AiDiagnosisPlatform.assigner.schemas import TicketContext
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.llm_dept_signal import LlmDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.signals.history_dept_signal import HistoryDeptSignal
from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter


async def _raw_signals(cfg: AssignerConfig, ticket: TicketContext):
    """返回 (原始R2 dict, 原始R3 dict)。R2 用内部方法拿未过滤前的 raw items（含 <min_conf）。"""
    sig = LlmDeptSignal(config=cfg)
    # 用同 prompt 但不动 classify：直接手动拼 prompt 调 LLM 看全部分数
    prompt = sig._build_prompt(ticket)
    from ai.core import get_llm_client
    llm = await get_llm_client()
    r = await llm.complete(prompt, max_tokens=int((cfg.department_routing or {}).get("llm", {}).get("max_tokens", 250)), temperature=0)
    items = sig._parse(r, {d.get("name") for d in cfg.departments if d.get("name")})
    raw = {it["name"]: (it["confidence"], it["reason"]) for it in items}
    return raw


async def main():
    cfg = AssignerConfig()
    print("=== 配置 ===")
    print("departments:", [d.get("name") for d in cfg.departments])
    print("R2 llm_cfg:", cfg.department_routing.get("llm"))
    print("R3 hist_cfg:", cfg.department_routing.get("history"))
    print("weights:", cfg.department_routing.get("weights"))
    print("thresholds:", cfg.department_routing.get("thresholds"))

    cases = [
        ("硬件", TicketContext(id="t1", title="AGV前轮脱落", problem_description="轮子卡死无法行走", status="new")),
        ("定位", TicketContext(id="t2", title="车辆定位漂移", problem_description="重定位失败雷达异常", status="new")),
        ("调度", TicketContext(id="t3", title="调度任务阻塞", problem_description="任务无法下发", status="new")),
        ("模糊", TicketContext(id="t4", title="页面显示异常", problem_description="加载缓慢", status="new")),
        ("混合", TicketContext(id="t5", title="机器人调度任务异常", problem_description="AGV执行任务时调度下发失败", status="new")),
    ]

    print("\n=== R2 LLM 原始分数（含 <min_confidence 的；标 * 为当前会被丢弃）===")
    for name, tk in cases:
        raw = await _raw_signals(cfg, tk)
        minc = float((cfg.department_routing or {}).get("llm", {}).get("min_confidence", 0.75))
        parts = []
        for dname, (conf, reason) in sorted(raw.items(), key=lambda x: -x[1][0]):
            mark = "" if conf >= minc else " *"
            parts.append(f"{dname}={conf:.2f}{mark}({reason[:18]})")
        print(f"  {name}: " + (" | ".join(parts) if parts else "(空)"))

    print("\n=== R3 历史（本地通常为空） + 融合后 dept_scores + mode ===")
    dr = DeptRouter(config=cfg)
    for name, tk in cases:
        cands, res = await dr.route(tk, [])
        print(f"  {name}: R2={ {k: round(v,3) for k,v in sorted(res.signals.get('llm',{}).items(), key=lambda x:-x[1])} }")
        print(f"         R3={ {k: round(v,3) for k,v in sorted(res.signals.get('history',{}).items(), key=lambda x:-x[1])} }")
        print(f"         fused={ {k: round(v,3) for k,v in sorted(res.dept_scores.items(), key=lambda x:-x[1])} }")
        print(f"         primary={res.primary_dept} mode={res.mode} conf={res.confidence:.3f} margin={res.margin:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
