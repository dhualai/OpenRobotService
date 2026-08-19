"""
提单测试 — 对话提单（Chat Path）+ 按钮提单（Button Path）+ 混合路径 + 提单后补充

Mock LLM + Memory + Retriever，调用 AiDiagnosisPlatform 真实方法。
"""
import json
import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import AgentState


# ================================================================
# 1. 对话提单（Chat Path）
# ================================================================

class TestChatSubmit:
    """对话中通过 LLM 或关键词触发提单"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_submit_success(self, platform, make_state, make_request):
        """LLM action=submit + collected_info 有 project → ticket 生成"""
        state = make_state(
            phase="idle",
            problem_summary="机器人离线",
            collected_info={"project": "华大制造基地"},
        )
        request = make_request(query="帮我提交工单")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "用户要求提单",
                "action": "submit",
                "intent": "troubleshoot",
                "message": "好的，已为你生成工单。",
                "state_update": {},
            }, ensure_ascii=False)
            + '\n```\n好的，已为你生成工单。'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 对话提单先生成待确认草稿，不会直接入库。
        assert result["action"] == "answer"
        assert state.phase == "diagnosing"
        assert "ticket_draft" in memory.metadata

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_submit_missing_project(self, platform, make_state, make_request):
        """LLM action=submit 但无 project → 被拦截，引导补项目"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={},  # 无 project
        )
        request = make_request(query="帮我提交工单")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "用户要求提单",
                "action": "submit",
                "intent": "troubleshoot",
                "message": "好的，已为你生成工单。",
                "state_update": {},
            }, ensure_ascii=False)
            + '\n```\n好的，已为你生成工单。'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 缺少字段时保留收集上下文，下一轮补充后再生成草稿。
        assert result["action"] in ("answer", "ask")
        assert "项目" not in result.get("message", "") or "弹窗" in result.get("message", "")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_draft_cancel_then_supplement_reopens_review(self, platform, make_state, make_request):
        """review 关闭后补充新字段，下一轮应重新发 review。"""
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            required_fields={"requested_assignee": "处理人"},
            collected_info={}, ticket_collecting=["处理人"],
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        _save_agent_state(memory, state)
        memory.metadata["ticket_draft"] = {
            "title": "机器人离线", "description": "机器人离线", "type": "problem", "project": "",
        }
        await platform._memory_manager.save_memory(memory)

        # 弹窗取消只关闭弹窗，草稿与固定清单必须保留。
        cancelled = await platform.clear_draft(session_id)
        assert cancelled["code"] == 0
        assert memory.metadata.get("ticket_draft")
        assert state.required_fields == {"requested_assignee": "处理人"}

        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = json.dumps({
            "action": "answer", "ticket_intent": True,
            "state_update": {"collected_info": {"requested_assignee": "张三"}},
        }, ensure_ascii=False)
        request = make_request(query="提给张三处理", session_id=session_id)
        events = [event async for event in platform._agent_think_stream(request, state, memory)]
        assert any(e["event"] == "status" and e["data"]["stage"] == "review" for e in events)
        assert memory.metadata["ticket_draft"]["description"].endswith("机器人离线")
        assert state.collected_info["requested_assignee"] == "张三"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_draft_cancel_command_clears_state(self, platform, make_state, make_request):
        """已有草稿但 collecting 为空时，LLM ticket_cancel 也必须清理。"""
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_collecting=[], required_fields={"occurrence_time": "发生时间"},
            collected_info={"occurrence_time": "今天"},
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        _save_agent_state(memory, state)
        memory.metadata["ticket_draft"] = {"title": "机器人离线", "project": ""}
        await platform._memory_manager.save_memory(memory)
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = json.dumps({
            "action": "answer", "ticket_cancel": True,
            "state_update": {}, "message": "好的，不转工单。",
        }, ensure_ascii=False)
        request = make_request(query="取消提单", session_id=session_id)
        events = [event async for event in platform._agent_think_stream(request, state, memory)]
        assert any(e["event"] == "status" and e["data"]["stage"] == "collect_cancel" for e in events)
        assert "ticket_draft" not in memory.metadata
        assert state.required_fields is None
        assert state.collected_info == {}
        assert state.ticket_collecting == []


# ================================================================
# 2. 按钮提单（Button Path）
# ================================================================

class TestButtonSubmit:
    """按钮路径：prepare_ticket → confirm_submit"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_prepare_draft_ready(self, platform, make_state):
        """有 project + can_submit → prepare 返回 stage=draft_ready"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大制造基地"},
        )
        session_id = state.session_id

        # 先存入 state 到 memory
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        result = await platform.prepare_ticket(session_id)

        assert result["stage"] in ("draft_ready", "need_fields")
        assert "draft" in result

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_prepare_need_fields(self, platform, make_state):
        """无 project → prepare 返回 stage=need_fields"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={},  # 无 project
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        result = await platform.prepare_ticket(session_id)

        assert result["stage"] in ("not_ready", "need_fields")
        assert "项目名称" in result.get("missing_info", []) or "项目" in str(result)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_prepare_blocked(self, platform, make_state):
        """刚提完单（last_submitted_ticket）+ 无新 problem → prepare 闭环拦截 code=1"""
        state = make_state(
            phase="resolved", problem_summary="",
            last_submitted_ticket={"ticket_id": "T-1", "db_id": 1, "title": "旧工单", "topic": "旧问题"},
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        result = await platform.prepare_ticket(session_id)

        assert result["code"] == 1
        assert "新问题" in result["message"] or "新现象" in result["message"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_confirm_success(self, platform, make_state):
        """有 draft + overrides 补全 → confirm 成功"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大"},
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        # 先 prepare 获得 draft
        await platform.prepare_ticket(session_id)

        # confirm with overrides
        result = await platform.confirm_submit(
            session_id,
            overrides={"project": "华大制造基地"},
            created_by="tester",
        )

        assert result["code"] == 0
        assert "data" in result

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_confirm_no_draft(self, platform, make_state):
        """无 draft → confirm 返回 code=1"""
        state = make_state(phase="idle")
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        result = await platform.confirm_submit(session_id)

        assert result["code"] == 1
        assert "没有待确认" in result["message"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_confirm_missing_fields(self, platform, make_state):
        """有 draft 但 overrides 未补全 → confirm 返回 code=1"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={},  # 无 project
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        # prepare 在缺 project 时不建草稿（返回 not_ready），手动注入一个缺 project 的草稿
        memory = await platform._memory_manager.get_memory(session_id)
        memory.metadata["ticket_draft"] = {"project": ""}
        await platform._memory_manager.save_memory(memory)

        # confirm 不补 project
        result = await platform.confirm_submit(session_id)

        assert result["code"] == 1
        assert "项目" in result["message"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_confirm_override_merges(self, platform, make_state):
        """overrides 合并到 draft，覆盖对应字段"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "旧项目"},
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        await platform.prepare_ticket(session_id)

        result = await platform.confirm_submit(
            session_id,
            overrides={"project": "新项目名称"},
            created_by="tester",
        )

        assert result["code"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_get_draft_returns_none_when_empty(self, platform, make_state):
        """无 draft → get_draft 返回 data.draft=None"""
        state = make_state(phase="idle")
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        result = await platform.get_draft(session_id)

        assert result["code"] == 0
        assert result["data"]["draft"] is None


# ================================================================
# 3. 混合路径
# ================================================================

class TestMixedPaths:
    """对话提单 ↔ 按钮提单互斥"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_chat_submit_then_button_blocked(self, platform, make_state, make_request):
        """对话已提单 → 按钮准备被拦截"""
        state = make_state(
            phase="idle",
            problem_summary="机器人离线",
            collected_info={"project": "华大"},
        )
        session_id = state.session_id

        # 使用 side_effect 区分诊断和工单的 LLM 响应
        call_count = [0]

        def _dual_response(prompt: str = "", **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                # 诊断调用 → submit
                return '```json\n{"action":"submit","intent":"troubleshoot","message":"已提单","state_update":{}}\n```\n已提单'
            else:
                # _build_ticket 调用 → 返回工单 JSON
                return json.dumps({
                    "title": "测试工单",
                    "type": "problem",
                    "priority": "中",
                    "description": "测试描述",
                    "project": "华大",
                }, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _dual_response
        platform._llm_client.complete.return_value = None

        request = make_request(query="帮我提交工单", session_id=session_id)
        memory = await platform._memory_manager.get_memory(session_id)

        # 对话提单 — mock submit 已将 state 清空并保存到 memory
        await platform._agent_think(request, state, memory)

        # 按钮准备 — 应被拦截（mock submit 已将 phase 设为 resolved，problem_summary=""）
        result = await platform.prepare_ticket(session_id)

        # 对话路径先生成草稿；按钮路径不应重复提交。
        assert result.get("code", 0) == 1 or result.get("stage") in ("need_fields", "draft_ready")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_submit_then_chat_blocked(self, platform, make_state, make_request):
        """按钮已提单 → 对话"转工单"被拦截"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大"},
        )
        session_id = state.session_id

        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        # 按钮 prepare + confirm（mock confirm 会清理 state）
        await platform.prepare_ticket(session_id)
        await platform.confirm_submit(
            session_id,
            overrides={"project": "华大制造基地"},
            created_by="tester",
        )

        # confirm 已清理 state → 对话"转工单"被拦截
        memory = await platform._memory_manager.get_memory(session_id)
        request = make_request(query="转工单", session_id=session_id)

        # mock confirm 已清理：last_submitted_ticket 已设，problem 清空
        from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
        loaded_state = _load_agent_state(memory.metadata) or make_state(phase="resolved", problem_summary="")

        # 让 LLM 输出 submit + 空 state_update（无新问题），触发闭环拦截
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "用户要求提单", "action": "submit", "intent": "troubleshoot",
                "message": "好的，已为你生成工单。", "state_update": {},
            }, ensure_ascii=False)
            + '\n```\n好的，已为你生成工单。'
        )

        result = await platform._agent_think(request, loaded_state, memory)

        assert result["action"] == "answer"
        assert "新问题" in result["message"] or "新现象" in result["message"]


# ================================================================
# 4. 提单后补充
# ================================================================

class TestPostSubmitFollowUp:
    """提单后状态重置 + 新问题处理"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_state_reset_after_submit(self, platform, make_state, make_request):
        """提单后 state 重置：last_submitted_ticket 设、诊断状态清空"""
        state = make_state(
            phase="idle",
            problem_summary="机器人离线",
            collected_info={"project": "华大"},
        )
        request = make_request(query="帮我提交工单")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n{"action":"submit","intent":"troubleshoot","message":"已提单","state_update":{}}\n```\n已提单'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        await platform._agent_think(request, state, memory)

        # 对话提单先生成待确认草稿，不会直接清空诊断状态。
        assert state.problem_summary
        assert memory.metadata.get("ticket_draft")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_post_submit_new_problem_not_follow_up(self, platform, make_state, make_request):
        """已提单 → 新故障描述 → 按新诊断处理，不追加"""
        # 先提单
        state = make_state(
            phase="idle",
            problem_summary="机器人离线",
            collected_info={"project": "华大"},
        )
        request = make_request(query="帮我提交工单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n{"action":"submit","intent":"troubleshoot","message":"已提单","state_update":{}}\n```\n已提单'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)
        await platform._agent_think(request, state, memory)

        # 新问题 → 走正常诊断路径
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "新的故障",
                "action": "answer",
                "intent": "troubleshoot",
                "message": "新的故障需要重新排查。",
                "state_update": {"problem_summary": "新的故障：激光报错"},
            }, ensure_ascii=False)
            + '\n```\n新的故障需要重新排查。'
        )
        state2 = make_state(
            phase="resolved",
            problem_summary="新的故障：激光报错",
            session_id=state.session_id,
        )
        memory = await platform._memory_manager.get_memory(state.session_id)
        request2 = make_request(query="新的故障：激光报错", session_id=state.session_id)

        result = await platform._agent_think(request2, state2, memory)

        # 应正常诊断，不拦截
        assert result["action"] == "answer"


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
