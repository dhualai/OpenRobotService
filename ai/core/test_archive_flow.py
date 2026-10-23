import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai.core import archive_flow


def test_report_prompts_use_knowledge_capture_terminology():
    for prompt in [archive_flow._SYSTEM_PROMPT, archive_flow._REPORT_PROMPT]:
        assert '知识沉淀报告' in prompt
        assert '归档报告' not in prompt


def test_prompt_requires_eight_report_sections_and_no_invented_facts():
    headings = ('问题概述', '问题场景', '复现条件', '原因分析', '解决方案', '验证结果', '适用范围', '遗留问题')
    for heading in headings:
        assert f'## {heading}' in archive_flow._REPORT_PROMPT
    assert '禁止编造' in archive_flow._SYSTEM_PROMPT
    assert '不写人员姓名' in archive_flow._REPORT_PROMPT


def test_background_writeback_does_not_overwrite_submitted_report(monkeypatch):
    database = MagicMock()
    database.execute.return_value.rowcount = 0
    monkeypatch.setitem(sys.modules, 'ai.core.database', SimpleNamespace(SessionLocal=lambda: database))
    assert not archive_flow._writeback_if_empty(7, '生成的报告')
    statement, values = database.execute.call_args.args
    assert "content = ''" in str(statement)
    assert "submit_status IN ('draft', 'rejected')" in str(statement)
    assert values == {'c': '生成的报告', 'rid': 7}
    database.close.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_worker_drives_solution_sink_archived_path(monkeypatch):
    """归档入库只留一条驱动：队列与处理都走 solution_sink。

    收口前这里自己拼 SQL、调 process_ticket、再补写 archive_knowledge_indexed，
    与 knowledge_worker 的归档分支各跑一遍（两套标记、两套语义）。这条钉住：
    走 load_archived_candidates + process_archived_ticket，且 worker 自己不再碰 DB、
    不再调 process_ticket。
    """
    import asyncio
    from unittest.mock import AsyncMock

    from ai.core import solution_sink

    seen: dict = {}
    row = {"id": 42, "report_version": 3}

    def fake_load(limit):
        seen["limit"] = limit
        return [row]

    async def fake_process(item, retriever, dry_run=False):
        seen["processed"] = dict(item)
        stop.set()
        return "indexed"

    async def fake_retriever():
        seen["retriever"] = True
        return object()

    database = MagicMock()
    monkeypatch.setattr(solution_sink, "load_archived_candidates", fake_load)
    monkeypatch.setattr(solution_sink, "process_archived_ticket", fake_process)
    monkeypatch.setattr(solution_sink, "process_ticket",
                        AsyncMock(side_effect=AssertionError("不应再走 process_ticket")))
    monkeypatch.setattr("ai.core.get_retrieval_service", fake_retriever)
    monkeypatch.setitem(sys.modules, 'ai.core.database', SimpleNamespace(SessionLocal=lambda: database))

    stop = asyncio.Event()
    await asyncio.wait_for(archive_flow.run_archive_ingest_worker(stop), timeout=5)

    assert seen["processed"] == row          # 行原样交给 process_archived_ticket
    assert seen["limit"] == 10               # 批量大小沿用原来的 10
    assert seen["retriever"] is True
    solution_sink.process_ticket.assert_not_called()
    assert not database.execute.called       # 打标在 process_archived_ticket 里，worker 不自己写
