"""长期记忆命令：列出、删除。"""
import asyncio

from ai.agents.AiTaskPlatform.memory.agent_memory_service import AgentMemoryService
from ai.agents.AiTaskPlatform.memory.cli import main


def _rec(mem_id: str, content: str) -> dict:
    return {
        "id": mem_id,
        "kind": "directive",
        "importance": 0.8,
        "source": "task",
        "source_id": "1",
        "created_at": "2026-09-29 16:00:00",
        "status": "active",
        "content": content,
    }


def test_list_and_delete(tmp_path, capsys, monkeypatch):
    svc = AgentMemoryService(tmp_path)
    svc._write_entry(_rec("abc", "折返时先看调度是否来回切换"))
    svc._rebuild_memory_md()

    async def _no_qdrant(_mem_id: str) -> None:
        return None

    monkeypatch.setattr(AgentMemoryService, "_qdrant_delete", _no_qdrant)

    assert main(["--dir", str(tmp_path), "list"]) == 0
    listed = capsys.readouterr().out
    assert "abc" in listed
    assert "折返时先看调度是否来回切换" in listed

    assert main(["--dir", str(tmp_path), "delete", "missing"]) == 1
    assert "没有这条记忆" in capsys.readouterr().out

    assert main(["--dir", str(tmp_path), "delete", "abc"]) == 0
    assert main(["--dir", str(tmp_path), "list"]) == 0
    assert "没有生效中的记忆" in capsys.readouterr().out
    assert asyncio.run(svc.delete("abc")) is False


def test_update_content(tmp_path, monkeypatch):
    svc = AgentMemoryService(tmp_path)
    svc._write_entry(_rec("abc", "旧约定"))
    svc._rebuild_memory_md()

    async def _no_upsert(_rec):
        return None

    monkeypatch.setattr(AgentMemoryService, "_qdrant_upsert", _no_upsert)
    updated = asyncio.run(svc.update_content("abc", "新约定：先看调度"))
    assert updated and updated["content"] == "新约定：先看调度"
    assert svc.list_entries()[0]["content"] == "新约定：先看调度"
    assert asyncio.run(svc.update_content("missing", "x")) is None
