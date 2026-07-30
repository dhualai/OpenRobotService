"""
AI 诊断系统测试 — 共享 Fixture

三层架构：
  Layer 0: 纯数据构造（无外部依赖）
  Layer 1: Mock 平台实例（LLM + Memory + Retriever + DB 全部 mock）
  Layer 2: 真实 Qdrant（session 级别，检索测试用）

关键注入点：AiDiagnosisPlatform._ensure_clients() 懒加载三个实例属性。
在 _ensure_clients() 之前直接设置 self._llm_client / self._memory_manager / self._retriever，
绕过真实服务初始化。
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


# ================================================================
# Layer 0: 纯数据构造
# ================================================================

@pytest.fixture
def make_state():
    """快捷构造 AgentState"""
    from ai.agents.AiDiagnosisPlatform.pipeline import AgentState

    def _make(phase="idle", problem_summary="", **kwargs):
        defaults = dict(
            session_id=f"test-{uuid4().hex[:8]}",
            phase=phase,
            problem_summary=problem_summary,
        )
        defaults.update(kwargs)
        return AgentState(**defaults)

    return _make


@pytest.fixture
def make_request():
    """构造 DiagnosisRequest（默认 skip_retrieval=True 跳过 Qdrant）"""
    from ai.agents.AiDiagnosisPlatform.pipeline import DiagnosisRequest

    def _make(query="机器人不动了", session_id=None, **kwargs):
        defaults = dict(skip_retrieval=True, created_by="tester")
        defaults.update(kwargs)
        return DiagnosisRequest(
            session_id=session_id or f"test-{uuid4().hex[:8]}",
            query=query,
            **defaults,
        )

    return _make


# ================================================================
# Layer 1: Mock 平台实例
# ================================================================

@pytest.fixture
def mock_llm():
    """Mock LLM — 返回可控的诊断 JSON

    测试中可设置 .return_value 或 .side_effect 覆盖默认行为。
    """
    mock = MagicMock()

    _DIAGNOSIS_RESPONSE = (
        '```json\n'
        + json.dumps({
            "thinking": "用户描述了机器人故障",
            "action": "answer",
            "intent": "troubleshoot",
            "message": "根据您的描述，这可能是激光传感器故障。请检查：\n1. 传感器连接线是否松动\n2. 传感器供电是否正常",
            "state_update": {"problem_summary": "机器人激光传感器无数据"},
        }, ensure_ascii=False)
        + '\n```\n'
        + '=== 根据您的描述，这可能是激光传感器故障。请检查：\n1. 传感器连接线是否松动\n2. 传感器供电是否正常'
    )

    _TICKET_RESPONSE = json.dumps({
        "title": "机器人激光传感器故障",
        "type": "problem",
        "priority": "中",
        "description": "用户报告激光传感器无数据，需要排查硬件连接和供电。",
        "project": "华大制造基地",
    }, ensure_ascii=False)

    def _llm_side_effect(prompt: str = "", max_tokens: int = 1500, temperature: float = 0.5):
        """根据 prompt 内容返回诊断或工单 JSON"""
        if "工单" in prompt or "ticket" in prompt.lower() or "title" in prompt:
            return _TICKET_RESPONSE
        return _DIAGNOSIS_RESPONSE

    mock.complete = AsyncMock(side_effect=_llm_side_effect)
    return mock


@pytest.fixture
def mock_memory():
    """Mock MemoryManager — 返回可控的 SessionMemory，所有写操作均为 no-op"""
    from ai.core.memory import SessionMemory

    mock = MagicMock()

    # 创建一个真实的 SessionMemory 用作读写目标
    _memory_store: dict = {}  # session_id → SessionMemory

    def _get_or_create(session_id: str) -> SessionMemory:
        if session_id not in _memory_store:
            _memory_store[session_id] = SessionMemory(
                session_id=session_id,
                turns=[],
                metadata={},
            )
        return _memory_store[session_id]

    async def _get_memory(session_id: str) -> SessionMemory:
        return _get_or_create(session_id)

    async def _add_turn(session_id: str, role: str, content: str) -> SessionMemory:
        mem = _get_or_create(session_id)
        mem.turns.append({"role": role, "content": content})
        return mem

    async def _save_memory(memory: SessionMemory) -> None:
        _memory_store[memory.session_id] = memory

    async def _resolve_pronoun(query: str, session_id: str):
        return (query, {})

    mock.get_memory = _get_memory
    mock.add_turn = _add_turn
    mock.save_memory = _save_memory
    mock.resolve_pronoun = _resolve_pronoun
    mock.add_pending_ticket = AsyncMock()
    mock.max_turns = 10  # int 类型，_finalize_diagnosis 中用于比较
    return mock


@pytest.fixture
def mock_retriever():
    """Mock RetrievalService — 五路检索均返回空"""
    mock = MagicMock()
    mock.retrieve = AsyncMock(return_value=([], 0.0))
    mock.retrieve_faq = AsyncMock(return_value=[])
    mock.retrieve_cheduan = AsyncMock(return_value=[])
    mock.retrieve_translation = AsyncMock(return_value=[])
    mock.retrieve_usp_diagnosis = AsyncMock(return_value=[])
    mock.retrieve_troubleshooting = AsyncMock(return_value=[])
    mock._extract_error_codes = MagicMock(return_value=[])
    return mock


@pytest.fixture
def mock_upsert_task():
    """Mock platform.submit() — 返回假工单数据，不写 MySQL

    替代直接 patch upsert_task，因为 task_adapter 模块级导入了 SessionLocal
    （触发 create_engine），patch 路径 ai.core.task_adapter.upsert_task 需要
    先导入该模块，会触发 MySQL 连接。
    """
    return None  # 标记由 platform fixture 处理


@pytest.fixture
async def platform(mock_llm, mock_memory, mock_retriever, mock_upsert_task):
    """组装完全 mock 的 AiDiagnosisPlatform 实例

    直接注入 mock 属性，绕过 _ensure_clients() 的真实服务初始化。
    submit() 方法也被 mock，避免 MySQL 依赖。
    """
    from ai.agents.AiDiagnosisPlatform.pipeline import AiDiagnosisPlatform

    p = AiDiagnosisPlatform()
    # 直接注入 mock，绕过 _ensure_clients()
    p._llm_client = mock_llm
    p._memory_manager = mock_memory
    p._retriever = mock_retriever

    # Mock submit() — 返回假工单，避免 MySQL 依赖
    # 同时模拟真实 submit() 的状态清理行为
    async def _mock_submit(session_id: str, created_by: str = "") -> dict:
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _save_agent_state, _load_agent_state, AgentState as AS,
        )
        memory = await p._memory_manager.get_memory(session_id)
        agent_state = _load_agent_state(memory.metadata) or AS(session_id=session_id)

        agent_state.ticket_seq += 1
        agent_state.phase = "resolved"
        agent_state.last_submitted_ticket = {
            "ticket_id": f"TK-{session_id[:8]}",
            "db_id": 99999,
            "title": "测试工单",
            "topic": agent_state.problem_summary,
        }
        agent_state.problem_summary = ""
        agent_state.ruled_out = []
        agent_state.hypotheses = []
        agent_state.collected_info = {}
        agent_state.diagnosis_rounds = 0
        _save_agent_state(memory, agent_state)
        await p._memory_manager.save_memory(memory)

        return {
            "type": "ticket",
            "data": {
                "ticket": {
                    "ticket_id": f"TK-{session_id[:8]}",
                    "title": "测试工单",
                    "type": "problem",
                    "priority": "中",
                    "description": "测试工单描述",
                },
                "db_id": 99999,
                "notice": "工单已生成并保存，等待自动派单。",
            },
        }

    p.submit = _mock_submit

    # Mock confirm_submit() — 避免 MySQL 依赖，但保留草稿校验逻辑
    async def _mock_confirm_submit(session_id: str, overrides: dict = None, created_by: str = "") -> dict:
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _save_agent_state, _load_agent_state, _check_required_fields, AgentState as AS,
        )
        memory = await p._memory_manager.get_memory(session_id)

        # 检查草稿是否存在
        draft = memory.metadata.get("ticket_draft")
        if not draft:
            return {"code": 1, "message": "没有待确认的工单草稿"}

        # 应用 overrides
        if overrides:
            draft.update(overrides)

        # 校验必填字段
        check = _check_required_fields(draft)
        if not check["ok"]:
            return {"code": 1, "message": check["prompt"], "missing_fields": check["missing"]}

        # 提交成功 → 更新状态
        agent_state = _load_agent_state(memory.metadata) or AS(session_id=session_id)
        agent_state.ticket_seq += 1
        agent_state.phase = "resolved"
        agent_state.last_submitted_ticket = {
            "ticket_id": f"TK-{session_id[:8]}",
            "db_id": 99999,
            "title": draft.get("title", "测试工单"),
            "topic": agent_state.problem_summary,
        }
        agent_state.problem_summary = ""
        agent_state.ruled_out = []
        agent_state.hypotheses = []
        agent_state.collected_info = {}
        agent_state.diagnosis_rounds = 0
        memory.metadata.pop("ticket_draft", None)
        _save_agent_state(memory, agent_state)
        await p._memory_manager.save_memory(memory)

        return {
            "code": 0,
            "data": {
                "ticket": {
                    "ticket_id": f"TK-{session_id[:8]}",
                    "title": draft.get("title", "测试工单"),
                    "type": draft.get("type", "problem"),
                    "db_id": 99999,
                },
                "db_id": 99999,
                "notice": "工单已生成并保存，等待自动派单。",
            },
        }

    p.confirm_submit = _mock_confirm_submit
    return p


# ================================================================
# Layer 2: 真实 Qdrant（integration 测试专用，session 级别复用）
# ================================================================

@pytest.fixture(scope="session")
def retrieval_service():
    """真实 RetrievalService（读本地 Qdrant 文件），session 级别复用

    需要 QDRANT_LOCAL_PATH 环境变量或 ai/.env 中配置。
    """
    import asyncio
    from ai.config import get_ai_config

    config = get_ai_config()
    if not config.qdrant_local_path:
        pytest.skip("需要本地 Qdrant 数据（QDRANT_LOCAL_PATH）")

    from ai.core.retrieval import RetrievalService

    svc = RetrievalService()
    # 使用 asyncio.run() 避免与 pytest-asyncio 的 loop 管理冲突
    try:
        asyncio.run(svc._ensure_clients())
    except RuntimeError:
        # asyncio.run() 在已有 running loop 的环境中会抛 RuntimeError
        # 回退到 get_event_loop（pytest-asyncio 已提供 loop）
        loop = asyncio.get_event_loop()
        loop.run_until_complete(svc._ensure_clients())
    return svc
