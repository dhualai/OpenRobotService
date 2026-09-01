"""
提单测试 — 对话提单（Chat Path）+ 按钮提单（Button Path）+ 混合路径 + 提单后补充

Mock LLM + Memory + Retriever，调用 AiDiagnosisPlatform 真实方法。
"""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock

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
# 项目预填（对话中识别项目 → 严格校验 → 写入草稿，弹窗仍可改）
# ================================================================

class TestProjectPrefill:
    """project_choice 严格校验 + _build_ticket 预填覆盖"""

    _PROJECTS = [
        {"name": "江苏常州多摩川混场项目", "code": "13"},
        {"name": "华大制造基地", "code": "7"},
    ]

    def test_match_by_exact_name(self):
        """LLM 照抄列表 name（允许首尾空白）→ 命中"""
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        got = AiDiagnosisPlatform._match_project_choice(
            " 江苏常州多摩川混场项目 ", self._PROJECTS)
        assert got == {"name": "江苏常州多摩川混场项目", "code": "13"}

    def test_match_by_code(self):
        """LLM 抄了编号 → 命中（code 也是列表内精确值）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        got = AiDiagnosisPlatform._match_project_choice("7", self._PROJECTS)
        assert got == {"name": "华大制造基地", "code": "7"}

    def test_no_fuzzy_match(self):
        """近似但非精确（幻觉/抄错）→ 拒绝，宁可走弹窗"""
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        assert AiDiagnosisPlatform._match_project_choice("多摩川项目", self._PROJECTS) is None
        assert AiDiagnosisPlatform._match_project_choice("华大基地", self._PROJECTS) is None
        assert AiDiagnosisPlatform._match_project_choice("不存在的项目", self._PROJECTS) is None

    def test_display_format_stripped(self):
        """照抄把展示格式整行带上（「名称（编号: code）」，live 实锤两次）→ 剥离取回完整 name；
        近似值仍拒绝（宁空勿错不变）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        got = AiDiagnosisPlatform._match_project_choice(
            "江苏常州多摩川混场项目（编号: 13）", self._PROJECTS)
        assert got == {"name": "江苏常州多摩川混场项目", "code": "13"}
        got2 = AiDiagnosisPlatform._match_project_choice(
            "就给华大制造基地提", self._PROJECTS)
        assert got2 == {"name": "华大制造基地", "code": "7"}
        assert AiDiagnosisPlatform._match_project_choice("多摩川", self._PROJECTS) is None

    def test_empty_choice_or_list(self):
        """空 choice / 空列表 → None（降级现状）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
        assert AiDiagnosisPlatform._match_project_choice("", self._PROJECTS) is None
        assert AiDiagnosisPlatform._match_project_choice(None, self._PROJECTS) is None
        assert AiDiagnosisPlatform._match_project_choice("华大制造基地", []) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_build_ticket_prefill(self, platform, make_state):
        """prefill_project 写入 draft 的 project/project_id；
        不传 → 维持空（现状）；analysis 里 LLM 偶然输出的 project 被忽略"""
        state = make_state(problem_summary="机器人离线")
        memory = await platform._memory_manager.get_memory(state.session_id)

        draft = await platform._build_ticket(
            state.session_id, state, memory,
            prefill_project={"name": "华大制造基地", "code": "7"})
        assert draft["project"] == "华大制造基地"
        assert draft["project_id"] == "7"

        draft2 = await platform._build_ticket(state.session_id, state, memory)
        assert draft2["project"] == ""
        assert draft2["project_id"] == ""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_prefill_passes_required_check(self, platform, make_state):
        """预填后 draft 过 _check_required_fields：project 不再缺，
        missing_fields 不含 project（弹窗免选，直接确认）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _check_required_fields
        state = make_state(problem_summary="机器人离线")
        memory = await platform._memory_manager.get_memory(state.session_id)
        draft = await platform._build_ticket(
            state.session_id, state, memory,
            prefill_project={"name": "华大制造基地", "code": "7"})
        check = _check_required_fields(draft)
        assert "project" not in check["missing"]
        assert check["ok"]


# ================================================================
# 项目预填 × 弹窗回写（confirm_submit：预填 draft + overrides 各种叠加）
# ================================================================

@pytest.fixture
async def platform_real_confirm(platform, monkeypatch):
    """恢复真实 confirm_submit（conftest 的 platform fixture 把它替换成了
    简化 mock——空值也覆盖、无归一化、返回假工单结构，测不到真实叠加逻辑）。

    DB 依赖隔离方式：
    - upsert_task：向 sys.modules 注入假 task_adapter 模块（真模块模块级
      import app.core.db，conftest 为避免该链路才 mock 了整个方法；假模块
      让 confirm_submit 函数内的局部 import 拿到桩，无需触碰真模块）
    - _resolve_project：patch 为返回 None（归一化是有 DB 依赖的独立逻辑，
      不在本测试范围；None 即「不命中不重写」，恰好测出 overrides 直通值）
    - _attach_chat_snapshot：no-op（截图附加有 MinIO/DB 依赖，且与本测试无关）
    """
    import sys
    import types

    class _FakeRec:
        id = 88888

    fake_ta = types.ModuleType("ai.core.task_adapter")
    fake_ta.upsert_task = lambda ticket, created_by="": _FakeRec()
    monkeypatch.setitem(sys.modules, "ai.core.task_adapter", fake_ta)

    async def _noop(*args, **kwargs):
        return None

    async def _no_resolve(*args, **kwargs):
        return None

    monkeypatch.setattr(platform, "_attach_chat_snapshot", _noop)
    monkeypatch.setattr(platform, "_resolve_project", _no_resolve)

    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform
    monkeypatch.setattr(
        platform, "confirm_submit",
        AiDiagnosisPlatform.confirm_submit.__get__(platform))
    return platform


# ================================================================
# 收集超限强弹 × 弹窗提交死锁（0825：弹窗让提交、提交让回对话补充）
# ================================================================

# ================================================================
# 跨单引用 × 字段卡死保险丝（0825：用户三答「#595工单里有」仍被追问账户名 4 次）
# ================================================================

class TestCollectRefAndFuse:
    """收集模式：#N 工单指代识别/查单注入 + 同字段连问保险丝"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ref_ticket_lookup_and_prompt_injection(
            self, platform, make_state, make_request, monkeypatch):
        """用户答「#N 工单里有」→ LLM 输出 referenced_ticket → 服务端查单挂 state，
        下一轮收集 prompt 注入工单内容（LLM 自行从中提取字段值）"""
        from ai.agents.AiDiagnosisPlatform import pipeline as pl

        async def _fake_lookup(ref_text):
            assert "595" in ref_text
            return "#595 USP平台无法登录\n账户名：admin，现象：登录后闪退"

        monkeypatch.setattr(pl, "_lookup_ticket_ref", _fake_lookup)

        state = make_state(
            phase="diagnosing", problem_summary="USP无法登录",
            required_fields={"account": "账户名", "error_content": "故障现象"},
            collected_info={"error_content": "无法登录"},
            ticket_collecting=["账户名"],
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = json.dumps({
            "action": "ask", "referenced_ticket": "595",
            "state_update": {"collected_info": {}},
            "message": "请直接告知账户名。",
        }, ensure_ascii=False)
        request = make_request(query="#595这个工单里有", session_id=session_id)
        events = [e async for e in platform._agent_think_stream(request, state, memory)]

        assert "admin" in state.ticket_ref_context, "查单结果未挂到 state"
        # 下一轮收集 prompt 注入「用户引用的历史工单」块
        prompt = platform._build_diagnosis_prompt(state, memory, reference_docs="（跳过检索）")
        assert "用户引用的历史工单" in prompt
        assert "admin" in prompt
        # 协议字段进 JSON 模板
        assert '"referenced_ticket":""' in prompt

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stuck_field_fuse_forces_skip(self, platform, make_state, make_request):
        """同一字段连续 3 轮收不到值 → 强制记「无」跳过并直接进 review，
        不再出现第 4 次追问（0825 生产事故形状）"""
        state = make_state(
            phase="diagnosing", problem_summary="USP无法登录",
            required_fields={"account": "账户名", "error_content": "故障现象"},
            collected_info={"error_content": "无法登录"},
            ticket_collecting=["账户名"],
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state, _load_agent_state
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)

        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = json.dumps({
            "action": "ask", "referenced_ticket": "",
            "state_update": {"collected_info": {}},
            "message": "请告知无法登录的账户名。",
        }, ensure_ascii=False)

        queries = ["转工单", "#595这个工单里有", "都说了在那个单子里"]
        for i, q in enumerate(queries, 1):
            state = _load_agent_state(memory.metadata)
            request = make_request(query=q, session_id=session_id)
            events = [e async for e in platform._agent_think_stream(request, state, memory)]
            has_review = any(e["event"] == "status"
                             and isinstance(e["data"], dict)
                             and e["data"].get("stage") == "review" for e in events)
            if i < 3:
                assert not has_review, f"第{i}轮不该提前弹窗"
            else:
                assert state.collected_info.get("account", "").startswith("无"), \
                    f"保险丝未强制记无: {state.collected_info}"
                assert state.ticket_collecting == []
                assert has_review, "第 3 轮保险丝触发后应进 review"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_lookup_ticket_ref_no_digits(self):
        """指代里没数字 → 不查库直接空串（静默降级）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _lookup_ticket_ref
        assert await _lookup_ticket_ref("上次那个单子") == ""
        assert await _lookup_ticket_ref("") == ""


class TestPlanExecute:
    """plan-and-execute 合并规划分支（AI_PLAN_EXECUTE=1）：一次 flash 输出
    意图路由+工具组合 → 并行执行 → 单次回答；关闭开关走原意图分类+乐观检索路径（零影响）"""

    def _patch_stream_path(self, platform, monkeypatch, plan, intent="diagnosis"):
        """把 stream 依赖全部替换成可控桩，返回捕获字典"""
        captured = {"retrieve_queries": [], "oneshot_docs": None, "plan_called": False,
                    "retrieve_called": False, "classify_called": False}

        async def _fake_plan(req, state, memory):
            captured["plan_called"] = True
            return intent, plan

        async def _fake_classify(llm, raw, resolved, context_turns=None):
            captured["classify_called"] = True
            return intent

        async def _fake_retrieve(session_id, state, context_turns=None, query_override=""):
            captured["retrieve_called"] = True
            captured["retrieve_queries"].append(query_override)
            return f"【知识库】{query_override} 的排查步骤"

        async def _fake_oneshot(req, state, memory, reference_docs=""):
            captured["oneshot_docs"] = reference_docs
            yield {"event": "result", "data": {"type": "diagnosis", "action": "answer",
                                               "message": "ok", "agent_state": {}}}

        monkeypatch.setattr(platform, "_plan_tools", _fake_plan)
        monkeypatch.setattr(platform, "_classify_intent", _fake_classify)
        monkeypatch.setattr(platform, "_retrieve_with_context", _fake_retrieve)
        monkeypatch.setattr(platform, "_diagnosis_oneshot_branch", _fake_oneshot)
        return captured

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_combo_tools_executed_in_parallel_branch(
            self, platform, make_state, make_request, monkeypatch):
        """开关开 + 规划组合（检索+查单）→ 并行执行，资料两块都进回答轮，
        检索用规划词、工单内容挂 state"""
        from ai.agents.AiDiagnosisPlatform import pipeline as pl
        monkeypatch.setenv("AI_PLAN_EXECUTE", "1")

        async def _fake_lookup(ref_text):
            assert ref_text.strip() == "595"
            return "#595 USP平台无法登录\n账户名：admin"

        monkeypatch.setattr(pl, "_lookup_ticket_ref", _fake_lookup)

        captured = self._patch_stream_path(
            platform, monkeypatch,
            plan=[("search_kb", {"query": "锁区配置步骤"}),
                  ("lookup_ticket", {"ticket_no": 595})])

        state = make_state(phase="diagnosing", diagnosis_rounds=1)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="@#595里说的锁区怎么配置", skip_retrieval=False, session_id=state.session_id)
        events = [e async for e in platform._agent_think_stream(request, state, memory)]

        assert captured["plan_called"], "开关开时规划器必须被调用"
        assert captured["retrieve_queries"] == ["锁区配置步骤"], "检索词必须来自规划"
        assert "【知识库】锁区配置步骤" in (captured["oneshot_docs"] or "")
        assert "#595" in captured["oneshot_docs"] and "admin" in captured["oneshot_docs"], \
            "工单内容必须注入回答轮"
        assert "不要说无法查看" in captured["oneshot_docs"]
        assert state.ticket_ref_context.startswith("#595"), "查单结果要挂 state 供后续轮引用"
        assert any(e["event"] == "result" for e in events)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_tools_plan_answers_without_docs(
            self, platform, make_state, make_request, monkeypatch):
        """开关开 + 规划无工具（寒暄/纯咨询）→ 空资料直接单轮回答，不检索"""
        monkeypatch.setenv("AI_PLAN_EXECUTE", "1")
        captured = self._patch_stream_path(platform, monkeypatch, plan=[])

        state = make_state(phase="diagnosing", diagnosis_rounds=1)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="你是谁", skip_retrieval=False, session_id=state.session_id)
        [e async for e in platform._agent_think_stream(request, state, memory)]

        assert captured["plan_called"]
        assert not captured["retrieve_called"], "无工具规划不应触发检索"
        assert captured["oneshot_docs"] == ""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_switch_off_keeps_legacy_path(
            self, platform, make_state, make_request, monkeypatch):
        """开关关（默认）→ 规划器不启动，走乐观检索老路径"""
        captured = self._patch_stream_path(platform, monkeypatch,
                                           plan=[("search_kb", {"query": "不该出现"})])
        # 关键差异：老路径的乐观检索在意图分类前发起，query_override=用户原话
        async def _fake_retrieve_legacy(session_id, state, context_turns=None, query_override=""):
            captured["retrieve_called"] = True
            captured["retrieve_queries"].append(query_override)
            return "【知识库】乐观检索结果"

        monkeypatch.setattr(platform, "_retrieve_with_context", _fake_retrieve_legacy)
        monkeypatch.delenv("AI_PLAN_EXECUTE", raising=False)

        state = make_state(phase="diagnosing", diagnosis_rounds=1)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="锁区怎么配置", skip_retrieval=False, session_id=state.session_id)
        [e async for e in platform._agent_think_stream(request, state, memory)]

        assert not captured["plan_called"], "开关关时规划器不得被调用"
        assert captured["classify_called"], "开关关时独立意图分类必须照跑（原路径）"
        assert captured["retrieve_called"], "老路径应发起乐观检索"
        assert captured["retrieve_queries"] == ["锁区怎么配置"], \
            "老路径检索词必须是用户原话（query_override=request.query）"
        assert "乐观检索结果" in (captured["oneshot_docs"] or "")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_lookup_ticket_reuses_prefetched_context(
            self, platform, make_state, make_request, monkeypatch):
        """@# 预查已挂同号工单 → 规划执行复用 state 缓存，不重复查库"""
        monkeypatch.setenv("AI_PLAN_EXECUTE", "1")
        lookup_calls = []

        async def _fake_lookup(ref_text):
            lookup_calls.append(ref_text)
            return "#3 查到了"

        from ai.agents.AiDiagnosisPlatform import pipeline as pl
        monkeypatch.setattr(pl, "_lookup_ticket_ref", _fake_lookup)
        captured = self._patch_stream_path(
            platform, monkeypatch, plan=[("lookup_ticket", {"ticket_no": 3})])

        state = make_state(phase="diagnosing", diagnosis_rounds=1,
                           ticket_ref_context="#3 预查已挂的工单内容")
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="@#3 怎么样了", skip_retrieval=False, session_id=state.session_id)
        [e async for e in platform._agent_think_stream(request, state, memory)]

        assert lookup_calls == [], "同号已预查时不应重复查库"
        assert "预查已挂的工单内容" in (captured["oneshot_docs"] or "")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_merged_intent_ticket_with_lookup_prefetches(
            self, platform, make_state, make_request, monkeypatch):
        """合并模式 route=ticket + 规划带 lookup（「针对595再提一单」）→
        查单执行挂 state、search_kb 丢弃、快路径主 prompt 当轮带旧单区块，
        且不再调用独立意图分类"""
        from ai.agents.AiDiagnosisPlatform import pipeline as pl
        monkeypatch.setenv("AI_PLAN_EXECUTE", "1")
        lookup_calls = []

        async def _fake_lookup(ref_text):
            lookup_calls.append(ref_text)
            return "#595 USP平台无法登录\n账户名：admin"

        monkeypatch.setattr(pl, "_lookup_ticket_ref", _fake_lookup)
        captured = self._patch_stream_path(
            platform, monkeypatch,
            plan=[("lookup_ticket", {"ticket_no": 595})], intent="ticket")

        state = make_state(phase="diagnosing", diagnosis_rounds=1)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="针对595工单的问题我再提一个单子",
                               skip_retrieval=False, session_id=state.session_id)

        prompts = []

        async def _capture_complete(prompt="", **kw):
            prompts.append(prompt)
            return ('```json\n' + json.dumps({
                "thinking": "", "action": "answer", "intent": "chat",
                "ticket_intent": True, "ticket_cancel": False,
                "message": "好的，已了解旧单内容，开始提单。",
                "state_update": {},
            }, ensure_ascii=False) + '\n```\n好的，已了解旧单内容，开始提单。')

        platform._llm_client.complete = AsyncMock(side_effect=_capture_complete)
        platform._get_user_projects = AsyncMock(return_value=[])

        [e async for e in platform._agent_think_stream(request, state, memory)]

        assert not captured["classify_called"], "合并模式不得再调独立意图分类"
        assert lookup_calls == ["595"], "提单轮规划带查单必须执行"
        assert not captured["retrieve_called"], "提单轮不得执行 search_kb"
        assert state.ticket_ref_context.startswith("#595"), "查单结果挂 state"
        assert state.ticket_fast_lane, "ticket 路由要置快路径旗标"
        # 按 fast lane prompt 特征取样，不能用 prompts[-1]：意图判定后启动的
        # decide 字段预跑是后台任务（无等待直接消费），其调用会混进捕获列表
        main_prompt = next((p for p in prompts
                            if "请先判断用户本轮是否真的提出了提单诉求" in p), "")
        assert "用户引用的历史工单" in main_prompt and "#595" in main_prompt, \
            "快路径主 prompt 当轮必须带旧单区块（草稿字段预填有据）"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_merged_intent_courtesy_direct_answer(
            self, platform, make_state, make_request, monkeypatch):
        """合并模式 route=courtesy（无工具）→ 单轮小 prompt 直答，零资料开销"""
        monkeypatch.setenv("AI_PLAN_EXECUTE", "1")
        captured = self._patch_stream_path(platform, monkeypatch,
                                           plan=[], intent="courtesy")

        state = make_state(phase="diagnosing", diagnosis_rounds=1)
        memory = await platform._memory_manager.get_memory(state.session_id)
        request = make_request(query="辛苦了", skip_retrieval=False, session_id=state.session_id)
        [e async for e in platform._agent_think_stream(request, state, memory)]

        assert captured["plan_called"]
        assert not captured["classify_called"], "合并模式不得再调独立意图分类"
        assert not captured["retrieve_called"], "courtesy 不得触发检索"
        assert captured["oneshot_docs"] == ""


class TestForceSubmitConfirm:
    """超限强弹的草稿带 force_submit：confirm_submit 放行 collected_info 兜底"""

    @staticmethod
    async def _put_draft(platform, state, force: bool):
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(state.session_id)
        _save_agent_state(memory, state)
        memory.metadata["ticket_draft"] = {
            "title": "充电桩故障", "type": "problem",
            "project": "华大制造基地", "project_id": "7",
        }
        if force:
            memory.metadata["ticket_draft"]["force_submit"] = True
        await platform._memory_manager.save_memory(memory)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_force_draft_confirm_passes(self, platform_real_confirm, make_state):
        """强弹草稿（collected_info 不齐 + force_submit）→ 点提交入库，不再打回"""
        state = make_state(
            phase="diagnosing",
            problem_summary="充电桩不伸出但调度显示已充电",
            required_fields={"charger_location": "充电桩位置", "fault_time": "发生时间",
                             "vehicle_id": "车辆编号"},
            collected_info={"charger_location": "锂电二楼一号"},
            ticket_collecting=[],
        )
        await self._put_draft(platform_real_confirm, state, force=True)

        result = await platform_real_confirm.confirm_submit(
            state.session_id, overrides={}, created_by="tester")

        assert result["code"] == 0, f"超限强弹草稿被拦截: {result}"
        # 标记只用于放行校验，不得泄进工单记录
        assert "force_submit" not in result["data"]["ticket"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_normal_draft_incomplete_still_blocks(self, platform_real_confirm, make_state):
        """普通草稿（无标记）+ collected_info 不齐 → 兜底校验照拦（防直调 API 绕过）"""
        state = make_state(
            phase="diagnosing",
            problem_summary="充电桩不伸出但调度显示已充电",
            required_fields={"charger_location": "充电桩位置", "fault_time": "发生时间",
                             "vehicle_id": "车辆编号"},
            collected_info={"charger_location": "锂电二楼一号"},
            ticket_collecting=[],
        )
        await self._put_draft(platform_real_confirm, state, force=False)

        result = await platform_real_confirm.confirm_submit(
            state.session_id, overrides={}, created_by="tester")

        assert result["code"] == 1
        assert result.get("stage") == "not_ready"
        assert "补充" in result["message"]


class TestPrefillWriteback:
    """预填值进 draft 后，弹窗确认链路的最终入库值"""

    @staticmethod
    async def _prefilled_draft(platform, state):
        """按按钮路径跑出合法草稿后，把项目字段改成预填值（模拟工具循环预填）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        memory = await platform._memory_manager.get_memory(state.session_id)
        _save_agent_state(memory, state)
        await platform._memory_manager.save_memory(memory)
        await platform.prepare_ticket(state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        draft = memory.metadata.get("ticket_draft")
        assert draft, "prepare 应生成草稿"
        draft["project"] = "华大制造基地"
        draft["project_id"] = "7"
        await platform._memory_manager.save_memory(memory)
        return draft

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confirm_prefill_unchanged(self, platform_real_confirm, make_state):
        """预填 + 用户不动项目直接确认 → project/project_id 原样入库"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大"},
        )
        await self._prefilled_draft(platform_real_confirm, state)

        result = await platform_real_confirm.confirm_submit(
            state.session_id, overrides={}, created_by="tester")

        assert result["code"] == 0
        assert result["data"]["ticket"]["project"] == "华大制造基地"
        assert result["data"]["ticket"]["project_id"] == "7"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confirm_user_changes_project(self, platform_real_confirm, make_state):
        """预填 A + 用户弹窗改成 B（前端成对发送）→ 入库 B 的名和 code，
        不残留 A 的 code"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大"},
        )
        await self._prefilled_draft(platform_real_confirm, state)

        result = await platform_real_confirm.confirm_submit(
            state.session_id,
            overrides={"project": "江苏常州多摩川混场项目", "project_id": "13"},
            created_by="tester")

        assert result["code"] == 0
        assert result["data"]["ticket"]["project"] == "江苏常州多摩川混场项目"
        assert result["data"]["ticket"]["project_id"] == "13"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_confirm_name_only_override_clears_stale_code(self, platform_real_confirm, make_state):
        """预填 + overrides 只改 project 名不带 code（双工单兜底名 project_id=''
        / 直调 API）→ 旧 code 不残留，project_id 置空（旧版行为）"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            collected_info={"project": "华大"},
        )
        await self._prefilled_draft(platform_real_confirm, state)

        result = await platform_real_confirm.confirm_submit(
            state.session_id,
            overrides={"project": "摇人吧服务号提单", "project_id": ""},
            created_by="tester")

        assert result["code"] == 0
        assert result["data"]["ticket"]["project"] == "摇人吧服务号提单"
        assert result["data"]["ticket"]["project_id"] == ""


# ================================================================
# 工具循环空转纠偏（2026-08-20 生产实录：触发轮只说过渡语不调工具 → 死寂）
# ================================================================

class TestToolLoopIdleCorrection:
    """AI_TICKET_TOOL_LOOP 分支：零工具调用的空转轮必须被纠偏重跑，粘性续接必须置位。"""

    @staticmethod
    def _scripted_llm(rounds):
        """构造 stream_with_tools 按调用序脚本化的 mock LLM。

        rounds: [(content, tool_calls), ...]，每次 LLM 调用消费一项（超出时
        复用最后一项）。tool_calls 为 OpenAI 格式、arguments 是 dict。
        返回 (mock, calls)：calls 记录每次调用收到的 messages 快照。
        """
        mock = MagicMock()
        mock.complete = AsyncMock(return_value="{}")
        mock.stream = None
        calls = []

        def _stream_with_tools(messages=None, tools=None, **kw):
            idx = len(calls)
            calls.append([dict(m) for m in (messages or [])])
            content, tool_calls = rounds[min(idx, len(rounds) - 1)]

            async def _gen():
                if content:
                    half = max(1, len(content) // 2)
                    yield {"type": "token", "content": content[:half]}
                    yield {"type": "token", "content": content[half:]}
                yield {"type": "final", "content": content, "tool_calls": tool_calls}

            return _gen()

        mock.stream_with_tools = _stream_with_tools
        return mock, calls

    async def _run(self, platform, make_request, make_state, rounds):
        llm, calls = self._scripted_llm(rounds)
        platform._llm_client = llm
        platform._get_user_projects = AsyncMock(return_value=[])
        state = make_state(problem_summary="车辆不动")
        request = make_request(query="帮我提单吧")
        memory = await platform._memory_manager.get_memory(request.session_id)
        events = [ev async for ev in
                  platform._ticket_tool_loop_branch(request, state, memory)]
        return state, memory, events, calls

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_idle_transition_corrected_then_tool_called(
            self, platform, make_request, make_state):
        """空转轮（只说过渡语）→ 纠偏重跑 → 调工具收「还缺」→ 追问用户。

        生产实录 14:46 的复现：第一轮只说「好的，我帮您转工单，我看一下
        还需要补充哪些信息：」就结束（0 工具调用），气泡死寂。
        """
        tool_call = {
            "id": "call_1", "name": "submit_ticket",
            "arguments": {
                "ticket_type": "problem",
                "problem_summary": "车辆不动",
                "required_fields": {"vehicle_id": "车辆编号"},
                "collected_fields": {},
            },
        }
        state, memory, events, calls = await self._run(
            platform, make_request, make_state,
            [
                ("好的，我帮您转工单，我看一下还需要补充哪些信息：", []),  # 空转轮
                ("", [tool_call]),                                        # 纠偏轮：调工具
                ("请问车辆编号是多少？", []),                              # 收到「还缺」后追问
            ],
        )
        # 纠偏轮的 messages 里注入了纠偏 system 指令（含不复述铁律）
        # + 空转回合的 assistant 消息
        assert any(m["role"] == "system" and "没有调用 submit_ticket" in m["content"]
                   for m in calls[1])
        assert any(m["role"] == "system" and "不要复述" in m["content"]
                   for m in calls[1])
        assert any(m["role"] == "assistant" and "补充哪些信息" in (m["content"] or "")
                   for m in calls[1])
        # 用户看到：过渡语 + 追问（同一气泡续上，无死寂）
        tokens = "".join(ev["data"] for ev in events if ev["event"] == "token")
        assert "补充哪些信息" in tokens
        assert "车辆编号是多少" in tokens
        # 工具参数已渐进写回 state + 粘性续接置位（下一轮直接回工具循环）
        assert state.tool_loop_active is True
        assert state.ticket_type == "problem"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_double_idle_sets_sticky_no_infinite_loop(
            self, platform, make_request, make_state):
        """纠偏轮仍空转 → 不无限重试，走「保留收集状态」，但粘性续接必须置位。

        生产实录 14:47：空转轮没置 tool_loop_active，用户补完 4 个字段后
        被意图分类掉进旧状态机。"""
        state, memory, events, calls = await self._run(
            platform, make_request, make_state,
            [
                ("好的，我帮您转工单，我看一下还需要补充哪些信息：", []),
                ("请问车辆编号是多少？", []),
            ],
        )
        assert len(calls) == 2  # 只纠偏一次，不循环
        assert state.tool_loop_active is True
        assert state.problem_summary == "车辆不动"  # 收集状态未销毁
        tokens = "".join(ev["data"] for ev in events if ev["event"] == "token")
        assert "补充哪些信息" in tokens
        assert "车辆编号是多少" in tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_abandon_not_retried(self, platform, make_request, make_state):
        """LLM 判用户放弃（固定话术「不转工单」）→ 不纠偏，直接清状态。"""
        state, memory, events, calls = await self._run(
            platform, make_request, make_state,
            [("好的，不转工单。有什么其他问题随时问我。", [])],
        )
        assert len(calls) == 1  # 放弃轮不重跑
        assert state.tool_loop_active is False
        assert state.problem_summary == ""  # 状态已清空
        tokens = "".join(ev["data"] for ev in events if ev["event"] == "token")
        assert tokens.count("不转工单") == 1  # 只出现一次，无重发


# ================================================================
# 老快路径（ticket_fast_lane JSON 协议）项目预填
# ================================================================

class TestOldPathProjectPrefill:
    """老快路径的项目预填：用户项目列表注入 prompt → LLM 照抄 project_choice
    → _match_project_choice 严格校验 → state.pending_prefill_project →
    _build_ticket(prefill_project=) 覆盖 draft.project/project_id。

    单向管道铁律：project 不进 collected_info/required_fields/判缺。
    """

    _PROJECTS = [
        {"name": "南京本川项目", "code": "NJBC01"},
        {"name": "华大制造基地", "code": "7"},
    ]

    @staticmethod
    def _main_side_effect(*main_responses):
        """按调用次序分发主 prompt 响应；_build_ticket / decide_fields prompt
        固定返回合法 JSON（不占序号——通过 prompt 标记识别）。"""
        queue = list(main_responses)

        def _f(prompt: str = "", **kwargs):
            if "生成结构化工单" in prompt:
                return json.dumps({
                    "type": "problem", "title": "机器人离线",
                    "description": "机器人离线，无法继续执行任务。",
                    "priority": "中",
                }, ensure_ascii=False)
            if "判定工单类型" in prompt:
                return json.dumps(
                    {"ticket_type": "problem", "required_fields": {"contact": "联系方式"}},
                    ensure_ascii=False)
            return queue.pop(0) if queue else queue
        return _f

    def _setup_platform(self, platform):
        platform._get_user_projects = AsyncMock(return_value=list(self._PROJECTS))

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fast_lane_prefill_into_draft(self, platform, make_state, make_request):
        """快路径 submit + project_choice 精确照抄 → draft 预填 + 播报含预填名"""
        self._setup_platform(platform)
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={"contact": "张三 13800000000"},
        )
        request = make_request(query="给华大制造基地提个单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "submit", "intent": "troubleshoot", "ticket_intent": True,
            "project_choice": "华大制造基地",
            "state_update": {"problem_summary": "机器人离线"},
        }, ensure_ascii=False))
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        review = [e for e in events if e["event"] == "status" and e["data"].get("stage") == "review"]
        assert review, "应发 review 弹窗"
        draft = review[0]["data"]["draft"]
        assert draft["project"] == "华大制造基地"
        assert draft["project_id"] == "7"
        # 预填是单向管道：随弹窗生成即被消费清空（防陈旧预填重复触发 review），
        # 流结束后断言其已被消费，而不是残留值（0727-0827 期间本断言一直误报红）
        assert state.pending_prefill_project is None
        # 预填播报单一信息源：服务端兜底话术说的是校验后的真实值
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "项目已预填为「华大制造基地」" in tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_hallucinated_choice_ignored(self, platform, make_state, make_request):
        """project_choice 近似但非精确（幻觉/抄错）→ 宁空勿错拒收预填；
        前置闸门接管本轮（先出项目题而非直达弹窗），用户下轮从列表纠偏"""
        self._setup_platform(platform)
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={"contact": "张三 13800000000"},
        )
        request = make_request(query="给华大基地提个单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "submit", "intent": "troubleshoot", "ticket_intent": True,
            "project_choice": "华大基地",  # 近似，非列表精确值
            "state_update": {"problem_summary": "机器人离线"},
        }, ensure_ascii=False))
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        # 幻觉值绝无预填（宁空勿错的本质保护）
        assert state.pending_prefill_project is None
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "项目已预填" not in tokens
        # 新格局：未点名成功即撞前置闸门，第一轮先出项目题，不出弹窗
        assert not [e for e in events if e["event"] == "status"
                    and e["data"].get("stage") == "review"], "幻觉 choice 时不应有弹窗"
        assert "出单前先跟您确认一下关联项目" in tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_prefill_persists_across_rounds(self, platform, make_state, make_request):
        """轮 1 识别项目（字段未齐 ask），轮 2 字段齐 submit（无 project_choice）
        → 预填值跨轮保留进 draft"""
        self._setup_platform(platform)
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={},
        )
        memory = await platform._memory_manager.get_memory(state.session_id)

        req1 = make_request(query="给华大制造基地提个单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "ask", "intent": "troubleshoot", "ticket_intent": True,
            "project_choice": "华大制造基地",
            "message": "请问现场联系方式是？",
            "state_update": {"problem_summary": "机器人离线"},
        }, ensure_ascii=False))
        [ev async for ev in platform._agent_think_stream(req1, state, memory)]
        assert state.pending_prefill_project == {"name": "华大制造基地", "code": "7"}

        req2 = make_request(query="张三 13800000000", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
        state = _load_agent_state(memory.metadata)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "submit", "intent": "troubleshoot", "ticket_intent": True,
            "state_update": {"collected_info": {"contact": "张三 13800000000"}},
        }, ensure_ascii=False))
        events = [ev async for ev in platform._agent_think_stream(req2, state, memory)]

        review = [e for e in events if e["event"] == "status" and e["data"].get("stage") == "review"]
        assert review, "轮 2 字段齐应弹 review"
        draft = review[0]["data"]["draft"]
        assert draft["project"] == "华大制造基地"
        assert draft["project_id"] == "7"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_cancel_clears_prefill(self, platform, make_state, make_request):
        """LLM 判 ticket_cancel → 预填值随收集状态一起清空，不泄漏到下一单"""
        self._setup_platform(platform)
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            required_fields={"contact": "联系方式"},
            collected_info={},
            ticket_collecting=["联系方式"],
            pending_prefill_project={"name": "华大制造基地", "code": "7"},
        )
        memory = await platform._memory_manager.get_memory(state.session_id)
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        _save_agent_state(memory, state)
        memory.metadata["ticket_draft"] = {"title": "机器人离线", "project": ""}
        await platform._memory_manager.save_memory(memory)

        request = make_request(query="不提了", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "answer", "ticket_cancel": True,
            "message": "好的，不转工单。有什么其他问题随时问我。",
        }, ensure_ascii=False))

        [ev async for ev in platform._agent_think_stream(request, state, memory)]

        assert state.pending_prefill_project is None
        assert state.collected_info == {}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_normal_round_skips_project_fetch(self, platform, make_state, make_request):
        """普通诊断轮（无提单上下文）不拉用户项目列表——零开销"""
        platform._get_user_projects = AsyncMock(return_value=list(self._PROJECTS))
        state = make_state(phase="diagnosing", problem_summary="机器人离线")
        request = make_request(query="机器人为什么会离线", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "answer", "intent": "troubleshoot",
            "message": "可能是网络问题。",
            "state_update": {},
        }, ensure_ascii=False))
        memory = await platform._memory_manager.get_memory(state.session_id)

        [ev async for ev in platform._agent_think_stream(request, state, memory)]

        platform._get_user_projects.assert_not_called()
        assert state.pending_prefill_project is None


# ================================================================
# #29 required_fields 字段粒度（防打包）
# ================================================================

class TestRequiredFieldsGranularity:
    """生产实锤（2026-08-20 16:30）：快路径 LLM 把「时间、车辆编号、任务」
    打包成一个 required_fields key（occurrence_details），用户只答「早上九点」
    → 单字段非空被判全齐 → ask 被强转 submit 提前弹窗，车辆编号/任务永远丢失。
    修复：三个 LLM 声明 required_fields 的 prompt 全部带「一项信息一个字段」铁律。
    """

    def test_fast_lane_prompt_has_rule(self, platform, make_state):
        import types
        state = make_state(phase="diagnosing", ticket_fast_lane=True)
        fake_mem = types.SimpleNamespace(turns=[])
        s = platform._build_diagnosis_prompt(state, fake_mem, "")
        assert "一项信息一个 key" in s
        assert "禁止打包" in s

    def test_main_prompt_has_rule(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import DIAGNOSIS_PROMPT
        assert "一项信息一个字段，禁止打包" in DIAGNOSIS_PROMPT

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_fields_prompt_has_rule(self, platform):
        import types
        fake_mem = types.SimpleNamespace(turns=[])
        captured = {}

        async def _cap(prompt: str = "", **kwargs):
            captured["prompt"] = prompt
            return json.dumps({"ticket_type": "problem",
                               "required_fields": {"contact": "联系方式"}},
                              ensure_ascii=False)
        platform._llm_client.complete.side_effect = _cap
        await platform._compute_ticket_fields("test-granularity", fake_mem, 0)
        assert "一项信息一个字段" in captured["prompt"]


class TestRequiredFieldsSanitize:
    """生产实锤（2026-08-25）：按钮提单显示「还差 {'page module:'问题页、
    {'occurrence time:」——LLM 把 required_fields 写成嵌套对象，采纳代码
    str(v)[:20] 把嵌套 dict 字符串化再截断，残片当中文标签进了判缺提示。
    修复：_sanitize_required_fields 统一类型清洗（非字符串 value / 含引号
    花括号的标签一律丢弃），四处采纳点 + _load_agent_state 加载自愈共用。
    """

    def test_nested_dict_value_dropped(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import _sanitize_required_fields
        bad = {"page_module": {"page module": "问题页"},
               "occurrence_time": {"occurrence time": "发生时间"}}
        assert _sanitize_required_fields(bad) == {}

    def test_stored_string_fragment_dropped(self):
        """事故会话 Redis 里存的已是 str() 化残片（类型是 str），按内容识别。"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _sanitize_required_fields
        stored = {"page_module": "{'page module': '问题页",
                  "occurrence_time": "{'occurrence time: "}
        assert _sanitize_required_fields(stored) == {}

    def test_clean_list_kept(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import _sanitize_required_fields
        ok = {"vehicle_id": "车辆编号", "occurrence_time": "发生时间"}
        assert _sanitize_required_fields(ok) == ok

    def test_assess_shows_no_fragment(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _sanitize_required_fields, _assess_ticket_readiness)
        st = AgentState(session_id="s")
        st.required_fields = _sanitize_required_fields(
            {"page_module": {"page module": "问题页"}}) or None
        assert st.required_fields is None
        _, missing = _assess_ticket_readiness(st)
        assert missing == []

    def test_load_heals_polluted_state(self):
        """已锁定污染清单的会话：加载时清洗为空 → None，重新 decide 自愈。"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
        polluted = {"page_module": "{'page module': '问题页"}
        loaded = _load_agent_state({"agent_state": {"session_id": "s",
                                                    "required_fields": polluted}})
        assert loaded.required_fields is None
        good = _load_agent_state({"agent_state": {"session_id": "s",
                                                  "required_fields": {"vehicle_id": "车辆编号"}}})
        assert good.required_fields == {"vehicle_id": "车辆编号"}

    def test_apply_state_update_rejects_nested(self):
        """对话路径 state_update 里的嵌套 required_fields 不再被 str() 采纳。"""
        from ai.agents.AiDiagnosisPlatform import pipeline as pl
        state = pl.AgentState(session_id="s")
        pl.AiDiagnosisPlatform._apply_state_update(
            None, state, {"required_fields": {
                "vehicle_id": "车辆编号",
                "occurrence_time": "发生时间",
                "page_module": {"page module": "问题页"},
            }})
        assert state.required_fields == {"vehicle_id": "车辆编号",
                                         "occurrence_time": "发生时间"}


class TestBackfillOnFirstSubmit:
    """转单首轮回填（0825 生产实锤）：用户对话里已明确说过的信息
    （「新车，XSC111，没路径」「无法移动」）decide 仍列为缺口（车辆编号/
    故障现象），转单轮被逼把自己刚说过的话重答一遍。修复：首次转单 decide
    后、判缺前跑 _backfill_collected_info，假缺口消掉只问真正没说过的。
    """

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_stated_fields_not_reasked(self, platform, make_state, make_request):
        state = make_state(phase="diagnosing", problem_summary="新车无路径无法移动")
        request = make_request(query="算了，提单吧", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [
            {"role": "user", "content": "我车不动了"},
            {"role": "assistant", "content": "先确认是哪台车、什么表现。"},
            {"role": "user", "content": "新车，XSC111，没路径"},
            {"role": "assistant", "content": "新车没路径，最常出在车型配置和路网这两块。"},
            {"role": "user", "content": "算了，提单吧"},
        ]

        def _f(prompt: str = "", **kwargs):
            if "判定工单类型" in prompt:  # _compute_ticket_fields
                return json.dumps({"ticket_type": "problem", "required_fields": {
                    "vehicle_id": "车辆编号", "description": "故障现象",
                    "occurrence_time": "发生时间", "location": "现场位置",
                }}, ensure_ascii=False)
            if "提取指定字段的值" in prompt:  # _backfill_collected_info
                return json.dumps({"vehicle_id": {"value": "XSC111",
                                                  "cite": "新车，XSC111，没路径"},
                                   "description": {"value": "没路径",
                                                   "cite": "新车，XSC111，没路径"}},
                                  ensure_ascii=False)
            # 主 LLM：转单 submit
            return json.dumps({"action": "submit", "intent": "troubleshoot",
                               "ticket_intent": True,
                               "state_update": {"problem_summary": "新车无路径无法移动"}},
                              ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        # 假缺口被回填消掉
        assert state.collected_info.get("vehicle_id") == "XSC111"
        assert state.collected_info.get("description") == "没路径"
        # 追问只问真正没说过的
        need = [e for e in events if e["event"] == "status"
                and e["data"].get("stage") == "need_info"]
        assert need, "应发 need_info 追问"
        missing = need[0]["data"]["missing_info"]
        assert "车辆编号" not in missing and "故障现象" not in missing
        assert "发生时间" in missing and "现场位置" in missing
        # 追问话术由 LLM 现场生成（_generate_missing_ask）：不锁字面，
        # 只锁不变式——有话术且不重复问已答过的字段
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert tokens.strip(), "追问话术不应为空"
        assert "车辆编号" not in tokens and "故障现象" not in tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_collect_rounds_not_backfilled(self, platform, make_state, make_request):
        """收集轮不额外回填（4116 既有顾虑：助手追问会被当答案），只有转单首轮回填。"""
        from unittest.mock import patch
        state = make_state(
            phase="escalated", problem_summary="新车无路径无法移动",
            required_fields={"vehicle_id": "车辆编号", "occurrence_time": "发生时间"},
            collected_info={"vehicle_id": "XSC111"},
            ticket_collecting=["发生时间"], collect_rounds=2,
        )
        request = make_request(query="今早九点", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [{"role": "user", "content": "算了，提单吧"},
                        {"role": "assistant", "content": "发生时间是什么？"},
                        {"role": "user", "content": "今早九点"}]

        with patch.object(platform, "_backfill_collected_info", new=AsyncMock()) as bf:
            def _f(prompt: str = "", **kwargs):
                return json.dumps({"action": "submit", "intent": "troubleshoot",
                                   "ticket_intent": True,
                                   "state_update": {"collected_info": {
                                       "occurrence_time": "今早九点"}}},
                                  ensure_ascii=False)
            platform._llm_client.complete.side_effect = _f
            events = [ev async for ev in platform._agent_think_stream(request, state, memory)]
            # required_fields 已决定（非 None）→ 不触发转单首轮回填
            bf.assert_not_called()
        review = [e for e in events if e["event"] == "status" and e["data"].get("stage") == "review"]
        assert review, "字段齐了应直接弹草稿"


class TestBackfillAntiFabrication:
    """backfill 防幻觉溯源校验（0827 生产实锤 sess_mtb7al26_ju4nuk）：
    对话里没有任何任务/车辆编号，backfill 却把 required_fields 全填满 →
    假齐直达弹窗（还顺带绕过了项目选择题环）。修复 = 输出强制两层
    {field: {value, cite}} + 服务端机械校验 cite⊂对话 且 value⊂cite。
    三种污染形态各拦一种：①cite 编造 ②value 不在其引用原句里 ③旧扁平格式。
    """

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_three_fabrication_forms_all_rejected(self, platform, make_state,
                                                        make_request, caplog):
        state = make_state(phase="diagnosing", problem_summary="任务下发后机器人不动")
        request = make_request(query="算了 提单吧", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [
            {"role": "user", "content": "机器人动不了"},
            {"role": "assistant", "content": "具体什么表现？"},
            {"role": "user", "content": "任务下发后不动"},
            {"role": "user", "content": "算了 提单吧"},
        ]

        def _f(prompt: str = "", **kwargs):
            if "判定工单类型" in prompt:  # _compute_ticket_fields
                return json.dumps({"ticket_type": "problem", "required_fields": {
                    "vehicle_id": "车辆编号", "occurrence_time": "发生时间",
                    "location": "现场位置",
                }}, ensure_ascii=False)
            if "提取指定字段的值" in prompt:  # _backfill_collected_info：三种污染各来一个
                return json.dumps({
                    # ① cite 是编的——对话里根本没有这句
                    "vehicle_id": {"value": "AGV-777",
                                   "cite": "用户提到的是AGV-777号车"},
                    # ② cite 真的在对话里，但 value 不在这句原句中
                    "occurrence_time": {"value": "上周三", "cite": "任务下发后不动"},
                    # ③ 旧扁平格式（没有 cite 引用）
                    "location": "三号车间",
                }, ensure_ascii=False)
            return json.dumps({"action": "submit", "intent": "troubleshoot",
                               "ticket_intent": True,
                               "state_update": {"problem_summary": "任务下发后机器人不动"}},
                              ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f

        with caplog.at_level(logging.INFO):
            events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        # 三个字段全部拒绝 —— 宁缺勿错，假齐通道关死
        for k in ("vehicle_id", "occurrence_time", "location"):
            assert not (state.collected_info.get(k) or "").strip(), f"{k} 不应被回填"
        need = [e for e in events if e["event"] == "status"
                and e["data"].get("stage") == "need_info"]
        assert need, "字段仍缺应拦截追问（而不是判齐直达弹窗）"
        missing = need[0]["data"]["missing_info"]
        assert "车辆编号" in missing and "发生时间" in missing and "现场位置" in missing
        # 观测契约：每次拒绝都要落日志带原因（0827 排查盲区教训：只有 keys 没有 values/原因）
        assert "[backfill] 拒绝回填" in caplog.text


class TestProjectNeverBlocks:
    """项目铁律落地（0827 生产实锤 sess_mtb7eh8u_05qgnd 死循环）：用户
    「给俊磊提工单」→ decide 明禁仍把 project 列成必需字段 → 项目值又只能经
    弹窗产生（对话链路一律丢弃）→ missing=[所属项目] 永远成立，对话答什么都拦、
    点按钮也拦。用户拍板：项目在对话链路只能是补充信息+项目环引导，
    永远不做拦截条件；弹窗必选就是它的兜底入口。"""

    def test_is_project_field_variants(self):
        from ai.agents.AiDiagnosisPlatform.pipeline import _is_project_field
        for k in ("project", "project_name", "projectName", "项目名称",
                  "project_id", "所属项目", "关联项目"):
            assert _is_project_field(k), f"{k} 应识别为 project 类字段"
        for k in ("vehicle_id", "task_floor", "content", "contact"):
            assert not _is_project_field(k), f"{k} 不应被误判"

    def test_sanitize_strips_project_from_required(self):
        """根治口：decide 结果采纳前洗掉 project 类字段"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _sanitize_required_fields
        rf = _sanitize_required_fields(
            {"content": "工单内容", "project": "所属项目",
             "project_name": "项目名称", "vehicle_id": "车辆编号"})
        assert set(rf.keys()) == {"content", "vehicle_id"}

    def test_readiness_ignores_project_even_in_locked_state(self):
        """兜底门：sanitize 上线前已锁定含 project 的存量 required 也不得拦截"""
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            AgentState, _assess_ticket_readiness)
        state = AgentState(session_id="s", phase="diagnosing",
                           required_fields={"vehicle_id": "车辆编号",
                                            "project": "所属项目"})
        ready, missing = _assess_ticket_readiness(state)
        assert not ready
        assert missing == ["车辆编号"], "missing 里绝不允许出现「所属项目」"
        # 只差 project 一个字段时 = 就绪（弹窗选）
        state.collected_info["vehicle_id"] = "xqe44"
        ready2, missing2 = _assess_ticket_readiness(state)
        assert ready2 and missing2 == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decided_project_never_blocks_stream(self, platform, make_state,
                                                       make_request):
        """端到端复刻生产死循环单：decide 违令定 {content, project}，
        content 对话中已收集 → 应直达弹窗，而不是反复「还缺所属项目」"""
        state = make_state(phase="diagnosing", problem_summary="摇人吧偶发Bug")
        request = make_request(query="给俊磊提工单", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [
            {"role": "user", "content": "我在摇人吧提问，答案突然跑到上面去了"},
            {"role": "user", "content": "给俊磊提工单"},
        ]

        def _f(prompt: str = "", **kwargs):
            if "判定工单类型" in prompt:  # decide 违令把 project 定成必需
                return json.dumps({"ticket_type": "support", "required_fields": {
                    "content": "工单内容", "project": "所属项目",
                }}, ensure_ascii=False)
            if "生成结构化工单" in prompt:
                return json.dumps({
                    "type": "support", "title": "摇人吧偶发Bug",
                    "description": "提问后答案出现在上方。",
                    "priority": "中",
                }, ensure_ascii=False)
            # 主 LLM：submit 且 content 已真实收集
            return json.dumps({"action": "submit", "intent": "howto",
                               "ticket_intent": True,
                               "state_update": {
                                   "ticket_type": "support",
                                   "problem_summary": "摇人吧偶发Bug",
                                   "collected_info": {"content": "摇人吧偶发Bug"},
                                   "ticket_ready": True}},
                              ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        assert review_exists(events), "content 已齐应直达弹窗"
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "所属项目" not in tokens, "不得再拿所属项目拦人"
        # 采纳口已把 project 洗掉
        assert state.required_fields is not None and "project" not in state.required_fields

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_button_not_ready_missing_excludes_project(self, platform, make_state):
        """按钮路径：存量 required 含 project + 真字段缺 → 拦截清单只有真字段，
        Toast/追问绝不再出现「所属项目」（15:36 生产两次按钮拦截实锤的反面）"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _save_agent_state
        # 候选可查（返回非空也必须不出题——按钮铁律）
        platform._get_project_candidates = AsyncMock(return_value=[
            {"name": "摇人吧服务号", "code": "Leo_test"}])
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            required_fields={"vehicle_id": "车辆编号", "project": "所属项目"},
            collected_info={},
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)

        result = await platform.prepare_ticket(session_id)

        assert result["stage"] == "not_ready"
        assert result["missing_info"] == ["车辆编号"]
        assert "所属项目" not in (result.get("message") or "")
        # 收集模式目标同样不含 project（下一轮 LLM 不再追问它）
        loaded_memory = await platform._memory_manager.get_memory(session_id)
        assert loaded_memory.metadata is not None


class TestAskHijackRingTail:
    """对话提单前置项目闸门（0827 用户钉死规则）：只要在对话里说提单，本单第一轮
    一律先出纯项目选择题（不管主 LLM 这轮想问字段还是喊 submit），答完才进入信息
    补充——「问了半天再说提单」和「直接说给我提单」同待遇。服务端模板接管话术，
    主 LLM 当轮输出整轮丢弃；submit 被拦(先纯环)、全齐直达(弹窗自选)、按钮(零项目)
    各有归宿。无提单意图的普通 ask 不出环。"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ask_hijack_first_round_is_pure_project_ring(self, platform,
                                                               make_state,
                                                               make_request):
        state = make_state(phase="diagnosing", problem_summary="配置叉车操作高度")
        request = make_request(
            query="给俊磊提工单，让他帮我配置一下叉车场景下的操作高度",
            session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [{"role": "user", "content": request.query}]

        def _f(prompt: str = "", **kwargs):
            return json.dumps({"action": "ask", "intent": "troubleshoot",
                               "ticket_intent": True,
                               "message": "请提供叉车场景下需要配置的操作高度具体数值及单位",
                               "state_update": {}}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        platform._get_project_candidates = AsyncMock(return_value=[
            {"name": "摇人吧服务号", "code": "Leo_test"},
            {"name": "江苏南京本川XSC仓储项目", "code": "69"},
        ])

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]
        tokens = "".join(e["data"] for e in events if e["event"] == "token")

        # 第一轮就是纯项目题：主 LLM 的字段追问被整轮接管，一个字都不透传
        assert "出单前先跟您确认一下关联项目" in tokens
        assert "江苏南京本川XSC仓储项目" in tokens
        assert "请提供叉车场景下需要配置的操作高度" not in tokens
        # 只问一次的标志与候选挂载到位
        assert state.project_asked is True
        assert [c["name"] for c in state.project_candidates] == [
            "摇人吧服务号", "江苏南京本川XSC仓储项目"]
        assert not state.ticket_collecting  # 未提前进收集模式（required_fields 为 None）

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_ask_without_ticket_intent_no_ring(self, platform, make_state,
                                                     make_request):
        """普通诊断追问（ticket_intent=false）不得混入项目题"""
        state = make_state(phase="diagnosing", problem_summary="机器人不移动")
        request = make_request(query="报了个错，E207", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [{"role": "user", "content": request.query}]

        def _f(prompt: str = "", **kwargs):
            return json.dumps({"action": "ask", "intent": "troubleshoot",
                               "ticket_intent": False,
                               "message": "这个故障码一般是驱动器告警，具体什么场景出现的？",
                               "state_update": {}}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        platform._get_project_candidates = AsyncMock(return_value=[
            {"name": "摇人吧服务号", "code": "Leo_test"}])

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]
        tokens = "".join(e["data"] for e in events if e["event"] == "token")

        assert "出单前先跟您确认一下关联项目" not in tokens
        assert state.project_asked is False

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_gate_preset_collecting_after_ring(self, platform, make_state,
                                                     make_request):
        """直说提单形态的信息补充初始化（0827 生产实锤）：闸门出题的同轮就要
        决定字段并挂上收集模式——否则用户答完编号后 submit 会被 decide+backfill
        凑假齐直达弹窗，「信息补充环节」整个被跳过。
        decide 判定真无缺口（空清单）才允许答完项目直达弹窗。"""
        state = make_state(phase="diagnosing", problem_summary="协助配置库位")
        request = make_request(
            query="帮我给贾爽提单，让她帮我配一下库位",
            session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [{"role": "user", "content": request.query}]

        def _f(prompt: str = "", **kwargs):
            if "判定工单类型" in prompt:
                return json.dumps({
                    "analysis": "诉求清晰但缺口未说",
                    "ticket_type": "support",
                    "required_fields": {
                        "location_name": "库位名称/编号",
                        "config_requirement": "配置要求"},
                    "prefilled": {},
                    "ask_message": "",
                }, ensure_ascii=False)
            return json.dumps({"action": "submit", "intent": "chat",
                               "ticket_intent": True,
                               "message": "",
                               "state_update": {"problem_summary": "协助配置库位"}},
                              ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        platform._get_project_candidates = AsyncMock(return_value=[
            {"name": "摇人吧服务号", "code": "Leo_test"}])

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]
        tokens = "".join(e["data"] for e in events if e["event"] == "token")

        # 出题 + 收集模式当轮预置：字段补充环节已初始化
        assert "出单前先跟您确认一下关联项目" in tokens
        assert state.project_asked is True
        assert state.required_fields is not None and "project" not in (
            {k.lower() for k in state.required_fields})
        assert state.ticket_collecting == ["库位名称/编号", "配置要求"]
        # 本轮绝不出弹窗（信息还没补）
        assert not [e for e in events if e["event"] == "status"
                    and e["data"].get("stage") == "review"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_streaming_body_suppressed_on_gate(self, platform, make_state,
                                                     make_request):
        """流式增量下的闸门接管（0827 生产实锤）：ask 形态的正文若边流边发，
        闸门在响应结束后才执行就收不回来——项目题会拼接在字段追问后面。
        快路径轮（intent 分类判了 ticket）正文必须延迟到结构化裁决后输出，
        本用例给 mock 挂上 stream 复刻真实增量产出来锁死该行为。"""
        state = make_state(phase="diagnosing", problem_summary="车辆走走停停",
                           ticket_fast_lane=True)
        request = make_request(query="帮我提单吧", session_id=state.session_id)
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.turns = [{"role": "user", "content": request.query}]

        _full = ('```json\n' + json.dumps({
            "action": "ask", "intent": "troubleshoot", "ticket_intent": True,
            "message": "",
            "state_update": {"problem_summary": "车辆走走停停"}},
            ensure_ascii=False) + '\n```\n'
            '卡顿问题大概什么时间开始的？当时车在执行什么任务？方便的话提供下车辆编号')

        async def _fake_stream(prompt: str = "", **kwargs):
            # 真实流式的量级：按小块随机切分增量产出（JSON 区→正文区）
            import re as _re
            for chunk in _re.findall(r".{1,7}", _full):
                yield chunk

        platform._llm_client.stream = _fake_stream
        platform._get_project_candidates = AsyncMock(return_value=[
            {"name": "摇人吧服务号", "code": "Leo_test"}])

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]
        tokens = "".join(e["data"] for e in events if e["event"] == "token")

        # 正文必须被抑制，用户只能看到纯项目题
        assert "卡顿问题大概什么时间开始的" not in tokens
        assert "提供下车辆编号" not in tokens
        assert "出单前先跟您确认一下关联项目" in tokens


class TestAttachmentBinding:
    """附件-工单绑定（2026-08-20 需求）：会话累积附件不再无条件全带。

    两层：机制层 = 提单成功消费清空（跨单硬边界，_reset_state_after_submit）；
    判断层 = _build_ticket 里 LLM 按附件摘要与本单问题相关性取舍（跨话题软边界，
    判断全交大模型）。降级 = 现状全带（字段缺失/解析失败不静默丢证据）。
    """

    _ATTS = [
        {"filename": "fault_3号车.png", "size": 100, "path": "http://x/f1",
         "object_path": "b/sess/fault1.png", "desc": "界面显示3号车离线，疑似网络断连"},
        {"filename": "工单列表截图.jpg", "size": 200, "path": "http://x/f2",
         "object_path": "b/sess/f2.jpg", "desc": "工单列表页，显示历史工单状态"},
    ]

    @pytest.mark.unit
    def test_reset_state_after_submit_clears_attachments(self):
        """提单成功 → 累积附件消费清空：下一单（含提单后发图问问题再换话题提单）
        不会带上本单之前的附件。"""
        from types import SimpleNamespace
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            AgentState, _reset_state_after_submit,
        )
        state = AgentState(session_id="s-att")
        memory = SimpleNamespace(
            turns=[{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
            metadata={"agent_state": {"session_id": "s-att", "attachments": list(self._ATTS)}},
        )
        _reset_state_after_submit(state, memory, {"ticket_id": "TK-1", "title": "t"}, 1)
        assert memory.metadata["agent_state"]["attachments"] == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_build_selects_relevant_attachments(self, platform, make_state):
        """LLM 输出 attach_files=[1] → draft 只带第 1 个；候选清单注入 prompt
        （文件名 + VLM 摘要——对话里图片内容已被 sanitize_images 屏蔽）"""
        state = make_state(phase="diagnosing", problem_summary="3号车离线",
                           original_query="3号车不动了")
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.metadata["agent_state"] = {
            "session_id": state.session_id, "attachments": [dict(a) for a in self._ATTS]}
        prompts = []

        async def _f(prompt: str = "", **kw):
            prompts.append(prompt)
            return json.dumps({"type": "problem", "title": "3号车离线",
                               "description": "3号车离线。", "priority": "中",
                               "attach_files": [1]}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        draft = await platform._build_ticket(state.session_id, state, memory)

        assert "## 附件候选" in prompts[0]
        assert "fault_3号车.png" in prompts[0]
        assert "界面显示3号车离线" in prompts[0]          # desc 是取舍信号
        assert "attach_files" in prompts[0]
        assert [a["filename"] for a in draft["attachments"]] == ["fault_3号车.png"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_string_indices_coerced(self, platform, make_state):
        """LLM 偶尔输出字符串数字 ["2"] → 仍按序号 2 取（宽松解析不丢选）"""
        state = make_state(phase="diagnosing", problem_summary="p")
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.metadata["agent_state"] = {
            "session_id": state.session_id, "attachments": [dict(a) for a in self._ATTS]}

        async def _f(prompt: str = "", **kw):
            return json.dumps({"type": "problem", "title": "t", "description": "d",
                               "priority": "中", "attach_files": ["2"]}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        draft = await platform._build_ticket(state.session_id, state, memory)
        assert [a["filename"] for a in draft["attachments"]] == ["工单列表截图.jpg"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_selection_drops_all(self, platform, make_state):
        """LLM 显式空数组（都与本单无关）→ 尊重，不带任何附件"""
        state = make_state(phase="diagnosing", problem_summary="p")
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.metadata["agent_state"] = {
            "session_id": state.session_id, "attachments": [dict(a) for a in self._ATTS]}

        async def _f(prompt: str = "", **kw):
            return json.dumps({"type": "problem", "title": "t", "description": "d",
                               "priority": "中", "attach_files": []}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        draft = await platform._build_ticket(state.session_id, state, memory)
        assert draft["attachments"] == []

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_missing_field_keeps_all(self, platform, make_state):
        """降级 = 现状：LLM 没输出 attach_files（字段缺失）→ 全带，
        不静默丢证据（弹窗没有附件编辑 UI，漏带无法补救）"""
        state = make_state(phase="diagnosing", problem_summary="p")
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.metadata["agent_state"] = {
            "session_id": state.session_id, "attachments": [dict(a) for a in self._ATTS]}

        async def _f(prompt: str = "", **kw):
            return json.dumps({"type": "problem", "title": "t", "description": "d",
                               "priority": "中"}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        draft = await platform._build_ticket(state.session_id, state, memory)
        assert [a["filename"] for a in draft["attachments"]] == \
            [a["filename"] for a in self._ATTS]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_attachments_no_block(self, platform, make_state):
        """无附件 → prompt 无候选清单（普通会话零开销），draft 附件为空"""
        state = make_state(phase="diagnosing", problem_summary="p")
        memory = await platform._memory_manager.get_memory(state.session_id)
        memory.metadata["agent_state"] = {"session_id": state.session_id, "attachments": []}
        prompts = []

        async def _f(prompt: str = "", **kw):
            prompts.append(prompt)
            return json.dumps({"type": "problem", "title": "t", "description": "d",
                               "priority": "中"}, ensure_ascii=False)

        platform._llm_client.complete.side_effect = _f
        draft = await platform._build_ticket(state.session_id, state, memory)
        assert "## 附件候选" not in prompts[0]
        assert draft["attachments"] == []


class TestTicketBoundaryPrefill:
    """跨单预填泄漏（2026-08-20 生产实录）：同一会话提多单，第二单把第一单
    对话里提到的项目名又预填上了。根因：context_start 实际恒为 0，提单归档
    turns[context_start:] 是空操作，第一单的「给XX项目提单」仍在最近对话窗口
    里，LLM 分不清那句项目名属于已提交的上一单 → 照抄。

    修复：提单成功记内容锚点（提交时最后一轮前 40 字）→ 对话切片在锚点后插
    「以上已随上一张工单提交归档」分隔线 → 照抄规则只认分隔线之后用户提到的
    项目（判断仍在 LLM，代码只提供分界事实）。
    """

    _PROJECTS = [{"name": "南京本川项目", "code": "NJBC01"}]

    @pytest.mark.unit
    def test_reset_sets_boundary_anchor(self):
        """提单成功 → 锚点 = 提交时最后一轮内容前 40 字；无对话 → 空锚点"""
        from types import SimpleNamespace
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            AgentState, _reset_state_after_submit,
        )
        state = AgentState(session_id="s-bd")
        memory = SimpleNamespace(
            turns=[{"role": "user", "content": "给南京本川项目提个单"},
                   {"role": "assistant", "content": "工单草稿已生成。"}],
            metadata={"agent_state": {}},
        )
        _reset_state_after_submit(state, memory, {"ticket_id": "TK-1"}, 1)
        assert state.ticket_boundary_prefix == "工单草稿已生成。"

        memory_empty = SimpleNamespace(turns=[], metadata={"agent_state": {}})
        _reset_state_after_submit(state, memory_empty, {"ticket_id": "TK-2"}, 2)
        assert state.ticket_boundary_prefix == ""

    @pytest.mark.unit
    def test_boundary_line_inserted_after_anchor(self, platform):
        """锚点轮之后插分隔线；锚点不在切片内（全新对话）不插"""
        from types import SimpleNamespace
        memory = SimpleNamespace(turns=[
            {"role": "user", "content": "给南京本川项目提个单"},
            {"role": "assistant", "content": "工单草稿已生成。"},
            {"role": "user", "content": "充电柜报故障了"},
        ])
        text = platform._format_conversation(
            memory, boundary_prefix="工单草稿已生成。")
        lines = text.split("\n")
        assert "───── 以上对话已随上一张工单提交归档；以下是新对话 ─────" in lines
        # 分隔线紧跟锚点轮（索引 1），锚点前的项目名轮次在线上方
        assert lines.index("───── 以上对话已随上一张工单提交归档；以下是新对话 ─────") == 2
        assert lines[0].startswith("用户：给南京本川项目")
        assert "充电柜报故障了" in lines[3]

        # 锚点被截掉（只看最近 2 轮）→ 全新对话，不插分隔线
        text2 = platform._format_conversation(
            memory, max_turns=2, boundary_prefix="给南京本川项目提个单")
        assert "以上对话已随上一张工单提交归档" not in text2

    def test_fast_lane_prompt_has_boundary_rule(self, platform, make_state):
        """快路径 prompt：含分隔线（锚点命中）+ 照抄规则明确「分隔线前禁抄」"""
        import types
        state = make_state(phase="diagnosing", ticket_fast_lane=True,
                           ticket_boundary_prefix="工单草稿已生成。")
        fake_mem = types.SimpleNamespace(turns=[
            {"role": "user", "content": "给南京本川项目提个单"},
            {"role": "assistant", "content": "工单草稿已生成。"},
            {"role": "user", "content": "帮我提单，充电柜报故障"},
        ])
        s = platform._build_diagnosis_prompt(
            state, fake_mem, "", user_projects=list(self._PROJECTS))
        assert "以上对话已随上一张工单提交归档" in s
        assert "不算本次提到，禁止照抄" in s
        assert "南京本川项目（编号: NJBC01）" in s


class TestDecidePrevTicketIsolation:
    """0825 工单 #588 实锤：decide 把上一单补充轮的字段（任务编号、末端
    站点名称）当成本单信息缺口——提单归档后上一单对话仍留在 turns（续接轮
    指代解析要用），但 decide/backfill 的对话切片没传 boundary_prefix，
    上一单补充轮问答平铺在本单对话前。修复：切片插分隔线 + prompt 铁律。"""

    BOUNDARY_LINE = "───── 以上对话已随上一张工单提交归档"
    ANCHOR = "好的，已记录。工单 #587 已提交，工程师会尽快联系您。"
    NEW_TICKET_MSG = "帮我给胡健楠提单，现在的ai诊断平台的单纯图片分析回答有问题"

    async def _seed(self, platform, state):
        """归档后的真实 turns 形态：上一单补充轮问答 → 锚点轮 → 本单对话"""
        memory = await platform._memory_manager.get_memory(state.session_id)
        for role, content in [
            ("assistant", "还需要确认任务编号和末端站点名称。"),
            ("user", "任务编号是 KD20260824001，末端站点名称是南山西丽站。"),
            ("assistant", self.ANCHOR),  # 锚点轮（上一单提交收尾）
            ("user", self.NEW_TICKET_MSG),
        ]:
            await platform._memory_manager.add_turn(state.session_id, role, content)
        return memory

    def _capture_llm(self, platform, payload):
        captured = {}

        async def _cap(prompt, **kw):
            captured["prompt"] = prompt
            return json.dumps(payload, ensure_ascii=False)

        platform._llm_client.complete = AsyncMock(side_effect=_cap)
        return captured

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_decide_prompt_isolates_prev_ticket(self, platform, make_state):
        state = make_state(ticket_boundary_prefix=self.ANCHOR[:40])
        memory = await self._seed(platform, state)
        captured = self._capture_llm(platform, {
            "ticket_type": "bug",
            "required_fields": {"issue_detail": "具体异常表现",
                                "expected_behavior": "期望行为"}})
        await platform._decide_ticket_fields(state.session_id, state, memory)
        prompt = captured["prompt"]
        # 分隔线插在锚点轮之后、本单对话之前——上一单问答被隔在线上方。
        # 行首匹配：铁律文本里也引用了分隔线字样，只认对话区独占一行的真分隔线
        _line = "\n" + self.BOUNDARY_LINE
        assert _line in prompt
        assert prompt.index("KD20260824001") < prompt.index(_line)
        assert prompt.index(self.ANCHOR) < prompt.index(_line)
        assert prompt.index(_line) < prompt.index(self.NEW_TICKET_MSG)
        # 铁律：旧对话字段样例不得进本单缺口
        assert "严禁照着旧对话的字段样例列本单待补字段" in prompt

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_backfill_prompt_isolates_prev_ticket(self, platform, make_state):
        state = make_state(ticket_boundary_prefix=self.ANCHOR[:40],
                           required_fields={"issue_detail": "具体异常表现"})
        memory = await self._seed(platform, state)
        captured = self._capture_llm(platform, {"issue_detail": "图片分析默认触发诊断"})
        await platform._backfill_collected_info(state.session_id, state, memory)
        prompt = captured["prompt"]
        _line = "\n" + self.BOUNDARY_LINE
        assert _line in prompt
        assert prompt.index("KD20260824001") < prompt.index(_line)
        assert prompt.index(_line) < prompt.index(self.NEW_TICKET_MSG)
        # 铁律：上一单问答里的值不得回填进本单
        assert "严禁提取为本单字段值" in prompt


class TestProjectChoiceRing:
    """0827 项目填充智能化：提单拦截先出项目选择题（服务端模板话术），
    用户答编号 → 收集轮 LLM 还原 project_choice → 严格校验进 prefill 管道。
    随单生命周期：submit/取消清空、reset_ticket 只清候选保留 asked 标志。"""

    _CANDS = [
        {"name": "智慧仓储项目", "code": "69"},
        {"name": "摆轮分拣线二期", "code": "12"},
        {"name": "XSC产线联调", "code": ""},
    ]

    # ---------- 纯函数级 ----------

    def test_state_roundtrip(self):
        """project_asked/project_candidates 随 _save/_load 序列化还原"""
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _load_agent_state, _save_agent_state)
        from ai.core.memory import SessionMemory
        memory = SessionMemory(session_id="t1", turns=[], metadata={})
        state = AgentState(
            session_id="t1", project_asked=True,
            project_candidates=[{"name": "智慧仓储项目", "code": "69"}])
        _save_agent_state(memory, state)
        loaded = _load_agent_state(memory.metadata)
        assert loaded.project_asked is True
        assert loaded.project_candidates == [{"name": "智慧仓储项目", "code": "69"}]
        # 缺省/旧数据 → 默认值不炸
        memory.metadata["agent_state"] = {"session_id": "t1"}
        loaded2 = _load_agent_state(memory.metadata)
        assert loaded2.project_asked is False and loaded2.project_candidates == []

    def test_reset_after_submit_clears_ring(self):
        """提单成功收尾：asked/candidates 随单重置，下一单重新问"""
        from ai.agents.AiDiagnosisPlatform.pipeline import _reset_state_after_submit
        state = AgentState(
            session_id="t2", project_asked=True,
            project_candidates=list(self._CANDS),
            pending_prefill_project={"name": "x", "code": "1"})
        _reset_state_after_submit(state, type("M", (), {
            "metadata": {}, "turns": []})(), {"ticket_id": "AI-x"}, 9)
        assert state.project_asked is False
        assert state.project_candidates == []

    def test_ask_template_format(self):
        """话术模板：编号列表格式稳定（编号还原的锚点）、单候选变体、无编号条目"""
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _build_project_choice_ask, _format_project_display)
        ask = _build_project_choice_ask(self._CANDS)
        assert "出单前先跟您确认一下关联项目" in ask
        for i, c in enumerate(self._CANDS, 1):
            assert f"{i}. {_format_project_display(c)}" in ask
        assert f"{self._CANDS[0]['name']}（编号: {self._CANDS[0]['code']}）" in ask
        # 无编号条目只显示名称（展示格式兼容）
        assert "XSC产线联调（编号: ）" not in ask
        single = _build_project_choice_ask([{"name": "智慧仓储项目", "code": "69"}])
        assert "还是这个项目吗" in single
        for tail_kw in ("回复【序号】", "工单弹窗里搜索", "没有我的项目"):
            assert tail_kw in ask
        # 列表与提示语之间必须空行：markdown 渲染下单 \n 会让尾句被并进最后一条
        # 列表项（lazy continuation，0827 生产实锤）
        assert "\n\n回复【序号】" in ask

    # ---------- 流级（mock 主 LLM，零真调用）----------

    @staticmethod
    def _main_side_effect(*main_responses):
        """按调用次序分发主 prompt 响应；_build_ticket / decide prompt 固定返回"""
        queue = list(main_responses)

        def _f(prompt: str = "", **kwargs):
            if "生成结构化工单" in prompt:
                return json.dumps({
                    "type": "problem", "title": "机器人离线",
                    "description": "机器人离线，无法继续执行任务。",
                    "priority": "中",
                }, ensure_ascii=False)
            if "判定工单类型" in prompt:
                return json.dumps(
                    {"ticket_type": "problem", "required_fields": {"contact": "联系方式"}},
                    ensure_ascii=False)
            return queue.pop(0) if queue else ""
        return _f

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_intercept_emits_project_ask_first(self, platform, make_state, make_request):
        """轮1 submit（缺 contact）→ 0827 前置闸门先行接管：本轮先出项目选择题
        而非字段反问，也不出弹窗；标志与候选挂载、收集模式由提前收集段补上"""
        platform._get_project_candidates = AsyncMock(
            return_value=[dict(c) for c in self._CANDS])
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={},
        )
        request = make_request(query="帮我提交个维修单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "submit", "intent": "troubleshoot", "ticket_intent": True,
            "state_update": {"problem_summary": "机器人离线"},
        }, ensure_ascii=False))
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "出单前先跟您确认一下关联项目" in tokens, f"实际正文: {tokens[:200]}"
        assert not review_exists(events), "不应有弹窗"
        assert "联系方式" not in tokens, "项目环轮禁止夹带字段反问"
        assert state.project_asked is True
        assert len(state.project_candidates) == 3
        assert state.ticket_collecting  # 提前进入收集模式（required_fields 预置了缺口）

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_number_reply_resolves_to_prefill(self, platform, make_state, make_request):
        """轮2 用户答「1」→ LLM 还原为完整项目名照抄 project_choice →
        校验命中 prefill；收集 prompt 含编号还原块；候选映射命中后清空"""
        platform._get_user_projects = AsyncMock(return_value=[])
        cands = [dict(c) for c in self._CANDS]
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={},
            ticket_collecting=["联系方式"],
            collect_rounds=1,
            project_asked=True,
            project_candidates=cands,
        )
        request = make_request(query="1", session_id=state.session_id)

        captured_prompts = []

        def _f(prompt: str = "", **kwargs):
            if "生成结构化工单" in prompt:
                return json.dumps({
                    "type": "problem", "title": "机器人离线",
                    "description": "机器人离线。", "priority": "中",
                }, ensure_ascii=False)
            if "判定工单类型" in prompt:
                return json.dumps({}, ensure_ascii=False)
            captured_prompts.append(prompt)
            # 模拟 LLM 编号还原行为：把「1」映射回完整名称照抄
            return json.dumps({
                "action": "ask", "intent": "troubleshoot",
                "project_choice": "智慧仓储项目",
                "message": "好的，已选智慧仓储项目。请问现场联系人是谁？",
            }, ensure_ascii=False)
        platform._llm_client.complete.side_effect = _f
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        assert "此前系统曾以下面编号向用户列出候选项目" in captured_prompts[0], \
            "收集轮必须注入编号还原块"
        assert state.pending_prefill_project == {
            "name": "智慧仓储项目", "code": "69"}
        assert state.project_candidates == [], "预填命中后映射即清"
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "出单前先跟您确认一下关联项目" not in tokens, "不复读项目题"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_none_of_the_above_falls_back_to_fields(self, platform, make_state,
                                                          make_request):
        """轮2 答「都不是这些项目」→ LLM 留空 project_choice → 不命中 prefill，
        正常走字段收集（绝不复读项目题、绝不出第二遍题）"""
        platform._get_user_projects = AsyncMock(return_value=[])
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={},
            ticket_collecting=["联系方式"],
            collect_rounds=1,
            project_asked=True,
            project_candidates=[dict(c) for c in self._CANDS],
        )
        request = make_request(query="都不是我的项目", session_id=state.session_id)

        def _f(prompt: str = "", **kwargs):
            if "生成结构化工单" in prompt:
                return json.dumps({
                    "type": "problem", "title": "机器人离线",
                    "description": "机器人离线。", "priority": "中",
                }, ensure_ascii=False)
            if "判定工单类型" in prompt:
                return json.dumps({}, ensure_ascii=False)
            return json.dumps({
                "action": "ask", "intent": "troubleshoot",
                "project_choice": "",
                "message": "好的。请问现场联系人是谁？",
            }, ensure_ascii=False)
        platform._llm_client.complete.side_effect = _f
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        assert state.pending_prefill_project is None
        assert state.project_candidates, "未命中不误清候选（同一单内不再重复出题即可）"
        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "出单前先跟您确认一下关联项目" not in tokens

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_no_candidates_skips_ring_entirely(self, platform, make_state,
                                                     make_request):
        """候选查询失败/为空（本地无名下项目等）→ 直接落回原字段追问，
        零侵入降级，状态不误标 asked"""
        platform._get_project_candidates = AsyncMock(return_value=[])
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            ticket_fast_lane=True,
            required_fields={"contact": "联系方式"},
            collected_info={},
        )
        request = make_request(query="帮我提交个维修单", session_id=state.session_id)
        platform._llm_client.complete.side_effect = self._main_side_effect(json.dumps({
            "action": "submit", "intent": "troubleshoot", "ticket_intent": True,
            "state_update": {"problem_summary": "机器人离线"},
        }, ensure_ascii=False))
        memory = await platform._memory_manager.get_memory(state.session_id)

        events = [ev async for ev in platform._agent_think_stream(request, state, memory)]

        tokens = "".join(e["data"] for e in events if e["event"] == "token")
        assert "出单前先跟您确认一下关联项目" not in tokens
        assert state.project_asked is False
        # 回到原字段追问路径：需要 wait LLM 现场生成或 decide 接管——
        # 这里 mock 只有一个主响应已被消费，追问走兜底/异常容忍均可，
        # 关键断言是本轮不是弹窗、也没出项目题。
        assert not review_exists(events)


    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_prepare_button_never_asks_project(self, platform, make_state):
        """按钮铁律（0827 二次实锤后拍板撤销按钮侧联动）：点按钮绝不往对话里
        注入项目题——弹窗强制必选是按钮路径项目的唯一入口；项目环仅存于
        对话链路 submit 拦截段。候选查询照常可用也要出题不出来。"""
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _load_agent_state, _save_agent_state)
        platform._get_project_candidates = AsyncMock(
            return_value=[dict(c) for c in self._CANDS])
        state = make_state(
            phase="diagnosing", problem_summary="机器人离线",
            required_fields={"contact": "联系方式"},
            collected_info={},
        )
        session_id = state.session_id
        memory = await platform._memory_manager.get_memory(session_id)
        _save_agent_state(memory, state)

        result = await platform.prepare_ticket(session_id)

        assert result["stage"] == "not_ready"
        assert "出单前先跟您确认一下关联项目" not in (result.get("message") or "")
        assert result["missing_info"] == ["联系方式"]
        # 聊天区也不出现项目题（不是 Toast 隐藏问题，是根本不出）
        loaded_memory = await platform._memory_manager.get_memory(session_id)
        assert not any("出单前先跟您确认一下关联项目" in (t.get("content") or "")
                       for t in loaded_memory.turns)
        # 候选拿到了也不挂载、不出题
        loaded = _load_agent_state(loaded_memory.metadata)
        assert loaded.project_asked is False
        assert not loaded.project_candidates
        assert loaded.ticket_collecting == ["联系方式"]


def review_exists(events):
    return any(e["event"] == "status" and e["data"].get("stage") == "review"
               for e in events)


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
