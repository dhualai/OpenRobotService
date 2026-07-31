"""
对话逻辑测试 — 状态转换、关键词快捷路径、LLM 输出解析、状态更新

调用真实 AiDiagnosisPlatform 方法，通过 mock LLM/Memory/Retriever 注入。
"""
import json
import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform, AgentState,
)


# ================================================================
# 1. 状态转换
# ================================================================

class TestStateTransitions:
    """AgentState phase 生命周期：idle → diagnosing → resolved/escalated"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_idle_enters_diagnosing(self, platform, make_state, make_request):
        """新会话首次查询 → phase 从 idle 进入 diagnosing"""
        state = make_state(phase="idle", problem_summary="")
        request = make_request(query="机器人报错404")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        assert result["action"] in ("answer", "ask")
        assert state.phase in ("diagnosing", "resolved")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_answer_resolves_session(self, platform, make_state, make_request):
        """LLM 返回 action=answer → phase 变为 resolved"""
        state = make_state(phase="idle")
        request = make_request(query="怎么处理激光报错")
        # 清除 side_effect 再设 return_value（side_effect 优先级更高）
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "给出解答",
                "action": "answer",
                "intent": "troubleshoot",
                "message": "请检查传感器连接线。",
                "state_update": {"problem_summary": "激光传感器故障"},
            }, ensure_ascii=False)
            + '\n```\n请检查传感器连接线。'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        assert result["action"] == "answer"
        assert state.phase == "resolved"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_escalates(self, platform, make_state, make_request):
        """LLM action=submit 且有 project + 保底必填字段 → phase 变为 escalated，ticket 生成"""
        state = make_state(
            phase="idle",
            problem_summary="机器人离线",
            ticket_type="problem",
            collected_info={"project": "华大制造基地", "robot_type": "XP1152",
                            "occurrence_time": "昨天下午", "frequency": "每次"},
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

        assert result["action"] in ("answer", "submit")
        # submit() 成功后会刷新并以持久化的 state 为准（局部传入的 state 对象不再更新）
        from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
        persisted = _load_agent_state(memory.metadata)
        assert persisted.phase in ("resolved", "escalated")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_new_problem_after_resolved(self, platform, make_state, make_request):
        """resolved 状态 + 新 problem_summary → 正常诊断不拦截"""
        state = make_state(
            phase="resolved",
            problem_summary="新的故障：通讯模块超时",
        )
        request = make_request(query="通讯模块报错1999")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "新问题诊断",
                "action": "answer",
                "intent": "troubleshoot",
                "message": "通讯模块超时可能是信号干扰导致。",
                "state_update": {},
            }, ensure_ascii=False)
            + '\n```\n通讯模块超时可能是信号干扰导致。'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 不拦截：有新 problem_summary 时 _can_submit 返回 True
        assert result["action"] == "answer"
        assert state.phase == "resolved"


# ================================================================
# 2. 关键词快捷路径（不调 LLM，直接 return）
# ================================================================

class TestKeywordShortCircuit:
    """转工单关键词 → 提前拦截/引导，不调 LLM"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_keyword_blocked_when_resolved_empty(self, platform, make_state, make_request):
        """resolved + 空 problem_summary + "转工单" → 直接拦截，不调 LLM"""
        state = make_state(phase="resolved", problem_summary="")
        request = make_request(query="转工单")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        assert "无需重复提交" in result["message"] or "请先描述现象" in result["message"]
        # 关键词被拦截 → 不应该调用 LLM
        platform._llm_client.complete.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_keyword_asks_project_when_missing(self, platform, make_state, make_request):
        """idle + 无 project + "转工单" → pending_submit=True，引导补项目"""
        state = make_state(phase="idle", problem_summary="机器人报错")
        request = make_request(query="转工单")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        assert state.pending_submit is True
        assert "项目" in result["message"]
        # 同样不调 LLM
        platform._llm_client.complete.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_normal_query_not_short_circuited(self, platform, make_state, make_request):
        """普通查询 → 正常走 LLM 路径"""
        state = make_state(phase="idle")
        request = make_request(query="怎么调试激光传感器")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 应该调用了 LLM
        platform._llm_client.complete.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kw", ["转工单", "转单", "生成工单", "提交工单", "提单", "帮我转", "我要转"])
    async def test_all_keywords_detected_in_resolved(self, platform, make_state, make_request, kw):
        """resolved + 空问题 + 任意转工单关键词 → 被拦截"""
        state = make_state(phase="resolved", problem_summary="")
        request = make_request(query=kw)
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        assert result["action"] == "answer"
        platform._llm_client.complete.assert_not_called()


# ================================================================
# 3. pending_submit 自动提单
# ================================================================

class TestPendingSubmit:
    """pending_submit=True 时，下一轮用户输入自动作为 project 提单"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pending_submit_auto_triggers(self, platform, make_state, make_request):
        """pending_submit=True → 用户输入 "现场A" → 直接提单"""
        state = make_state(
            phase="idle",
            problem_summary="机器人报错",
            pending_submit=True,
        )
        request = make_request(query="华大制造基地")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # pending_submit 提单成功 → message 含提单确认
        assert "工单" in result["message"] or state.pending_submit is False
        # project 已被设置
        assert state.collected_info.get("project") == "华大制造基地"


# ================================================================
# 4. LLM 输出解析（_parse_agent_output 纯同步方法）
# ================================================================

class TestParseAgentOutput:
    """_parse_agent_output 处理各种 LLM 输出格式"""

    @pytest.fixture
    def parser(self):
        """返回 _parse_agent_output 绑定方法"""
        p = AiDiagnosisPlatform()
        return p._parse_agent_output

    def test_parse_fenced_json(self, parser):
        """```json ... ``` === 回复文本"""
        raw = '```json\n{"action":"answer","intent":"troubleshoot","message":"请检查连接"}\n```\n=== 回复文本'
        result = parser(raw)
        assert result["action"] == "answer"
        assert result["intent"] == "troubleshoot"

    def test_parse_bare_json(self, parser):
        """{...} 回复文本（无 ``` 包裹）"""
        raw = '{"action":"submit","intent":"troubleshoot","thinking":"需要提单"}好的已提单'
        result = parser(raw)
        assert result["action"] == "submit"
        assert "好的已提单" in result["message"]

    def test_parse_json_with_thinking(self, parser):
        """含 thinking 字段"""
        raw = '```json\n{"thinking":"分析中...","action":"answer","message":"结果"}\n```\n回复'
        result = parser(raw)
        assert result["thinking"] == "分析中..."
        assert result["action"] == "answer"

    def test_parse_malformed_to_fallback(self, parser):
        """无效 JSON → action="ask"，message 兜底"""
        raw = "这不是有效的 JSON 格式，但我还是给出回复"
        result = parser(raw)
        assert result["action"] == "ask"
        # message 不为空
        assert result["message"]

    def test_parse_invalid_action_fallback(self, parser):
        """action="dance"（不在枚举内）→ 修正为 "ask" """
        raw = '```json\n{"action":"dance","message":"跳舞"}\n```'
        result = parser(raw)
        assert result["action"] == "ask"

    def test_parse_no_json_at_all(self, parser):
        """纯文本无 JSON → action="ask" """
        raw = "你好，我可以帮你解决这个问题吗？"
        result = parser(raw)
        assert result["action"] == "ask"
        assert result["message"] == raw

    def test_parse_default_message_when_empty(self, parser):
        """JSON 后无文本 → 兜底消息"""
        raw = '```json\n{"action":"answer"}\n```'
        result = parser(raw)
        assert result["action"] == "answer"
        # 兜底消息不为空
        assert result["message"]


# ================================================================
# 5. 状态更新（_apply_state_update 纯同步方法）
# ================================================================

class TestStateUpdate:
    """_apply_state_update 合并 LLM 提炼的信息到 AgentState"""

    @pytest.fixture
    def updater(self):
        """返回 _apply_state_update 绑定方法"""
        p = AiDiagnosisPlatform()
        return p._apply_state_update

    def test_update_problem_summary(self, updater):
        """state_update 含 problem_summary → state 更新"""
        state = AgentState(session_id="test")
        updater(state, {"problem_summary": "新的问题摘要"})
        assert state.problem_summary == "新的问题摘要"

    def test_update_filter_useless_values(self, updater):
        """collected_info 含 "无"/"不清楚"/"不知道" → 对应 key 被移除
        （注意：project 键由用户显式输入设置，LLM 的 state_update 无权改动，这里用 location 验证保留逻辑）"""
        state = AgentState(session_id="test")
        updater(state, {"collected_info": {
            "location": "华大",
            "robot_type": "无",
            "error_code": "不清楚",
            "fault": "不知道",
        }})
        assert state.collected_info.get("location") == "华大"
        assert "robot_type" not in state.collected_info
        assert "error_code" not in state.collected_info
        assert "fault" not in state.collected_info

    def test_update_clear_null(self, updater):
        """collected_info 含 None → 对应 key 被移除"""
        state = AgentState(session_id="test", collected_info={"existing": "keep", "to_clear": "old"})
        updater(state, {"collected_info": {"existing": None, "to_clear": None}})
        assert "existing" not in state.collected_info
        assert "to_clear" not in state.collected_info

    def test_update_merge_preserves_existing(self, updater):
        """已有 collected_info，新 state_update 只加新 key → 旧 key 保留"""
        state = AgentState(session_id="test", collected_info={"project": "华大"})
        updater(state, {"collected_info": {"robot_type": "堆高车"}})
        assert state.collected_info.get("project") == "华大"
        assert state.collected_info.get("robot_type") == "堆高车"

    def test_update_ruled_out(self, updater):
        """state_update 含 ruled_out → state 更新"""
        state = AgentState(session_id="test")
        updater(state, {"ruled_out": ["网络问题", "电源故障"]})
        assert state.ruled_out == ["网络问题", "电源故障"]

    def test_update_hypotheses(self, updater):
        """state_update 含 hypotheses → state 更新"""
        state = AgentState(session_id="test")
        updater(state, {"hypotheses": ["传感器松动", "通讯超时"]})
        assert state.hypotheses == ["传感器松动", "通讯超时"]


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
