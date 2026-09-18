"""验证「补充信息后自动重新弹窗」修复（mock LLM，无真实依赖）。

背景（原始 bug）：草稿已存在（弹窗已出），用户补充信息（如「派单给贾爽」）时，
LLM 重新调 submit_ticket，但补充轮的 collected_fields 只带本轮新增字段——
执行器按「声明 vs 本轮已收集」判缺，会把前几轮已收齐的字段重复判成缺失，
返回 collecting，LLM 继续追问、不再重新弹窗（日志实锤：补充后无「草稿就绪」）。

第一版修复用 force_draft 直接跳过补充轮的判缺，但暴露了新问题：submit_ticket
的工具 schema 强制 required_fields 至少声明 1 个字段（minProperties:1），补充轮
没有真正缺项时 LLM 也会被迫编一个，若被写回 state.required_fields 会导致
confirm_submit 用这个虚假清单重新校验，把已弹窗的「就绪」草稿又拦下来。

根源修复（本版）：
1. 补充轮改用 TOOL_SCHEMA_SUPPLEMENT——required_fields 不再是必填参数，
   LLM 不会被逼着声明它并没有的缺项。
2. 执行器判缺时，把跨轮累计的 state.collected_info 合并进本轮 collected_fields
   再比对——不管 LLM 是否重复声明了早前已收集的字段，都不会被误判为缺失。
3. state.required_fields 依然只在首次生成草稿（非补充轮）时被写回，补充轮
   不覆盖——避免真正遗留的新缺口被写进去污染后续校验。

验证点：
1. 无草稿 + 字段缺 → collecting（原行为不回归）
2. 有草稿 + 补充轮，LLM 遵循新 schema 不声明 required_fields → draft_ready，重新弹窗
3. 有草稿 + 补充轮，LLM 仍重复声明了早前已满足的字段（未在本轮 collected_fields
   里重复给值）→ 合并 state.collected_info 后应视为已满足 → draft_ready
4. 有草稿 + 补充轮，LLM 声明了一个真正从未回答过的新缺口 → 应停在 collecting，
   不强行弹窗（force_draft 那种"无脑跳过判缺"的行为已被移除）
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ["AI_TICKET_TOOL_LOOP"] = "1"

from ai.core.memory import SessionMemory  # noqa: E402
from ai.agents.AiDiagnosisPlatform.pipeline import (  # noqa: E402
    AiDiagnosisPlatform, _save_agent_state, AgentState, DiagnosisRequest,
    _assess_ticket_readiness,
)


class FakeMemoryManager:
    def __init__(self):
        self.store = {}
    async def get_memory(self, session_id):
        m = self.store.get(session_id)
        if not m:
            m = SessionMemory(session_id=session_id)
            self.store[session_id] = m
        return m
    async def save_memory(self, memory):
        self.store[memory.session_id] = memory
    async def add_turn(self, session_id, role, content):
        m = await self.get_memory(session_id)
        m.turns.append({"role": role, "content": content})
        return m


class FakeLLM:
    """stream_with_tools：单轮，直接调 submit_ticket。"""
    def __init__(self, args):
        self._args = args
        self.calls = 0
    async def stream_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        yield {"type": "tool_calls", "tool_calls": [
            {"id": "call_1", "name": "submit_ticket", "arguments": self._args}],
            "content": ""}


def make_platform(args, with_draft, initial_required_fields=None):
    p = AiDiagnosisPlatform()
    p._llm_client = FakeLLM(args)
    p._memory_manager = FakeMemoryManager()
    state = AgentState(session_id="sess_supp", phase="diagnosing")
    state.problem_summary = "库位分支报 invalid order"
    state.collected_info = {"device_info": "AGV-03", "scene": "取放货"}
    if initial_required_fields is not None:
        state.required_fields = dict(initial_required_fields)
    mem = SessionMemory(session_id="sess_supp")
    mem.turns.append({"role": "user", "content": "库位配置咨询"})
    mem.turns.append({"role": "assistant", "content": "查一下知识库"})
    mem.turns.append({"role": "user", "content": "帮我提单给胡健楠"})
    if with_draft:
        mem.metadata["ticket_draft"] = {
            "title": "旧草稿", "type": "problem", "project_id": "", "project": "",
            "description": "旧描述", "priority": "中",
        }
    _save_agent_state(mem, state)
    return p, state, mem


async def run_branch(p, state, mem, query="补充一下，派单给贾爽"):
    """调用真实 _ticket_tool_loop_branch，收集 review 事件。"""
    req = DiagnosisRequest(session_id="sess_supp", query=query, created_by="tester")
    events = []
    # patch _build_ticket / _finalize_diagnosis，避免真实 LLM
    p._build_ticket = AsyncMock(return_value={
        "ticket_id": "AI-SUPP-1", "type": "problem", "title": "补充后草稿",
        "description": "[指定处理人：贾爽] 库位分支报 invalid order",
        "priority": "中", "project": "", "project_id": "",
    })
    p._finalize_diagnosis = AsyncMock(return_value={
        "type": "diagnosis", "thinking": "", "action": "answer",
        "message": "草稿已更新", "agent_state": {}, "title": "", "_tokens_streamed": True,
    })
    async for ev in p._ticket_tool_loop_branch(req, state, mem):
        events.append(ev)
    return events


async def main():
    ok = True

    # ---- 场景 1：无草稿 + 字段缺 → 保持 collecting（原行为不回归）----
    args_no_draft = {
        "ticket_type": "problem",
        "problem_summary": "库位分支报 invalid order",
        "required_fields": {"occurrence_time": "发生时间"},
        "collected_fields": {"device_info": "AGV-03"},
    }
    p1, st1, mem1 = make_platform(args_no_draft, with_draft=False)
    ev1 = await run_branch(p1, st1, mem1)
    statuses1 = [e["data"].get("stage") for e in ev1 if e["event"] == "status"]
    print("=== 场景1：无草稿 + 字段缺（应保持收集，不弹窗）===")
    print(f"  statuses: {statuses1}")
    if "review" not in statuses1:
        print("  ✅ 字段仍缺 → collecting，不弹窗（原行为保留）")
    else:
        print("  ❌ 字段缺却被强制弹窗"); ok = False

    # ---- 场景 2：有草稿 + 补充轮，遵循新 schema 不声明 required_fields ----
    args_supp = {
        "ticket_type": "problem",
        "problem_summary": "库位分支报 invalid order",
        "collected_fields": {"requested_assignee": "贾爽"},  # 只带本轮新增
    }
    p2, st2, mem2 = make_platform(args_supp, with_draft=True)
    ev2 = await run_branch(p2, st2, mem2)
    review_ev = [e for e in ev2 if e["event"] == "status" and e["data"].get("stage") == "review"]
    print("\n=== 场景2：有草稿 + 补充轮，不声明 required_fields（应重新弹窗）===")
    print(f"  review 事件数: {len(review_ev)}")
    if review_ev:
        draft = review_ev[0]["data"]["draft"]
        print(f"  草稿 description: {draft.get('description')}")
        if "贾爽" in draft.get("description", ""):
            print("  ✅ 补充的 assignee 已进草稿")
        else:
            print("  ❌ 补充的 assignee 丢失"); ok = False
        mem2b = await p2._memory_manager.get_memory("sess_supp")
        if mem2b.metadata.get("ticket_draft", {}).get("title") == "补充后草稿":
            print("  ✅ memory.ticket_draft 已更新为新草稿")
        else:
            print("  ❌ 草稿未写回 memory"); ok = False
    else:
        print("  ❌ 补充轮未重新弹窗（卡在 collecting）"); ok = False

    # ---- 场景 3：补充轮重复声明早前已满足的字段（本轮未重复给值）----
    # LLM 即使按新 schema 不必声明，仍可能习惯性带上 required_fields 重复列出
    # device_info/scene——这两个字段本轮 collected_fields 没有值（用户没有重复说），
    # 但 state.collected_info 里已经有。合并后应视为满足，正常弹窗。
    args_supp3 = {
        "ticket_type": "problem",
        "problem_summary": "库位分支报 invalid order",
        "required_fields": {"device_info": "设备信息", "scene": "场景"},
        "collected_fields": {"requested_assignee": "贾爽"},
    }
    p3, st3, mem3 = make_platform(
        args_supp3, with_draft=True,
        initial_required_fields={"device_info": "设备信息", "scene": "场景"})
    ev3 = await run_branch(p3, st3, mem3)
    review_ev3 = [e for e in ev3 if e["event"] == "status" and e["data"].get("stage") == "review"]
    ready3, missing3 = _assess_ticket_readiness(st3)
    print("\n=== 场景3：补充轮重复声明早前已满足的字段（合并跨轮信息后不应误判缺失）===")
    print(f"  重新弹窗: {bool(review_ev3)}")
    print(f"  st.required_fields: {st3.required_fields}")
    print(f"  _assess_ticket_readiness: ready={ready3}, missing={missing3}")
    if review_ev3 and ready3:
        print("  ✅ 合并跨轮 collected_info 后正确判定已满足，正常弹窗")
    else:
        print("  ❌ 早前已满足的字段被误判缺失，未能重新弹窗"); ok = False

    # ---- 场景 4：补充轮出现真正从未回答过的新缺口 → 应停在 collecting ----
    # 与旧版 force_draft（无脑跳过判缺）不同：现在改为合并式判缺，若 LLM 确实
    # 发现一个全新缺口且本轮/历史都没有答案，应正常追问，不能强行弹出草稿。
    args_supp4 = {
        "ticket_type": "problem",
        "problem_summary": "库位分支报 invalid order",
        "required_fields": {"occurrence_time": "发生时间"},  # 从未被问过/回答过
        "collected_fields": {"requested_assignee": "贾爽"},
    }
    p4, st4, mem4 = make_platform(args_supp4, with_draft=True)
    ev4 = await run_branch(p4, st4, mem4)
    review_ev4 = [e for e in ev4 if e["event"] == "status" and e["data"].get("stage") == "review"]
    print("\n=== 场景4：补充轮出现真正未回答的新缺口（不应强行弹窗）===")
    print(f"  重新弹窗: {bool(review_ev4)}")
    if not review_ev4:
        print("  ✅ 真正的新缺口正常拦截，未强行弹窗")
    else:
        print("  ❌ 真正缺失的信息被忽略，强行弹窗了"); ok = False

    print("\n=== " + ("PASS" if ok else "FAIL") + " ===")


if __name__ == "__main__":
    asyncio.run(main())
