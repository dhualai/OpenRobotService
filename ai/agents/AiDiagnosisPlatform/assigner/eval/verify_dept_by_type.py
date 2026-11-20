"""验证 R2 部门判定按工单类型（耗时/缺陷/需求/升级/咨询）区分逻辑。

目的：确认 feature/support/upgrade 类工单按"产品/项目归属"判部门，
而非被当成故障来判；bug/problem 仍按故障现象判部门。

用法：
    uv run python ai/agents/AiDiagnosisPlatform/assigner/eval/verify_dept_by_type.py
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
from ai.agents.AiDiagnosisPlatform.assigner.filtering.dept_router import DeptRouter


async def main():
    cfg = AssignerConfig()
    dr = DeptRouter(config=cfg)

    cases = [
        ("bug-硬件", TicketContext(id="b1", ticket_type="bug",
            title="AGV前轮脱落", problem_description="轮子卡死无法行走", status="new")),
        ("bug-调度", TicketContext(id="b2", ticket_type="bug",
            title="调度任务阻塞", problem_description="任务无法下发", status="new")),
        ("feature-调度需求", TicketContext(id="f1", ticket_type="feature",
            title="希望调度系统支持新增排队规则",
            problem_description="现有调度不支持自定义排队策略",
            project_name="四川峨眉山调度项目", status="new")),
        ("feature-摇人吧需求", TicketContext(id="f2", ticket_type="feature",
            title="摇人吧增加部门权限控制",
            problem_description="希望摇人吧服务号支持按部门设置可见范围",
            project_name="摇人吧服务号提单", status="new")),
        ("support-咨询", TicketContext(id="s1", ticket_type="support",
            title="请问如何配置地图编辑的路径避让",
            problem_description="想了解使用方法", project_name="调度项目", status="new")),
        ("upgrade-车端升级", TicketContext(id="u1", ticket_type="upgrade",
            title="车端定位算法升级需求", problem_description="希望提升定位稳定性", status="new")),
    ]

    print("=== R2 按类型判定部门（四档刻度 + 类型感知）===")
    for name, tk in cases:
        cands, res = await dr.route(tk, [])
        r2 = {k: round(v, 3) for k, v in sorted(res.signals.get("llm", {}).items(), key=lambda x: -x[1])}
        r3 = {k: round(v, 3) for k, v in sorted(res.signals.get("history", {}).items(), key=lambda x: -x[1])}
        fused = {k: round(v, 3) for k, v in sorted(res.dept_scores.items(), key=lambda x: -x[1])}
        print(f"\n[{name}] type={tk.ticket_type} project={tk.project_name or '-'}")
        print(f"  R2   : {r2}")
        print(f"  R3   : {r3}")
        print(f"  fused: {fused}")
        print(f"  primary={res.primary_dept} mode={res.mode} conf={res.confidence:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
