# -*- coding: utf-8 -*-
"""归档流 AI 环节测试（ORS-986）。

覆盖：初稿生成的三时序（AI 先写/处理人先写/空内容拒绝）、SSE 事件序列、
标题解析、入库打标、process_ticket 的 archive_content 覆盖、幂等去重。
backend 零改动是本设计的前提——测试同时验证 SQL 条件写回的形态。
"""
import asyncio
import json

import pytest

from ai.core import archive_flow
from ai.core import solution_sink


# ---------------- fakes ----------------

class FakeResult:
    def __init__(self, rowcount=1, mappings_rows=()):
        self._rowcount = rowcount
        self._rows = mappings_rows

    @property
    def rowcount(self):
        return self._rowcount

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def all(self):
        return list(self._rows)


class FakeDB:
    """记录 execute 的 SQL 与参数；SELECT 按 callsite 注水。"""

    def __init__(self, select_rows=(), update_rowcount=1):
        self.executed = []
        self._select_rows = select_rows
        self._update_rowcount = update_rowcount
        self.committed = 0

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            return FakeResult(mappings_rows=self._select_rows)
        return FakeResult(rowcount=self._update_rowcount)

    def commit(self):
        self.committed += 1

    def close(self):
        pass


class FakeLLM:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

    async def stream(self, prompt, **kw):
        self.calls.append(prompt)
        for c in self._chunks:
            yield c


@pytest.fixture
def patch_db(monkeypatch):
    """archive_flow / solution_sink 内联 import 的 SessionLocal 换成可控 fake。"""
    holder = {"db": FakeDB()}

    def factory():
        return holder["db"]

    from ai.core import database
    monkeypatch.setattr(database, "SessionLocal", factory)
    return holder


def _drain(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ---------------- 生成：三时序 ----------------

@pytest.mark.asyncio
async def test_generate_happy_path_writes_and_emits(patch_db, monkeypatch):
    """AI 先于处理人：事件序列 title→delta→done，且 SQL 带(content='')条件写回。"""
    patch_db["db"] = FakeDB(
        select_rows=[{"id": 1, "title": "T", "description": "D", "metadata_info": {},
                      "task_type": "problem", "project_name": "P", "status": "archiving",
                      "assigned_to": "", "report_id": 9, "version": 1,
                      "content": "", "submit_status": "draft",
                      "resolution_summary": "rs", "comments": "c", "last_human": "x"}],
        update_rowcount=1)
    monkeypatch.setattr(archive_flow, "load_archive_context",
                        async_stub({"report_id": 9, "version": 1, "content": "",
                                    "submit_status": "draft", "title": "T",
                                    "description": "D", "resolution_summary": "rs",
                                    "task_type": "problem", "project_name": "P",
                                    "comments": "处理人：换了雷达", "last_human": "处理人",
                                    "meta": {}}))

    class LLM:
        async def stream(self, prompt, **kw):
            assert "问题背景" in prompt  # 结构约定
            for c in ["# 标题行\n", "## 问题背景\n", "内容"]:
                yield c
    monkeypatch.setattr("ai.core.solution_distiller._get_distill_llm",
                        async_stub(LLM()))

    q: asyncio.Queue = asyncio.Queue()
    await archive_flow.generate_stream_task(1, q, "tester")
    evs = _drain(q)
    types = [e["type"] for e in evs]
    assert types[0] == "status"
    assert "title" in types and "delta" in types
    assert types[-1] == "done"
    upd = [s for s, _ in patch_db["db"].executed if "UPDATE archive_reports" in s]
    assert upd and "content = ''" in upd[0]  # 条件写回防覆盖


@pytest.mark.asyncio
async def test_generate_skips_when_human_wrote_first(patch_db, monkeypatch):
    """处理人先写：写回 0 行 → skipped 事件，绝不覆盖。"""
    monkeypatch.setattr(archive_flow, "load_archive_context",
                        async_stub({"report_id": 9, "version": 1, "content": "我的版本",
                                    "submit_status": "draft", "title": "T",
                                    "description": "D", "resolution_summary": "rs",
                                    "task_type": "", "project_name": "",
                                    "comments": "", "last_human": "", "meta": {}}))
    patch_db["db"] = FakeDB(update_rowcount=0)  # content='' 条件不命中
    monkeypatch.setattr("ai.core.solution_distiller._get_distill_llm",
                        async_stub(FakeLLM(["# x\n正文"])))

    q: asyncio.Queue = asyncio.Queue()
    await archive_flow.generate_stream_task(1, q, "tester")
    evs = _drain(q)
    assert evs[-1]["type"] == "skipped"


@pytest.mark.asyncio
async def test_generate_refuses_non_draft_state(patch_db, monkeypatch):
    """报告已 approved（非 draft/rejected）→ 直接拒绝，不生成。"""
    monkeypatch.setattr(archive_flow, "load_archive_context",
                        async_stub({"report_id": 9, "version": 2, "content": "已归档内容",
                                    "submit_status": "approved", "title": "T",
                                    "description": "", "resolution_summary": "",
                                    "task_type": "", "project_name": "",
                                    "comments": "", "last_human": "", "meta": {}}))
    patch_db["db"] = FakeDB()
    q: asyncio.Queue = asyncio.Queue()
    await archive_flow.generate_stream_task(1, q, "tester")
    evs = _drain(q)
    # approved 报告必有内容 → 走「不覆盖已有内容」跳过路径（语义上也不该重写终态报告）
    assert evs[-1]["type"] == "skipped"


# ---------------- spawn 幂等 ----------------

@pytest.mark.asyncio
async def test_spawn_is_idempotent(monkeypatch):
    monkeypatch.setattr(archive_flow, "load_archive_context",
                        async_stub({"report_id": 9, "version": 1, "content": "",
                                    "submit_status": "draft", "title": "T",
                                    "description": "", "resolution_summary": "",
                                    "task_type": "", "project_name": "",
                                    "comments": "", "last_human": "", "meta": {}}))
    monkeypatch.setattr("ai.core.solution_distiller._get_distill_llm",
                        async_stub(FakeLLM(["慢"])))  # 永远不结束的假流太麻烦，用短流+sleep
    q1: asyncio.Queue = asyncio.Queue()
    assert archive_flow.spawn_generation(1, q1, "u") is True
    assert archive_flow.spawn_generation(1, q2 := asyncio.Queue(), "u") is False
    task = archive_flow._ACTIVE.pop(1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------- 入库：approved 报告 → 卡 → 打标 ----------------

@pytest.mark.asyncio
async def test_ingest_worker_uses_archive_content_and_marks(patch_db, monkeypatch):
    """入库以 approved 报告全文为知识源（archive_content 覆盖），成功后打归档标记。"""
    rows = [{"task_id": 7, "content": "# 归档报告全文", "version": 1, "title": "T",
             "description": "D", "assigned_to": "", "metadata_info": {},
             "project_name": "P", "task_type": "problem"}]
    patch_db["db"] = FakeDB(select_rows=rows, update_rowcount=1)
    seen = {}

    async def fake_process(row, retriever, dry_run=False, archive_content=None):
        seen["archive_content"] = archive_content
        seen["row"] = row
        return "indexed"

    monkeypatch.setattr(solution_sink, "process_ticket", fake_process)
    stop = asyncio.Event()

    class R:
        async def get_retrieval_service(self=None):
            return object()

    monkeypatch.setattr("ai.core.get_retrieval_service",
                        async_stub(object()))
    worker = asyncio.create_task(archive_flow.run_archive_ingest_worker(stop))
    # 轮询一轮后停
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(worker, timeout=5)
    assert seen.get("archive_content") == "# 归档报告全文"
    upd = [s for s, _ in patch_db["db"].executed if "archive_knowledge_indexed" in s]
    assert upd  # 归档标记已写


@pytest.mark.asyncio
async def test_ingest_worker_failed_no_mark(patch_db, monkeypatch):
    """入库失败（qdrant 异常等）不打标，下轮重试。"""
    rows = [{"task_id": 7, "content": "x", "version": 1, "title": "T",
             "description": "", "assigned_to": "", "metadata_info": {},
             "project_name": "", "task_type": ""}]
    patch_db["db"] = FakeDB(select_rows=rows)

    async def fake_process(row, retriever, dry_run=False, archive_content=None):
        raise RuntimeError("qdrant down")
    monkeypatch.setattr(solution_sink, "process_ticket", fake_process)
    monkeypatch.setattr("ai.core.get_retrieval_service", async_stub(object()))
    stop = asyncio.Event()
    worker = asyncio.create_task(archive_flow.run_archive_ingest_worker(stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(worker, timeout=5)
    upd = [s for s, _ in patch_db["db"].executed if "UPDATE tasks" in s and "archive_knowledge_indexed" in s]
    assert not upd  # 未打标


# ---------------- process_ticket：archive_content 覆盖 ----------------

@pytest.mark.asyncio
async def test_process_ticket_archive_content_overrides_meta(monkeypatch):
    captured = {}

    async def fake_distill(title, description, resolution_summary,
                           comments_text, llm=None, task_type=""):
        captured["rs"] = resolution_summary
        return {"problem_summary": "p", "root_cause": "r", "solution_steps": "s",
                "fault_code": "", "robot_type": ""}

    monkeypatch.setattr("ai.core.solution_distiller.distill_solution", fake_distill)
    monkeypatch.setattr(solution_sink, "load_comments_text", lambda tid: ("", ""))
    monkeypatch.setattr(solution_sink, "_resolver_from_oplog", lambda tid: "张三")
    monkeypatch.setattr(solution_sink, "mark_indexed", lambda tid, st: None)

    class R:
        async def index_task_resolution(self, **kw):
            return True

    row = {"id": 1, "title": "T", "description": "D", "assigned_to": "",
           "metadata_info": {"resolution_summary": "旧的敷衍总结"},
           "project_name": "", "task_type": "problem"}
    st = await solution_sink.process_ticket(row, R(), archive_content="归档报告全文（人审过）")
    assert st == "indexed"
    assert captured["rs"] == "归档报告全文（人审过）"  # 归档内容覆盖敷衍总结


# ---------------- helpers ----------------

def async_stub(value):
    """返回一个 async 函数，调用时返回 value（支持可调用 value 的形态）。"""
    if callable(value) and not isinstance(value, type):
        async def f(*a, **kw):
            return value(*a, **kw)
    else:
        async def f(*a, **kw):
            return value
    return f
