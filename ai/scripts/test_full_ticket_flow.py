"""完整工具循环对话测试（真实 LLM，双开关全开）。

验证工具循环本身真正跑起来（不只是旧路径）：
  R1 咨询诊断 → diagnosis 意图 → 诊断工具循环（search_kb + submit_ticket）
  R2 提单     → ticket 意图 → 提单工具循环（submit_ticket → 追问字段）
  R3 补充字段 → 补齐 → draft_ready → review 弹窗
  R4 取消     → LLM 判取消 → 清状态
  R5 按钮再点 → _can_submit 拦截「刚放弃或提交过工单…」

日志验证点：
- R1 应出现 [diag_tool] 循环完成（search_kb 被调用）
- R2 应出现 [tool_loop] 循环完成（submit_ticket 被调用）
- 若只见 [stream] 旧路径日志 → 工具循环没触发（意图分类或开关问题）

运行：python ai/scripts/test_full_ticket_flow.py
"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
os.environ["AI_TICKET_TOOL_LOOP"] = "1"       # 提单工具循环
os.environ["AI_DIAGNOSIS_TOOL_LOOP"] = "1"     # 诊断工具循环

from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    DiagnosisRequest,
    get_diagnosis_platform,
)


async def ask(platform, session, query, label, skip_retrieval=False):
    """发一轮对话，返回 (token拼接, statuses, review_draft)。"""
    req = DiagnosisRequest(
        session_id=session, query=query, created_by="tester", skip_retrieval=skip_retrieval)
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
    print(f"  回复: {streamed[:250]}")
    if review_draft:
        print(f"  草稿: title={review_draft.get('title')}, type={review_draft.get('type')}")
    return streamed, statuses, review_draft


async def main():
    platform = await get_diagnosis_platform()
    session = f"sess_tool_{int(time.time())}"

    # R1 咨询 → diagnosis 意图 → 应走诊断工具循环（LLM 查知识库）
    await ask(platform, session, "库位分支下发取放货任务报 invalid order 怎么办？",
              "R1 咨询诊断（应走 search_kb）")

    # R2 提单 → ticket 意图 → 应走提单工具循环（submit_ticket → 追问字段）
    r2, _, _ = await ask(platform, session, "我要转工单，帮我提单给胡健楠",
                         "R2 提单（应走 submit_ticket）")

    # R3 如果追问了字段就补充（模拟用户配合）
    if "?" in r2 or "？" in r2 or "请" in r2 or "补充" in r2:
        await ask(platform, session,
                  "是库位分支下发取放货任务时报的 invalid order，车号 AGV-03，今天上午发生的",
                  "R3 补充字段")

    # R4 取消
    await ask(platform, session, "算了，不转工单了，帮我清掉", "R4 取消", skip_retrieval=True)

    # R5 按钮拦截验证：直接调 prepare_ticket
    print("\n=== R5 按钮再点（应被拦截） ===")
    res = await platform.prepare_ticket(session)
    print(f"  code={res.get('code')}, message={res.get('message')}")
    print(f"  stage={res.get('stage')}")
    if res.get("code") == 1 and "刚放弃或提交过工单" in (res.get("message") or ""):
        print("  ✅ 拦截文案正确")
    else:
        print("  ⚠️ 未被拦截或文案不符！")

    print("\n=== DONE ===")


if __name__ == "__main__":
    asyncio.run(main())
