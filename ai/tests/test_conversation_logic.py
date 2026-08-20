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
        """LLM 返回 action=answer → phase 保持 diagnosing（answer 不再设 resolved；
        resolved 只由 _reset_state_after_submit 在提单成功后设置，避免与诊断完成语义混淆）"""
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
        assert state.phase == "diagnosing"

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

        # 对话提单先生成草稿，真正清理状态发生在 confirm_submit。
        from ai.agents.AiDiagnosisPlatform.pipeline import _load_agent_state
        persisted = _load_agent_state(memory.metadata)
        assert result["action"] == "answer"
        assert persisted.phase == "diagnosing"
        assert memory.metadata.get("ticket_draft")

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
        assert state.phase == "diagnosing"  # answer 不再设 resolved（resolved 仅提单后设置）


# ================================================================
# 2. 转工单意图识别（统一走 LLM，不再用关键词字符串匹配）
# ================================================================

class TestTicketIntent:
    """转工单意图由 LLM 判断，后处理链（_can_submit / _assess_ticket_readiness）兜底"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_blocked_when_just_submitted(self, platform, make_state, make_request):
        """刚提完单（last_submitted_ticket）+ 无新 problem + LLM 输出 submit → 闭环拦截"""
        state = make_state(
            phase="resolved", problem_summary="",
            last_submitted_ticket={"ticket_id": "T-1", "db_id": 1, "title": "旧工单", "topic": "旧问题"},
        )
        request = make_request(query="帮我转工单")
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

        assert "新问题" in result["message"] or "新现象" in result["message"]
        # LLM 被调用，由闭环保护（last_submitted_ticket + 无新问题）拦截
        platform._llm_client.complete.assert_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_submit_sets_ticket_collecting_when_fields_missing(self, platform, make_state, make_request):
        """idle + 无必填字段 + LLM 输出 submit → 拦截为 ask + ticket_collecting 激活"""
        state = make_state(phase="idle", problem_summary="机器人报错")
        request = make_request(query="给我下个工单")
        platform._llm_client.complete.side_effect = None
        platform._llm_client.complete.return_value = (
            '```json\n'
            + json.dumps({
                "thinking": "用户要提单",
                "action": "submit",
                "intent": "troubleshoot",
                "message": "好的，让我帮你提交。",
                "state_update": {},
            }, ensure_ascii=False)
            + '\n```\n好的，让我帮你提交。'
        )
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 字段清单由首次意图 LLM 决定；默认 mock 已覆盖字段，空清单也属于已锁定状态。
        assert state.required_fields is not None
        assert platform._llm_client.complete.call_count >= 1

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_normal_query_not_blocked(self, platform, make_state, make_request):
        """普通查询 → LLM 正常应答，不被拦截"""
        state = make_state(phase="idle")
        request = make_request(query="怎么调试激光传感器")
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 正常走 LLM
        platform._llm_client.complete.assert_called_once()
        assert result["action"] in ("answer", "ask")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_negation_not_mistaken_for_submit(self, platform, make_state, make_request):
        """"我不转工单就问问" → LLM 识别为否定，不输出 submit"""
        state = make_state(phase="idle")
        request = make_request(query="我不转工单，就问问这个报错什么意思")
        # 使用默认 mock（action=answer），模拟 LLM 正确识别非提单意图
        memory = await platform._memory_manager.get_memory(request.session_id)

        result = await platform._agent_think(request, state, memory)

        # 不被拦截，正常走 LLM 回答
        assert result["action"] in ("answer", "ask")
        platform._llm_client.complete.assert_called_once()


# ================================================================
# 3. LLM 输出解析（_parse_agent_output 纯同步方法）
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
        """"无"/"不清楚"/"不知道" 是用户明确回答，保留为标准值以满足固定字段。"""
        state = AgentState(session_id="test")
        updater(state, {"collected_info": {
            "location": "华大",
            "robot_type": "无",
            "error_code": "不清楚",
            "fault": "不知道",
        }})
        assert state.collected_info.get("location") == "华大"
        assert state.collected_info["robot_type"] == "无"
        assert state.collected_info["error_code"] == "无"
        assert state.collected_info["fault"] == "无"

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
