# -*- coding: utf-8 -*-
"""工单方案卡读写必须经 wrapper 的线程池入口（`self._qdrant._to_thread`）。

背景（2026-10-15 生产事故）：`_to_thread` 是 QdrantClientWrapper 的方法，
`_scroll_task_resolution_points` 当初写成 `self._to_thread(...)` —— RetrievalService 上
根本没有这个方法，AttributeError 被上层 try/except 吞掉，于是「归档审核通过后给已有卡
打 patch」这条路一直静默失败；而当时 `ai/tests/test_ticket_card_isolation.py` 给 service
硬塞了一个 `_to_thread`，等于替实现补窟窿，把它掩盖了两个月。

两条测试各钉一半：
  1. 源码级：RetrievalService 里不许出现 `self._to_thread(`；
  2. 行为级：读卡 / patch 卡真的走 wrapper 的 `_to_thread`（fake 记账），不只是能跑通。
"""
import inspect
import re

import pytest

from ai.core import retrieval


class _Vec:
    def __init__(self, v):
        self._v = v

    def tolist(self):
        return self._v


class FakeEmbed:
    async def embed(self, text):
        return _Vec([0.1] * 8)

    async def get_dimension(self):
        return 8


class FakePoint:
    def __init__(self, pid, payload):
        self.id = pid
        self.payload = payload


class FakeQdrant:
    """只实现被调用到的那几个成员；`_to_thread` 记下走了哪些同步调用。"""

    def __init__(self, points):
        self.is_unavailable = False
        self._points = list(points)
        self.thread_calls = []
        self.set_payloads = []

    async def _ensure_client(self):
        return self

    async def _to_thread(self, func, *args, **kwargs):
        self.thread_calls.append(getattr(func, "__name__", "?"))
        return func(*args, **kwargs)

    def scroll(self, collection_name=None, scroll_filter=None, limit=None, **kw):
        return self._points[:limit or 64], None

    def set_payload(self, collection_name=None, payload=None, points=None):
        self.set_payloads.append((collection_name, dict(payload or {}), list(points or [])))
        return True


def _make_service(monkeypatch, points, collection="col_ticket_cards"):
    svc = retrieval.RetrievalService.__new__(retrieval.RetrievalService)
    svc._embed_client = FakeEmbed()
    svc._qdrant = FakeQdrant(points)
    monkeypatch.setattr("ai.config.get_active_collection_for", lambda kind: collection)

    async def ensure_clients():
        return None

    svc._ensure_clients = ensure_clients
    return svc


def test_retrievalservice_不能自己调_to_thread():
    """源码级守卫：这一行曾静默失败两个月，别再回来。"""
    assert hasattr(retrieval.QdrantClientWrapper, "_to_thread")
    assert not hasattr(retrieval.RetrievalService, "_to_thread")

    src = inspect.getsource(retrieval.RetrievalService)
    offenders = [
        line_no + 1
        for line_no, line in enumerate(src.splitlines())
        if re.search(r"self\._to_thread\s*\(", line)
    ]
    assert not offenders, (
        "RetrievalService 里直接调 self._to_thread（应为 self._qdrant._to_thread），"
        f"相对方法体行号：{offenders}"
    )


async def test_读方案卡走_wrapper_的线程池入口(monkeypatch):
    svc = _make_service(monkeypatch, [FakePoint("p1", {"task_id": "T-1", "content": "x"})])

    points = await svc._scroll_task_resolution_points("T-1")

    assert [str(p.id) for p in points] == ["p1"]
    assert svc._qdrant.thread_calls == ["scroll"]


async def test_没有集合时不碰_qdrant(monkeypatch):
    svc = _make_service(monkeypatch, [], collection="")

    assert await svc._scroll_task_resolution_points("T-1") == []
    assert await svc.fetch_task_resolution_payload("T-1") is None
    assert svc._qdrant.thread_calls == []


async def test_patch_方案卡走_wrapper_的线程池入口(monkeypatch):
    svc = _make_service(monkeypatch, [FakePoint("p1", {"task_id": "T-1"}), FakePoint("p2", {"task_id": "T-1"})])

    ok = await svc.patch_task_resolution_payload("T-1", {"review_status": "approved"})

    assert ok is True
    assert svc._qdrant.thread_calls == ["scroll", "set_payload"]
    assert svc._qdrant.set_payloads == [
        ("col_ticket_cards", {"review_status": "approved"}, ["p1", "p2"])
    ]


async def test_找不到卡时_patch_不动_qdrant(monkeypatch):
    svc = _make_service(monkeypatch, [])

    assert await svc.patch_task_resolution_payload("T-404", {"verified": "confirmed"}) is False
    assert svc._qdrant.thread_calls == ["scroll"]


@pytest.mark.parametrize("bad", ["bogus"])
def test_verified_取值仍受约束(bad):
    """顺带钉住：非法 verified 直接 False，不去读卡。"""
    svc = retrieval.RetrievalService.__new__(retrieval.RetrievalService)

    async def run():
        return await svc.update_task_resolution_verified("T-1", bad)

    import asyncio

    assert asyncio.run(run()) is False
