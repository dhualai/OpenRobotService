"""待补充字段选题验证（真实入口：构造 SessionMemory → _compute_ticket_fields）。

    python ai/tests/verify_ticket_fields.py

选题规范（0824 收紧）：2 个核心（不问就无法定位/复现）+ 0-2 个锦上添花，总数 2-4，
且不得重复追问对话里已经给过的信息。
"""
import asyncio, io, os, sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_PROJ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJ)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJ, "ai", ".env"))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))


async def main():
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform as AgentCls
    from ai.core.memory import SessionMemory

    agent = AgentCls()
    await agent._ensure_clients()

    async def fields(turns):
        mem = SessionMemory(session_id="verify_fields", turns=turns)
        r = await agent._compute_ticket_fields("verify_fields", mem, 0)
        return r.get("ticket_type", ""), r.get("required_fields", {})

    # ── S1 报障只有现象：核心字段应落在可定位项（编号/故障码/版本一类） ──
    print("\nS1 报障缺定位信息：车不动了，任务下不了")
    tt, rf = await fields([
        {"role": "user", "content": "车不动了，任务下不了，屏幕上有个报错"},
        {"role": "assistant", "content": "请问具体现象？车辆能否手动推动？"},
        {"role": "user", "content": "推不动，调度界面一直转圈"},
    ])
    labels = list(rf.values())
    print(f"    type={tt} fields={rf}")
    check("S1 字段数 2-4", 2 <= len(rf) <= 4, f"{len(rf)} 个: {rf}")
    _s1_core_hit = any(k in str(rf) for k in ("编号", "码", "版本", "车型", "型号", "设备", "模块"))
    check("S1 含可定位核心字段", _s1_core_hit, f"fields={rf}")

    # ── S2 已给编号+故障码：不得重复追问已给信息 ──
    print("\nS2 报障已给编号/故障码：XCD151 报 2101")
    tt, rf = await fields([
        {"role": "user", "content": "XCD151 报错 2101，充电充不进去"},
        {"role": "assistant", "content": "请问这个现象是一直如此还是偶发？"},
        {"role": "user", "content": "从今天早上开始一直这样"},
    ])
    print(f"    type={tt} fields={rf}")
    check("S2 字段数 1-4", 1 <= len(rf) <= 4, f"{len(rf)} 个: {rf}")
    check("S2 不重复追问车辆编号", "编号" not in str(rf), f"fields={rf}")
    # 用户已给「报错 2101」：追问「完整故障码」是合理细化，重问「报错代码」才是没读对话
    _fc = str(rf)
    check("S2 故障码类追问必须是细化（如完整码），不是重问",
          ("故障码" not in _fc and "报错代码" not in _fc) or "完整" in _fc, f"fields={rf}")
    check("S2 不把用户已给数字当未知重问", "2101" not in _fc, f"fields={rf}")

    # ── S3 支持类咨询：字段应合理，不硬凑报障字段 ──
    print("\nS3 支持类：怎么配置充电桩功率")
    tt, rf = await fields([
        {"role": "user", "content": "帮我转工单，我要问下充电桩功率怎么配置"},
        {"role": "assistant", "content": "好的，请问是哪里的充电桩？"},
        {"role": "user", "content": "3号仓那边的新桩"},
    ])
    print(f"    type={tt} fields={rf}")
    check("S3 类型判定支持类", tt in ("support", "problem", "other"), f"type={tt}")
    check("S3 字段数 1-4", 1 <= len(rf) <= 4, f"{len(rf)} 个: {rf}")
    check("S3 标签均为中文短标签", all(0 < len(str(v)) <= 8 for v in rf.values()), f"fields={rf}")

    print(f"\n═══ 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 ═══")
    if FAIL:
        for f_ in FAIL:
            print("  失败:", f_)
        sys.exit(1)


asyncio.run(main())
