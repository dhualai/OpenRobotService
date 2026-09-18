"""规划器 mention_project 反幻觉闸门测试（0904）

生产实锤：规划 prompt 旧示例把平台名（「摇人吧」）列为可报称呼，flash 对
没提项目的话也输出 mention_project("摇人吧")，服务端唯一子串匹配命中平台
测试项目 → 所有用户提单都被误预填。

闸门：project_name（非 last）归一化空白后必须是本轮用户消息的子串，
否则拒收——与 backfill 溯源门同一机械校验纪律。
"""
import sys
from unittest.mock import AsyncMock

import pytest


_PROJECTS = [
    {"name": "东昇汽配厂潜伏车项目", "code": "X1"},
    {"name": "摇人吧服务号", "code": "Leo_test"},
]


class _FakeIntentClient:
    def __init__(self, tool_calls):
        self._tc = tool_calls

    async def complete_with_tools(self, **kw):
        return {"tool_calls": self._tc}


def _patch_intent(monkeypatch, tool_calls):
    import ai.core as _core
    client = _FakeIntentClient(tool_calls)

    async def _get():
        return client
    monkeypatch.setattr(_core, "get_intent_client", _get)


def _tc_route(intent="ticket"):
    return {"name": "route", "arguments": {"intent": intent}}


def _tc_mention(name):
    return {"name": "mention_project", "arguments": {"project_name": name}}


@pytest.fixture(autouse=True)
def _user_projects(platform, monkeypatch):
    monkeypatch.setattr(platform, "_get_user_projects",
                        AsyncMock(return_value=list(_PROJECTS)))


class TestMentionGate:
    @pytest.mark.asyncio
    async def test_hallucinated_platform_name_rejected(self, platform, monkeypatch,
                                                      make_state, make_request):
        """0904 事故复刻：mention 是平台名、本轮消息没提 → 拒收。"""
        _patch_intent(monkeypatch, [_tc_route(), _tc_mention("摇人吧")])
        state = make_state()
        memory = await platform._memory_manager.get_memory("mg-1")
        req = make_request(query="提单给胡健楠，我觉得应该要持久化用户信息")
        intent, _plan = await platform._plan_tools(req, state, memory)
        assert intent == "ticket"
        assert state.mentioned_project is None
        assert not state.pending_prefill_project

    @pytest.mark.asyncio
    async def test_from_context_not_query_rejected(self, platform, monkeypatch,
                                                   make_state, make_request):
        """mention 只出现在历史对话（助手自称），不在本轮消息 → 拒收。"""
        _patch_intent(monkeypatch, [_tc_route("diagnosis"), _tc_mention("摇人吧服务号")])
        state = make_state()
        memory = await platform._memory_manager.get_memory("mg-2")
        memory.turns.append({"role": "assistant", "content": "我是摇人吧服务号的AI助手"})
        req = make_request(query="车不动了怎么办")
        await platform._plan_tools(req, state, memory)
        assert state.mentioned_project is None

    @pytest.mark.asyncio
    async def test_real_mention_captured(self, platform, monkeypatch, make_state, make_request):
        _patch_intent(monkeypatch, [_tc_route(), _tc_mention("东昇")])
        state = make_state()
        memory = await platform._memory_manager.get_memory("mg-3")
        req = make_request(query="给东昇那个项目提单，车不动了")
        await platform._plan_tools(req, state, memory)
        assert state.mentioned_project == {
            "name": "东昇汽配厂潜伏车项目", "code": "X1"}

    @pytest.mark.asyncio
    async def test_whitespace_normalized(self, platform, monkeypatch, make_state, make_request):
        """用户消息带空格、flash 照抄时去空白差异不误拒。"""
        _patch_intent(monkeypatch, [_tc_route(), _tc_mention("东昇")])
        state = make_state()
        memory = await platform._memory_manager.get_memory("mg-4")
        req = make_request(query="给 东 昇 提个单")
        await platform._plan_tools(req, state, memory)
        assert state.mentioned_project is not None

    @pytest.mark.asyncio
    async def test_last_reference_bypasses_gate(self, platform, monkeypatch,
                                                make_state, make_request):
        """「last」指代不撞闸门，取上单项目。"""
        _patch_intent(monkeypatch, [_tc_route(), _tc_mention("last")])
        state = make_state(last_submitted_ticket={
            "ticket_id": "TK-1", "title": "x", "project": "东昇汽配厂潜伏车项目",
            "project_id": "X1"})
        memory = await platform._memory_manager.get_memory("mg-5")
        req = make_request(query="跟上个单一个项目，再提一单")
        await platform._plan_tools(req, state, memory)
        assert state.mentioned_project == {
            "name": "东昇汽配厂潜伏车项目", "code": "X1"}
