"""用户身份注入（0904）链路测试

覆盖：
  1. _flatten_resp_modules：三层/两层/字符串 JSON/扁平 list/垃圾 → 扁平串
  2. _resolve_user_profile：查到（部门 JOIN / 旧列回退）/查无/异常 → 降级 {}
  3. _user_profile_block：完整块 / 无画像空串 / 字段缺省省略
  4. 三套 prompt 注入：_session_state_block（全量）、收集模式、提单快路径
  5. _build_ticket contact：LLM 未提供联系人时兜底用户注册姓名
"""
import json
from unittest.mock import AsyncMock

import pytest

from ai.agents.AiDiagnosisPlatform.pipeline import (
    _USER_PROFILE_CACHE, _flatten_resp_modules, _resolve_user_profile,
    _user_profile_block, _session_state_block,
)


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    _USER_PROFILE_CACHE.clear()
    yield
    _USER_PROFILE_CACHE.clear()


def _profile(**kw):
    base = dict(name="张三", department="调度部", job_level_cn="一线工程师",
                modules_text="调度USP：路径规划(避障)", duty="负责调度系统日常运维")
    base.update(kw)
    return base


def _patch_db(monkeypatch, session_cls):
    """patch sys.modules 条目（函数内 from-import 的取用目标）。

    test_report 的 _preload_database_module 会把 sys.modules["ai.core.database"]
    覆盖成手工副本，此时 `import ai.core.database as db_mod` 走属性链拿到旧
    对象——patch 它对 from-import 不生效（0904 全量回归实锤的分裂问题）。
    """
    import sys
    mod = sys.modules.get("ai.core.database")
    if mod is None:
        mod = __import__("ai.core.database", fromlist=["SessionLocal"])
    monkeypatch.setattr(mod, "SessionLocal", session_cls)


def _fake_db(rows_or_error, project_roles=None):
    """SessionLocal 工厂：rows_or_error 为 fetchone 返回值，或抛出的异常类实例。
    project_roles：第二次 execute（项目内角色查询）的 fetchall 返回值，默认 []。"""

    class _Result:
        def __init__(self, is_roles_query):
            self._is_roles = is_roles_query

        def fetchone(self):
            if isinstance(rows_or_error, Exception):
                raise rows_or_error
            return rows_or_error

        def fetchall(self):
            if self._is_roles:
                return [(r,) for r in (project_roles or [])]
            if isinstance(rows_or_error, Exception):
                raise rows_or_error
            return []

    class _Session:
        def __init__(self):
            self._calls = 0

        def execute(self, sql, params=None):
            self._calls += 1
            return _Result(self._calls == 2)

        def close(self):
            pass

    return _Session


# ================================================================
# 1. responsibility_modules 扁平化
# ================================================================

class TestFlattenModules:
    def test_three_layer(self):
        m = {"调度USP": {"任务界面": ["路径规划", "避障"]}}
        assert _flatten_resp_modules(m) == "调度USP：任务界面(路径规划、避障)"

    def test_two_layer(self):
        assert _flatten_resp_modules({"调度": ["路径规划", "避障"]}) == "调度：路径规划、避障"

    def test_json_string(self):
        assert _flatten_resp_modules(json.dumps({"调度": ["路径"]})) == "调度：路径"

    def test_flat_list_legacy(self):
        assert _flatten_resp_modules(["A", "B"]) == "其他：A、B"

    @pytest.mark.parametrize("bad", ["不是json", None, 123, {"k": 456}])
    def test_garbage_returns_blank(self, bad):
        assert _flatten_resp_modules(bad) == ""


# ================================================================
# 2. users 表解析
# ================================================================

class TestResolveUserProfile:
    @pytest.mark.asyncio
    async def test_found(self, monkeypatch):
        row = ("张三", "调度部", "旧部门", 1,
               json.dumps({"调度USP": {"任务界面": ["路径规划"]}}), "负责调度系统")
        _patch_db(monkeypatch, _fake_db(row))
        p = await _resolve_user_profile("zhangsan")
        assert p["name"] == "张三"
        assert p["department"] == "调度部"
        assert p["job_level_cn"] == "一线工程师"
        assert "路径规划" in p["modules_text"]
        assert p["duty"] == "负责调度系统"

    @pytest.mark.asyncio
    async def test_dept_fallback_to_legacy_column(self, monkeypatch):
        row = ("李四", None, "旧部门名", 2, None, None)
        _patch_db(monkeypatch, _fake_db(row))
        p = await _resolve_user_profile("lisi")
        assert p["department"] == "旧部门名"
        assert p["job_level_cn"] == "管理/审核"
        assert p["modules_text"] == "" and p["duty"] == ""

    @pytest.mark.asyncio
    async def test_no_such_user(self, monkeypatch):
        _patch_db(monkeypatch, _fake_db(None))
        assert await _resolve_user_profile("ghost") == {}

    @pytest.mark.asyncio
    async def test_db_error_degrades_to_empty(self, monkeypatch):
        _patch_db(monkeypatch, _fake_db(RuntimeError("db down")))
        assert await _resolve_user_profile("zhangsan") == {}

    @pytest.mark.asyncio
    async def test_blank_username(self):
        assert await _resolve_user_profile("") == {}
        assert await _resolve_user_profile("   ") == {}

    @pytest.mark.asyncio
    async def test_cached_second_call(self, monkeypatch):
        calls = {"n": 0}

        class _Result:
            def fetchone(self):
                return ("张三", "调度部", None, 1, None, None)

            def fetchall(self):
                return [("调度研发",)]

        class _CountingSession:
            # 计数单位=「画像解析次数」（主查询 1 次 + 角色查询 1 次/轮）：
            # 断言两次调用只解析一轮（缓存命中不再查库）
            def execute(self, sql, params=None):
                calls["n"] = calls.get("n", 0) + 1
                return _Result()

            def close(self):
                pass

        _patch_db(monkeypatch, _CountingSession)
        await _resolve_user_profile("zhangsan")
        p2 = await _resolve_user_profile("zhangsan")
        assert calls["n"] == 2
        assert p2["name"] == "张三"


# ================================================================
# 3. 【用户】块渲染
# ================================================================

class TestUserProfileBlock:
    def test_full_block(self, make_state):
        out = _user_profile_block(make_state(user_profile=_profile()))
        assert out.startswith("【用户】张三｜调度部｜一线工程师\n")
        assert "【用户职责】负责：调度USP：路径规划(避障)｜负责调度系统日常运维" in out
        assert "无需每句称呼用户名" in out

    def test_name_only(self, make_state):
        out = _user_profile_block(make_state(user_profile=_profile(
            department="", job_level_cn="", modules_text="", duty="")))
        assert out.startswith("【用户】张三\n")
        assert "【用户职责】" not in out

    def test_no_profile_blank(self, make_state):
        assert _user_profile_block(make_state()) == ""
        assert _user_profile_block(make_state(user_profile={})) == ""
        assert _user_profile_block(make_state(user_profile={"name": ""})) == ""


# ================================================================
# 4. 序列化往返（0904 生产实锤：白名单漏字段 → 跨轮丢画像）
# ================================================================

class TestStateRoundtrip:
    def test_save_load_preserves_profile(self, make_state):
        from types import SimpleNamespace
        from ai.agents.AiDiagnosisPlatform.pipeline import (
            _save_agent_state, _load_agent_state)
        mem = SimpleNamespace(metadata={"agent_state": {"attachments": ["a.png"]}})
        _save_agent_state(mem, make_state(user_profile=_profile()))
        loaded = _load_agent_state(mem.metadata)
        assert loaded is not None
        assert loaded.user_profile["name"] == "张三"
        assert loaded.user_profile["department"] == "调度部"
        # 旧数据无该键 → 空画像，不炸
        mem2 = SimpleNamespace(metadata={"agent_state": {
            "session_id": "s1", "problem_summary": "", "phase": "idle"}})
        loaded2 = _load_agent_state(mem2.metadata)
        assert loaded2 is not None and loaded2.user_profile == {}
        # 附件保留逻辑不受影响
        assert mem.metadata["agent_state"]["attachments"] == ["a.png"]


# ================================================================
# 5. 三套 prompt 注入
# ================================================================

class TestPromptInjection:
    def test_session_state_block_contains_user(self, make_state):
        from types import SimpleNamespace
        state = make_state(user_profile=_profile())
        mem = SimpleNamespace(metadata={})
        block = _session_state_block(state, mem)
        assert "【用户】张三" in block
        # 用户是最稳定上下文，置于工单/项目事实之前
        assert block.index("【用户】") < block.index("【上一张工单】")

    def test_session_state_block_without_profile(self, make_state):
        from types import SimpleNamespace
        block = _session_state_block(make_state(), SimpleNamespace(metadata={}))
        assert "【用户】" not in block

    @pytest.mark.asyncio
    async def test_collecting_prompt_contains_user(self, platform, make_state):
        state = make_state(user_profile=_profile(),
                           ticket_collecting=["robot_id"],
                           required_fields={"robot_id": "车辆编号"})
        memory = await platform._memory_manager.get_memory("up-collect")
        prompt = platform._build_diagnosis_prompt(state, memory, "")
        assert "【用户】张三" in prompt

    @pytest.mark.asyncio
    async def test_fast_lane_prompt_contains_user(self, platform, make_state):
        state = make_state(user_profile=_profile(), ticket_fast_lane=True)
        memory = await platform._memory_manager.get_memory("up-fast")
        prompt = platform._build_diagnosis_prompt(state, memory, "")
        assert "【用户】张三" in prompt

    @pytest.mark.asyncio
    async def test_oneshot_prompt_contains_user(self, platform, mock_llm,
                                                make_state, make_request):
        """0904 生产实锤：「我是谁」判 courtesy 走闲聊单轮小 prompt，
        该分支漏注入 → 画像解析成功 LLM 却不知道用户是谁。"""
        captured = {}

        def _fake_stream(prompt="", system_prompt="", **kw):
            captured["system"] = system_prompt

            async def _gen():
                yield "你是胡健楠呀"
            return _gen()

        mock_llm.stream = _fake_stream
        memory = await platform._memory_manager.get_memory("up-oneshot")
        state = make_state(user_profile=_profile())
        req = make_request(query="我是谁")
        async for _ in platform._diagnosis_oneshot_branch(req, state, memory):
            pass
        assert "【用户】张三" in captured["system"]

    @pytest.mark.asyncio
    async def test_oneshot_prompt_without_profile(self, platform, mock_llm,
                                                  make_state, make_request):
        captured = {}

        def _fake_stream(prompt="", system_prompt="", **kw):
            captured["system"] = system_prompt

            async def _gen():
                yield "你好"
            return _gen()

        mock_llm.stream = _fake_stream
        memory = await platform._memory_manager.get_memory("up-oneshot2")
        req = make_request(query="在吗")
        async for _ in platform._diagnosis_oneshot_branch(req, make_state(), memory):
            pass
        assert "【用户】" not in captured["system"]


# ================================================================
# 6. contact 兜底
# ================================================================

def _llm_json(**extra) -> str:
    payload = {
        "title": "激光传感器故障", "type": "problem", "priority": "中",
        "description": "传感器无数据，需排查。",
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


class TestContactFallback:
    @pytest.mark.asyncio
    async def test_contact_falls_back_to_profile_name(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json())
        memory = await platform._memory_manager.get_memory("up-c1")
        state = make_state(user_profile=_profile())
        result = await platform._build_ticket("up-c1", state, memory)
        assert result["contact"] == "张三"

    @pytest.mark.asyncio
    async def test_llm_contact_wins(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json(contact="王经理"))
        memory = await platform._memory_manager.get_memory("up-c2")
        state = make_state(user_profile=_profile())
        result = await platform._build_ticket("up-c2", state, memory)
        assert result["contact"] == "王经理"

    @pytest.mark.asyncio
    async def test_no_profile_no_contact(self, platform, mock_llm, make_state):
        mock_llm.complete = AsyncMock(return_value=_llm_json())
        memory = await platform._memory_manager.get_memory("up-c3")
        result = await platform._build_ticket("up-c3", make_state(), memory)
        assert result["contact"] == ""
