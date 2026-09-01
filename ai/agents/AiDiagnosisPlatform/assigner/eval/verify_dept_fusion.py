"""验证部门融合新算法：LLM 不打折 + 历史佐证修正。

场景对照（历史缺失 vs 历史有分）：
- 历史缺失 → final = LLM 分（不打折）
- 历史有分 → 按"该部门历史占比 >= 平均线"加 history_bonus / 否则减 history_penalty

用法：
    uv run python ai/agents/AiDiagnosisPlatform/assigner/eval/verify_dept_fusion.py
"""
from __future__ import annotations

import logging
logging.disable(logging.CRITICAL)  # 静默无关日志

import asyncio
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parents[5]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter
from ai.agents.AiDiagnosisPlatform.assigner.settings import AssignerConfig

CN = {
    "机器人事业部": "硬件",
    "智能移动研究院": "车端",
    "智能规划研究院": "调度/服务号",
}


def main():
    cfg = AssignerConfig()
    fusion = (cfg.department_routing or {}).get("fusion") or {}
    bonus = float(fusion.get("history_bonus", 0.05))
    thresh = float(fusion.get("history_confirm_threshold", 0.5))
    print(f"history_bonus={bonus} history_confirm_threshold={thresh}")
    print("=" * 70)

    def show(name, llm, hist):
        merged = DeptRouter._fuse(llm, hist, bonus, thresh)
        print(f"[{name}]")
        for d in sorted(merged, key=lambda x: -merged[x]):
            lv = llm.get(d, 0)
            hv = hist.get(d, 0)
            if hv <= 0:
                tag = "(无历史→纯LLM)"
            elif hv >= thresh:
                tag = "(历史佐证→加分)"
            else:
                tag = "(历史不足→维持)"
            print(f"   {CN.get(d,d):<10} llm={lv:.2f} hist={hv:.2f} -> fused={merged[d]:.3f} {tag}")

    print("=== 场景1: 历史缺失（无相似历史）→ 纯 LLM 不打折 ===")
    show("硬件 0.95", {"机器人事业部": 0.95}, {})
    show("调度 0.90", {"智能规划研究院": 0.90}, {})

    print("\n=== 场景2: 历史有分（佐证）===")
    # 单部门历史占满（8/10 派这）
    show("调度 0.95, 历史占0.8(佐证)", {"智能规划研究院": 0.95}, {"智能规划研究院": 0.8})
    # 单部门历史占比低
    show("调度 0.95, 历史占0.3(不偏向)", {"智能规划研究院": 0.95}, {"智能规划研究院": 0.3})

    print("\n=== 场景3: 多部门历史分布 ===")
    show("主调车端混合", {"智能移动研究院": 0.3, "智能规划研究院": 0.9},
         {"智能移动研究院": 0.6, "智能规划研究院": 0.4})

    print("\n=== 场景4: 摘要（历史缺失 vs 有分的首位对比）===")
    print("历史缺失: LLM 0.95 调度 -> fused 应=0.95（不打折）")
    print("历史佐证: LLM 0.95 + 历史0.8 -> fused 应=1.0（0.95+0.05 封顶）")


if __name__ == "__main__":
    main()
