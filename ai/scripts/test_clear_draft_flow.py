"""验证「弹窗取消 ≠ 放弃」修复：取消后补充信息链路不被拦截。

场景（真实 LLM）：
  1. 提单收集 → 草稿生成 → 弹窗
  2. 弹窗取消（clear_draft）→ 草稿应保留，状态不被清
  3. 对话补充信息 → 工具循环继续收集 → 重新出弹窗
  4. 对话显式放弃（说「不转工单了」）→ 才写 cancelled 标记 + 清草稿

日志验证点：
- R1/R3 应有 [tool_loop] 循环完成 + review 事件
- R3 草稿应更新（含补充字段）
- clear_draft 后 memory.ticket_draft 仍存在（不被清）
"""
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["AI_TICKET_TOOL_LOOP"] = "1"
os.environ["AI_DIAGNOSIS_TOOL_LOOP"] = "1"

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


async def ask(platform, session, query, label):
    req = DiagnosisRequest(session_id=session, query=query, created_by="tester")
    streamed = ""
    statuses = []
    review_draft = None
    async for ev in platform.run_stream(req):
        if ev.get("event") == "token":
            streamed += ev.get("data", "")
        elif ev.get("event") == "status":
            st = ev.get("data", {})
            statuses.append(st.get("stage"))
            if st.get("stage") == "review" and st.get("draft"):
                review_draft = st["draft"]
    print(f"\n=== {label} ===")
    print(f"  statuses: {statuses}")
    print(f"  回复: {streamed[:200]}")
    return streamed, statuses, review_draft


async def collect_until_review(platform, session, starter):
    """持续补充字段直到出 review 弹窗（避免收集链长度不确定）。"""
    await ask(platform, session, starter, "开局提单")
    # 补充分数轮，直到出现 review
    extras = [
        "补充一下，车号是 AGV-03，库位分支是 W1-2",
        "是今天上午十点发生的，当时在跑取放货任务",
    ]
    draft = None
    for i, extra in enumerate(extras):
        _, statuses, draft = await ask(platform, session, extra, f"补充({i+1})")
        if "review" in statuses:
            return draft
    # 还没出弹窗，兜底查草稿
    mem = await platform._memory_manager.get_memory(session)
    return mem.metadata.get("ticket_draft")


async def main():
    platform = await get_diagnosis_platform()
    session = f"sess_clear_{int(time.time())}"

    # R1 收集 → 直到出草稿弹窗
    draft = await collect_until_review(platform, session, "我要转工单，帮我提单给胡健楠")
    print(f"\n=== 弹窗草稿生成: {'有' if draft else '无'} ===")

    # R2 弹窗取消 → 草稿应保留
    print(f"\n=== R2 弹窗取消（clear_draft）===")
    res = await platform.clear_draft(session)
    print(f"  clear_draft: {res}")
    memory2 = await platform._memory_manager.get_memory(session)
    has_draft_after = bool(memory2.metadata.get("ticket_draft"))
    from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
    state = _load_agent_state(memory2.metadata)
    print(f"  取消后 ticket_draft 仍在: {has_draft_after}")
    print(f"  取消后 last_submitted_ticket: {state.last_submitted_ticket if state else None}")
    if has_draft_after and not (state and state.last_submitted_ticket):
        print("  ✅ 取消≠放弃：草稿保留、无 cancelled 标记")
    else:
        print("  ❌ 取消被当成了放弃（草稿被清/写了 cancelled 标记）")

    # R3 再点按钮 → 应复用草稿重新弹窗（不被 _can_submit 拦）
    res3 = await platform.prepare_ticket(session)
    print(f"\n=== R3 取消后点按钮（应复用草稿重新弹窗）===")
    print(f"  code={res3.get('code')}, stage={res3.get('stage')}")
    if res3.get("stage") in ("draft_ready", "need_fields") and res3.get("draft"):
        print("  ✅ 取消后点按钮重新弹窗（继续提单）")
    else:
        print(f"  ⚠️ {res3.get('message', '')}")

    # R4 对话补充信息 → 应走工具循环继续，不被 _can_submit 拦
    r4, s4, draft4 = await ask(
        platform, session,
        "补充一下，是今天上午十点发生的",
        "R4 补充信息（应走工具循环，不应被拦）")
    if "刚放弃或提交过工单" in r4:
        print("  ❌ 被 _can_submit 拦截（取消被当成了放弃）")
    else:
        print("  ✅ 未被拦截，正常补充")

    # R5 显式放弃 → 才写 cancelled + 清草稿
    r5, s5, _ = await ask(platform, session, "算了，不转工单了，帮我清掉", "R5 显式放弃")
    memory5 = await platform._memory_manager.get_memory(session)
    state5 = _load_agent_state(memory5.metadata)
    has_draft5 = bool(memory5.metadata.get("ticket_draft"))
    print(f"\n=== R5 显式放弃 ===")
    print(f"  草稿已清: {not has_draft5}")
    print(f"  last_submitted_ticket: {state5.last_submitted_ticket if state5 else None}")
    if state5 and state5.last_submitted_ticket and state5.last_submitted_ticket.get("ticket_id") == "cancelled":
        print("  ✅ 显式放弃 → cancelled 标记")
    else:
        print("  ❌ 显式放弃未写 cancelled 标记")

    # R6 放弃后再点按钮 → 被拦（这才该拦）
    res6 = await platform.prepare_ticket(session)
    print(f"\n=== R6 放弃后再点按钮（应被拦）===")
    print(f"  code={res6.get('code')}, message={res6.get('message')}")
    if res6.get("code") == 1 and "刚放弃或提交过工单" in (res6.get("message") or ""):
        print("  ✅ 显式放弃后被拦，正确")
    else:
        print("  ⚠️ 未按预期拦截")

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
