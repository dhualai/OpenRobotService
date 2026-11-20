# -*- coding: utf-8 -*-
"""oneshot 分支 problem_summary 回填 + 闸门 spoke 判据（0901 生产死锁修复）。

生产实锤（sess_msinpzyx_sx969d）：提过单后用户描述全新问题（orderId 都给了），
闸门仍拦「请描述新现象」——旧判据等的 summary 唯一写入口是 state_update 工具，
而诊断意图全走 _diagnosis_oneshot_branch（无工具），summary 永远没人写。

修复两层：
  ① 闸门判据重构：_can_submit 只看 user_spoke_after_submit——提交/取消后
     用户又发过消息即放行，不再依赖 summary；
  ② oneshot 回填：summary 为空时回填本轮 query（fill_problem_summary=True
     仅 diagnosis 意图调用点传），供后续「转工单」的草稿取问题描述。
"""
import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import _can_submit

LAST_TICKET = {"ticket_id": "#595", "db_id": 7, "submitted_at": 1756700000}


def _gen_stream(text: str = "这是排查回答。"):
    async def _stream(*args, **kwargs):
        yield text
    return _stream


class TestOneshotSummaryRefill:
    """分支单元：回填触发条件 × 闸门放行 × 不变量"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_refill_fills_summary_for_draft_context(self, platform, make_state,
                                                          make_request, monkeypatch):
        """提单后新问题走 oneshot（fill=True）→ summary 回填（供提单草稿取问题描述）；
        闸门放行由 user_spoke_after_submit 负责（run_stream 已置位）"""
        platform._llm_client.stream = _gen_stream()
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET),
                           user_spoke_after_submit=True)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="XQE下发放货任务没带推送识别参数 orderId:32861",
                               session_id=state.session_id)

        async for _ in platform._diagnosis_oneshot_branch(
                request, state, memory, "【知识库】…", fill_problem_summary=True):
            pass

        assert state.problem_summary.startswith("XQE下发放货任务没带推送识别参数")
        saved = memory.metadata["agent_state"]["problem_summary"]
        assert saved == state.problem_summary, "回填必须持久化到 memory"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_overwrite_existing_summary(self, platform, make_state,
                                                 make_request):
        """summary 非空（省略式追问轮）→ 不覆盖已提炼的问题"""
        platform._llm_client.stream = _gen_stream()
        state = make_state(phase="diagnosing",
                           problem_summary="机器人激光传感器无数据",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="然后呢", session_id=state.session_id)

        async for _ in platform._diagnosis_oneshot_branch(
                request, state, memory, "", fill_problem_summary=True):
            pass

        assert state.problem_summary == "机器人激光传感器无数据"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fill_false_no_refill(self, platform, make_state, make_request):
        """courtesy/问候调用点（fill=False）→ 闲聊不伪造新问题"""
        platform._llm_client.stream = _gen_stream()
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="哈哈好的", session_id=state.session_id)

        async for _ in platform._diagnosis_oneshot_branch(
                request, state, memory, ""):
            pass

        assert state.problem_summary == ""
        assert _can_submit(state)[0] is False, "闲聊后闸门必须仍拦截"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_query_no_refill(self, platform, make_state, make_request):
        """query 空 → 不回填（防空 summary）"""
        platform._llm_client.stream = _gen_stream()
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="   ", session_id=state.session_id)

        async for _ in platform._diagnosis_oneshot_branch(
                request, state, memory, "", fill_problem_summary=True):
            pass

        assert state.problem_summary == ""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_long_query_truncated(self, platform, make_state, make_request):
        """超长 query 截到 120 字（防 summary 撑爆后续 prompt）"""
        platform._llm_client.stream = _gen_stream()
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="故障" * 100, session_id=state.session_id)

        async for _ in platform._diagnosis_oneshot_branch(
                request, state, memory, "", fill_problem_summary=True):
            pass

        assert len(state.problem_summary) == 120

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_failure_not_fatal(self, platform, make_state,
                                           make_request, monkeypatch):
        """save_memory 抛异常 → 回填不阻断回答，result 事件照常产出"""
        platform._llm_client.stream = _gen_stream()

        calls = {"n": 0}

        async def _boom(memory):
            calls["n"] += 1
            if calls["n"] == 1:  # 只炸回填那次；_finalize_diagnosis 后续照常
                raise RuntimeError("redis down")

        monkeypatch.setattr(platform._memory_manager, "save_memory", _boom)
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="新故障描述", session_id=state.session_id)

        events = [e async for e in platform._diagnosis_oneshot_branch(
            request, state, memory, "", fill_problem_summary=True)]

        assert state.problem_summary == "新故障描述", "内存态仍回填（闸门本轮即放行）"
        assert any(e["event"] == "result" for e in events), "持久化失败不能吞回答"


class TestSpokeLifecycle:
    """闸门判据 user_spoke_after_submit 的生命周期：提交/取消置 False →
    run_stream 收到新消息置 True → 提交成功又置回 False（防同轮/狂点）"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_run_stream_disarms_gate_on_new_message(self, platform, make_state,
                                                           make_request):
        """提交后（spoke=False）新消息进来 → run_stream 置 True，闸门放行"""
        state = make_state(phase="resolved", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        assert _can_submit(state)[0] is False, "前置：提交后未说话时闸门必须拦"
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _save_agent_state, _load_agent_state)
        _save_agent_state(memory, state)
        request = make_request(query="新的故障描述", session_id=state.session_id)

        async for _ in platform.run_stream(request):
            pass

        reloaded = _load_agent_state(memory.metadata)
        assert reloaded.user_spoke_after_submit is True, "新消息后必须置位并持久化"
        assert _can_submit(reloaded)[0] is True, "说话后闸门必须放行"

    @pytest.mark.unit
    def test_reset_state_rearms_gate(self, make_state):
        """_reset_state_after_submit 把 spoke 置回 False——闸门重新武装"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _reset_state_after_submit

        class _Mem:
            metadata = {}
            turns = []

        state = make_state(phase="escalated", problem_summary="故障",
                           collected_info={"project": "基地"},
                           user_spoke_after_submit=True)
        _reset_state_after_submit(state, _Mem(),
                                  {"ticket_id": "AI-9", "title": "t",
                                   "project": "基地", "project_id": "1"}, 9)
        assert state.last_submitted_ticket.get("ticket_id") == "AI-9"
        assert state.user_spoke_after_submit is False
        assert _can_submit(state)[0] is False, "提交后闸门必须重新拦截"


class TestStreamIntentWiring:
    """集成：意图 → oneshot 的 fill_problem_summary 接线"""

    def _patch(self, platform, monkeypatch, intent):
        captured = {"fill": None, "called": False}

        async def _fake_plan(req, state, memory):
            # 生产与 .env 均 AI_PLAN_EXECUTE=1：意图由规划器给出
            return intent, []

        async def _fake_classify(llm, raw, resolved, context_turns=None):
            return intent

        async def _fake_retrieve(session_id, state, context_turns=None,
                                 query_override=""):
            return "【知识库】排查步骤"

        async def _fake_oneshot(req, state, memory, reference_docs="",
                                fill_problem_summary=False):
            captured["called"] = True
            captured["fill"] = fill_problem_summary
            yield {"event": "result", "data": {"type": "diagnosis",
                                               "action": "answer", "message": "ok"}}

        monkeypatch.setattr(platform, "_plan_tools", _fake_plan)
        monkeypatch.setattr(platform, "_classify_intent", _fake_classify)
        monkeypatch.setattr(platform, "_retrieve_with_context", _fake_retrieve)
        monkeypatch.setattr(platform, "_diagnosis_oneshot_branch", _fake_oneshot)
        return captured

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_diagnosis_intent_fills(self, platform, make_state,
                                           make_request, monkeypatch):
        """diagnosis 意图（有检索资料）→ oneshot 收到 fill=True"""
        captured = self._patch(platform, monkeypatch, "diagnosis")
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="车不走了", session_id=state.session_id,
                               skip_retrieval=False)

        async for _ in platform._agent_think_stream(request, state, memory):
            pass

        assert captured["called"]
        assert captured["fill"] is True

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_courtesy_intent_does_not_fill(self, platform, make_state,
                                                  make_request, monkeypatch):
        """courtesy 意图 → oneshot 收到 fill=False（闲聊不伪造新问题）"""
        captured = self._patch(platform, monkeypatch, "courtesy")
        state = make_state(phase="diagnosing", problem_summary="",
                           last_submitted_ticket=dict(LAST_TICKET))
        memory = await platform._memory_manager.get_memory(state.session_id)
        # 「辛苦了朋友」不命中闲聊收尾短接正则（纯谢谢/好的才短接），
        # 会走到 courtesy 分支的 oneshot 调用
        request = make_request(query="辛苦了朋友", session_id=state.session_id,
                               skip_retrieval=False)

        async for _ in platform._agent_think_stream(request, state, memory):
            pass

        assert captured["called"]
        assert captured["fill"] is False
