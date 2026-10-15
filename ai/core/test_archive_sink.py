"""归档路沉淀（归档审核通过 → 放行该单的卡）的就近单测。

口径：与人工审核路（closed 提炼）的**向量文本逐字相同**——
已有卡只改 payload（放行 + 换 solution），没卡才用同一个提炼器补建。
隔离 DB / Qdrant。
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.core import solution_sink


def test_archived_sql_guards_archived_approved_and_unmarked():
    sql = solution_sink._ARCHIVED_CANDIDATES_SQL
    assert "archive_reports" in sql
    assert "submit_status = 'approved'" in sql
    assert "t.status = 'archived'" in sql
    # 独立标记，避免与 closed 路的 solution_indexed 互相屏蔽
    assert "archive_sink_status" in sql
    assert "solution_indexed" not in sql


def _retriever(existing=None, patch_ok=True, index_ok=True):
    return SimpleNamespace(
        fetch_task_resolution_payload=AsyncMock(return_value=existing),
        patch_task_resolution_payload=AsyncMock(return_value=patch_ok),
        index_task_resolution=AsyncMock(return_value=index_ok),
    )


ROW = {
    "id": 77,
    "title": "充电桩无法启动",
    "description": "现场描述",
    "project_name": "P1",
    "task_type": "problem",
    "metadata_info": {"fault_code": "E01", "robot_type": "R2"},
    "report_content": "## 问题\nx\n## 解决\n换保险丝",
    "report_reviewer": "张三",
    "report_version": 2,
    "report_reviewed_at": dt.datetime(2026, 10, 15, 9, 0, 0),
}


@pytest.mark.asyncio
async def test_existing_card_only_patches_payload_vector_untouched(monkeypatch):
    """逐字相同：已有卡只改 payload（放行 + 换 solution），绝不重写向量。"""
    marked = {}
    monkeypatch.setattr(solution_sink, "mark_archived_sunk",
                        lambda tid, st: marked.setdefault(tid, st))
    existing_card = {
        "problem_summary": "提炼的问题摘要",
        "root_cause": "提炼的根因",
        "fault_code": "E01",
        "robot_type": "R2",
        "solution_steps": "提炼的解决步骤",
    }
    retriever = _retriever(existing=existing_card)

    status = await solution_sink.process_archived_ticket(dict(ROW), retriever)

    assert status == "indexed"
    assert marked[77] == "indexed"
    retriever.index_task_resolution.assert_not_awaited()      # 向量一字不动
    args = retriever.patch_task_resolution_payload.await_args.args
    assert args[0] == "77"
    patch = args[1]
    assert patch["review_status"] == "approved"
    assert patch["solution_steps"] == ROW["report_content"]
    assert patch["reviewed_by"] == "张三"
    assert patch["reviewed_at"] == "2026-10-15 09:00:00"
    assert patch["source"] == "archive_review"
    # content 跟着 solution 换，但问题侧沿用原卡（向量/召回口径不变）
    assert "提炼的问题摘要" in patch["content"]
    assert "提炼的根因" in patch["content"]
    assert ROW["report_content"] in patch["content"]


@pytest.mark.asyncio
async def test_no_card_falls_back_to_distiller_as_approved(monkeypatch):
    """没卡才补建：用同一个提炼器（素材=归档报告），构造与 closed 路一致。"""
    marked = {}
    monkeypatch.setattr(solution_sink, "mark_archived_sunk",
                        lambda tid, st: marked.setdefault(tid, st))
    monkeypatch.setattr(solution_sink, "load_comments_text", lambda tid: ("", ""))
    monkeypatch.setattr(solution_sink, "_resolver_from_oplog", lambda tid: "")

    import ai.core.solution_distiller as distiller
    card = {"problem_summary": "摘要", "root_cause": "根因",
            "solution_steps": "步骤", "fault_code": "E9", "robot_type": "R9"}
    seen = {}

    async def fake_distill(**kwargs):
        seen.update(kwargs)
        return card

    monkeypatch.setattr(distiller, "distill_solution", fake_distill)

    retriever = _retriever(existing=None)
    status = await solution_sink.process_archived_ticket(dict(ROW), retriever)

    assert status == "indexed"
    assert marked[77] == "indexed"
    retriever.patch_task_resolution_payload.assert_not_awaited()
    assert seen["resolution_summary"] == ROW["report_content"]   # 报告当解法素材
    kwargs = retriever.index_task_resolution.await_args.kwargs
    assert kwargs["review_status"] == "approved"
    assert kwargs["problem_summary"] == card["problem_summary"]
    assert kwargs["root_cause"] == card["root_cause"]
    assert kwargs["solution_steps"] == card["solution_steps"]
    assert kwargs["fault_code"] == "E9"


@pytest.mark.asyncio
async def test_empty_report_skips_without_touching_qdrant(monkeypatch):
    marked = {}
    monkeypatch.setattr(solution_sink, "mark_archived_sunk",
                        lambda tid, st: marked.setdefault(tid, st))
    retriever = _retriever()

    status = await solution_sink.process_archived_ticket(
        {"id": 88, "report_content": "   "}, retriever)

    assert status == "empty"
    assert marked[88] == "empty"
    retriever.fetch_task_resolution_payload.assert_not_awaited()
    retriever.index_task_resolution.assert_not_awaited()


@pytest.mark.asyncio
async def test_patch_failure_leaves_unmarked(monkeypatch):
    marked = {}
    monkeypatch.setattr(solution_sink, "mark_archived_sunk",
                        lambda tid, st: marked.setdefault(tid, st))
    retriever = _retriever(existing={"problem_summary": "s"}, patch_ok=False)

    status = await solution_sink.process_archived_ticket(dict(ROW), retriever)

    assert status == "failed"
    assert marked == {}          # 失败不标记，下轮重试


@pytest.mark.asyncio
async def test_no_card_and_distiller_empty_marks_skipped(monkeypatch):
    marked = {}
    monkeypatch.setattr(solution_sink, "mark_archived_sunk",
                        lambda tid, st: marked.setdefault(tid, st))
    monkeypatch.setattr(solution_sink, "load_comments_text", lambda tid: ("", ""))

    import ai.core.solution_distiller as distiller

    async def fake_distill(**kwargs):
        return None

    monkeypatch.setattr(distiller, "distill_solution", fake_distill)
    retriever = _retriever(existing=None)

    status = await solution_sink.process_archived_ticket(dict(ROW), retriever)

    assert status == "empty"
    assert marked[77] == "empty"
    retriever.index_task_resolution.assert_not_awaited()
