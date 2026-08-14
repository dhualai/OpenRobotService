"""今日提单链路冒烟测试 — 2026-08-13 改动后

覆盖今天的核心改动：
1. 意图分类三分类（ticket 快路径）
2. 快路径：提单轮跳过检索 + 精简 prompt + LLM 复核
3. 收集模式：LLM ask 时不回填（防「提问里的词当答案」幻觉）
4. submit message 留空：解析器不塞兜底文案
5. required_fields 三态（None=未决定 / {} =已决定无需补）
6. project 不进对话链路（_build_ticket 输出 project=""）
"""
import json
import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import AgentState


class TestTicketFastLane:
    """意图分类判 ticket → 快路径"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_intent_classify_ticket(self, platform, make_state, make_request):
        """「帮我转工单」→ 意图分类应返回 ticket（mock 分类器）"""
        # 先清掉 conftest 里设置的 side_effect，再设 return_value
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = "ticket"
        result = await platform._classify_intent(
            platform._llm_client, "帮我转工单", "帮我转工单",
            context_turns=[],
        )
        assert result == "ticket"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_intent_classify_consultation_not_ticket(self, platform, make_state, make_request):
        """「工单流转流程是怎样的？」→ 不应判 ticket（流程咨询不是提单诉求）"""
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = "diagnosis"
        result = await platform._classify_intent(
            platform._llm_client, "工单流转流程是怎样的？", "工单流转流程是怎样的？",
            context_turns=[],
        )
        assert result == "diagnosis"


class TestSubmitEmptyMessage:
    """submit message 留空：解析器行为"""

    @pytest.mark.unit
    async def test_parse_submit_empty_message(self, platform):
        """submit + 纯 JSON 无正文 → message 应为空，不塞「抱歉」兜底"""
        raw = (
            '```json\n'
            + json.dumps({
                "action": "submit",
                "intent": "troubleshoot",
                "ticket_cancel": False,
                "state_update": {"collected_info": {"vehicle_id": "Xp1"}, "ticket_ready": True},
            }, ensure_ascii=False)
            + '\n```'
        )
        parsed = platform._parse_agent_output(raw)
        assert parsed["action"] == "submit"
        assert parsed["message"] == ""


class TestBackfillNoHallucination:
    """收集模式：LLM ask 时不回填（防提问里的词当答案）"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_backfill_not_called_when_llm_asks(self, platform, make_state, make_request, monkeypatch):
        """LLM 还在 ask 时，服务端不应把提问内容当答案回填"""
        state = make_state(phase="diagnosing", problem_summary="车动不了")
        state.required_fields = {"vehicle_id": "车辆编号"}
        request = make_request(query="Xp1")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = json.dumps({
            "action": "ask",
            "intent": "troubleshoot",
            "state_update": {"collected_info": {}},
        }, ensure_ascii=False)
        memory = await platform._memory_manager.get_memory(request.session_id)
        # 用 monkeypatch 记录 backfill 调用次数
        calls = []
        orig_backfill = platform._backfill_collected_info

        async def spy_backfill(session_id, agent_state, mem):
            calls.append(session_id)
            return await orig_backfill(session_id, agent_state, mem)

        monkeypatch.setattr(platform, "_backfill_collected_info", spy_backfill)
        await platform._agent_think(request, state, memory)
        # 关键断言：LLM ask 时 backfill 不被调用
        assert len(calls) == 0, "LLM ask 时不应回填（防提问内容幻觉）"


class TestRequiredFieldsTriState:
    """required_fields 三态：None=未决定，{}=已决定无需补，非空=补哪些"""

    @pytest.mark.unit
    def test_adopt_empty_rf_keeps_none(self, platform, make_state):
        """预测结果空清单 → 不采纳，保持 None（交给 decide 重试）"""
        state = make_state()
        platform._adopt_ticket_fields(state, {"ticket_type": "support", "required_fields": {}})
        assert state.required_fields is None

    @pytest.mark.unit
    def test_adopt_nonempty_rf(self, platform, make_state):
        """预测结果非空 → 采纳"""
        state = make_state()
        platform._adopt_ticket_fields(
            state, {"ticket_type": "problem", "required_fields": {"vehicle_id": "车辆编号"}})
        assert state.required_fields == {"vehicle_id": "车辆编号"}


class TestBuildTicketNoProject:
    """_build_ticket：project 不进对话/LLM 链路"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_build_ticket_project_empty(self, platform, make_state):
        """即使 collected_info 有 project 历史脏值，工单 project 也应为空"""
        state = make_state(
            phase="escalated",
            problem_summary="车头朝向与路径不符",
            collected_info={"vehicle_id": "Xlp", "project": "摇人吧服务号"},  # 历史脏值
        )
        state.ticket_type = "problem"
        memory = await platform._memory_manager.get_memory(state.session_id)
        # mock LLM 生成工单
        platform._llm_client.complete.return_value = json.dumps({
            "type": "problem",
            "title": "车头朝向与路径不符",
            "description": "叉车车头朝向与路径箭头不一致",
            "priority": "中",
            "project": "摇人吧服务号",  # LLM 抽风输出
        }, ensure_ascii=False)
        ticket = await platform._build_ticket(state.session_id, state, memory)
        assert ticket["project"] == "", "project 必须为空（弹窗才是唯一入口）"
        assert ticket["project_id"] == ""


class TestTicketContextSlicing:
    """对话切片：build_ticket 只看当前工单的对话"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_build_ticket_uses_context_start(self, platform, make_state):
        """context_start 之前的对话不应进工单生成 prompt"""
        state = make_state(phase="escalated", problem_summary="新问题")
        state.ticket_type = "problem"
        memory = await platform._memory_manager.get_memory(state.session_id)
        # 3 轮旧对话 + context_start=3（旧对话已归档）
        memory.turns = [
            {"role": "user", "content": "旧问题A"},
            {"role": "assistant", "content": "旧回答A"},
            {"role": "user", "content": "调度版本是2.6.4"},  # 上一单的补充
        ]
        state.context_start = 3
        # 新对话
        memory.turns.append({"role": "user", "content": "新问题B"})
        platform._llm_client.complete.return_value = json.dumps({
            "type": "problem",
            "title": "新问题B",
            "description": "新问题B的描述",
            "priority": "中",
        }, ensure_ascii=False)
        # 捕获 _build_ticket 用的 conversation_text：spy _format_conversation
        import importlib
        pipeline_mod = importlib.import_module("ai.agents.AiDiagnosisPlatform.pipeline")
        orig = pipeline_mod.AiDiagnosisPlatform._format_conversation

        captured = {}

        def spy_fmt(self, memory_, **kw):
            text = orig(self, memory_, **kw)
            captured["text"] = text
            captured["kw"] = kw
            return text

        pipeline_mod.AiDiagnosisPlatform._format_conversation = spy_fmt
        try:
            await platform._build_ticket(state.session_id, state, memory)
        finally:
            pipeline_mod.AiDiagnosisPlatform._format_conversation = orig

        assert captured["kw"].get("from_turn") == 3, "build_ticket 应按 context_start 切片"
        assert "调度版本" not in captured["text"], "旧工单的补充信息不应出现在新工单对话切片"
