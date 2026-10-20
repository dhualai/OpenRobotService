"""相似工单供数接口：已沉淀口径 + review 深度（报告/审核人） 的处理器级覆盖。

对应需求：审核界面需要「相似已沉淀工单 + 其审核信息」——
- 相似工单只有一个出口：Qdrant 里 `review_status=approved` 的已沉淀卡（向量召回）；
  Qdrant 不可用即返回空列表，不用 MySQL 兜底。
- scope=review 时另附对应工单最新一版 approved 归档报告（报告正文/审核人）。

本套件隔离 MySQL / AI / Qdrant：只验证纯逻辑与接口接线。
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.tasks.api import task as task_api
from app.modules.tasks.api.task import (
    _attach_reports,
    _similar_item_from_task,
)
from app.modules.tasks.models.ticket import TaskStatus


def _db_with(report_rows, user_rows):
    """两次 execute：① ArchiveReport 查询（.scalars().all()）② 用户查询（.all()）。"""
    res_reports = MagicMock()
    res_reports.scalars.return_value.all.return_value = report_rows
    res_users = MagicMock()
    res_users.all.return_value = user_rows
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[res_reports, res_users])
    return db


# ---------- 列表项 ----------


def test_similar_item_flags_archived():
    archived = _similar_item_from_task(
        SimpleNamespace(id=7, title="充电失败", status=TaskStatus.ARCHIVED, project_name="P")
    )
    assert archived["archived"] is True
    assert archived["status"] == "archived"

    closed = _similar_item_from_task(
        SimpleNamespace(id=8, title="", status=TaskStatus.CLOSED, project_name="")
    )
    assert closed["archived"] is False
    assert closed["title"] == "工单#8"


# ---------- 回表：分数与阈值 ----------


def _db_tasks(rows):
    res = MagicMock()
    res.scalars.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    return db


@pytest.mark.asyncio
async def test_hydrate_carries_score_and_verified_in_vector_order():
    rows = [
        SimpleNamespace(id=101, title="A", status=TaskStatus.CLOSED, project_name="P"),
        SimpleNamespace(id=202, title="B", status=TaskStatus.CLOSED, project_name=""),
    ]
    items = [
        {"task_id": 101, "score": 0.42, "verified": "confirmed"},
        {"task_id": 202, "score": 0.01},
    ]

    out = await task_api._hydrate_similar_from_db(_db_tasks(rows), items, 1, 10)

    assert [it["task_id"] for it in out] == [101, 202]   # 保持向量顺序
    assert out[0]["score"] == 0.42
    assert out[0]["verified"] == "confirmed"
    assert out[1]["score"] == 0.01
    assert out[1]["verified"] == "unknown"               # 缺省兜底


@pytest.mark.asyncio
async def test_hydrate_min_score_filters_then_truncates():
    rows = [
        SimpleNamespace(id=101, title="A", status=TaskStatus.CLOSED, project_name=""),
        SimpleNamespace(id=202, title="B", status=TaskStatus.CLOSED, project_name=""),
    ]
    items = [{"task_id": 101, "score": 0.42}, {"task_id": 202, "score": 0.01}]

    out = await task_api._hydrate_similar_from_db(
        _db_tasks(rows), items, 1, 10, min_score=0.1)

    assert [it["task_id"] for it in out] == [101]


# ---------- 报告补全 ----------


@pytest.mark.asyncio
async def test_attach_reports_latest_approved_with_reviewer_name():
    now = dt.datetime(2026, 10, 14, 10, 0, 0)
    # 查询已按 version desc 排序，首行即最新版
    r_new = SimpleNamespace(task_id=101, version=2, submit_status="approved",
                            content="新报告", reviewer="u1", reviewed_at=now)
    r_old = SimpleNamespace(task_id=101, version=1, submit_status="approved",
                            content="旧报告", reviewer="u1", reviewed_at=now)
    db = _db_with([r_new, r_old],
                  [SimpleNamespace(id="u1", username="zhang", name="张三")])

    items = [{"task_id": 101}, {"task_id": 202}]
    await _attach_reports(db, items)

    assert items[0]["report"]["content"] == "新报告"
    assert items[0]["report"]["version"] == 2
    assert items[0]["report"]["reviewer_name"] == "张三"
    assert items[0]["report"]["reviewed_at"] == "2026-10-14T10:00:00"
    # 无 approved 报告的相似单：显式置 None
    assert items[1]["report"] is None


@pytest.mark.asyncio
async def test_attach_reports_no_ids_is_noop():
    db = AsyncMock()
    await _attach_reports(db, [])
    db.execute.assert_not_called()


# ---------- 接口接线 ----------


class _FakeResp:
    status_code = 200
    content = b"{}"

    def json(self):
        return {"code": 0, "data": {"similar": [{"task_id": 101}]}}


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _FakeResp()


def _wire(monkeypatch, calls):
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    async def plain_text(db, task, text, hide_participants=True):
        return text or ""
    monkeypatch.setattr("app.modules.admin.services.knowledge_service.anonymous_text", plain_text)
    monkeypatch.setattr("app.modules.tasks.services.knowledge_review.review_detail", AsyncMock(return_value={}))

    async def fake_hydrate(db, items, exclude_id, limit, min_score=None):
        return [{"task_id": 101, "title": "旧相似", "status": "closed",
                 "project_name": "", "archived": False, "score": 0.42,
                 "verified": "confirmed"}]

    async def fake_attach(db, items):
        calls["attached"] = [it["task_id"] for it in items]

    monkeypatch.setattr(task_api, "_hydrate_similar_from_db", fake_hydrate)
    monkeypatch.setattr(task_api, "_attach_reports", fake_attach)


@pytest.mark.asyncio
async def test_endpoint_review_scope_attaches_reports(monkeypatch):
    calls: dict = {}
    _wire(monkeypatch, calls)

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(title="充电失败", description="描述"))
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: SimpleNamespace(id=1, submit_status="submitted")))

    out = await task_api.get_similar_tasks(
        task_id=1, limit=10, min_score=0.0, scope="review", db=db)

    assert out["scope"] == "review"
    assert [it["task_id"] for it in out["similar"]] == [101]
    assert calls["attached"] == [101]


@pytest.mark.asyncio
async def test_endpoint_returns_empty_when_vector_unavailable(monkeypatch):
    """单一出口：Qdrant / AI 不可用 → 空列表，不再用 MySQL 兜底召回。"""
    calls: dict = {}
    _wire(monkeypatch, calls)

    class _BoomClient(_FakeClient):
        async def get(self, url, params=None):
            raise RuntimeError("qdrant down")

    monkeypatch.setattr("httpx.AsyncClient", _BoomClient)
    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(title="充电失败", description="描述"))
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: SimpleNamespace(id=1, submit_status="submitted")))

    out = await task_api.get_similar_tasks(
        task_id=1, limit=10, min_score=0.0, scope="review", db=db)

    assert out["similar"] == []
    assert calls.get("attached", []) == []


@pytest.mark.asyncio
async def test_endpoint_unknown_scope_falls_back_to_remind_without_reports(monkeypatch):
    calls: dict = {}
    _wire(monkeypatch, calls)

    db = AsyncMock()
    db.get = AsyncMock(return_value=SimpleNamespace(title="充电失败", description="描述"))

    out = await task_api.get_similar_tasks(
        task_id=1, limit=10, min_score=0.0, scope="weird", db=db)

    assert out["scope"] == "remind"
    assert "attached" not in calls  # remind 深度不补报告
