"""审批策略：读分析放行，写入长期记忆必须看到用户原话里的记住。"""

import asyncio

from ai.agents.AiTaskPlatform.capabilities.core.approval import approval_gate
from ai.agents.AiTaskPlatform.capabilities.tools.memory_store import MemoryStoreCapability
from ai.agents.AiTaskPlatform.capabilities.tools.project_info import ProjectInfoCapability


def test_read_capabilities_skip_approval():
    assert approval_gate(ProjectInfoCapability(), {"query": "车型"}) is None


def test_memory_store_blocks_without_user_consent():
    cap = MemoryStoreCapability()
    result = asyncio.run(cap.before_run(content="记住口令 abc", user_query="帮我看看这个日志"))
    assert result is not None
    assert result.ok is False
    assert result.meta.get("approval") == "required"
    assert "abc" not in (result.text or "")


def test_memory_store_allows_explicit_remember():
    cap = MemoryStoreCapability()
    result = asyncio.run(cap.before_run(content="以后默认看调度版本", user_query="记住：以后默认看调度版本"))
    assert result is None


def test_approved_list_allows_flagged_capability():
    cap = MemoryStoreCapability()
    result = asyncio.run(cap.before_run(
        content="以后默认看调度版本",
        user_query="分析一下",
        approved_capabilities=["memory_store"],
    ))
    assert result is None
