# -*- coding: utf-8 -*-
"""工单卡集合隔离（TICKET_CARD_COLLECTION）测试。

验证：override 生效时工单卡读写全部走独立集合、ID 加 test: 命名空间、
ensure 不写 company 指针；无 override 时行为与旧版一致。
"""
import uuid

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


class FakeQdrant:
    def __init__(self, collections=()):
        self.is_unavailable = False
        self._cols = set(collections)
        self.created = []
        self.upserted = []

    async def _ensure_client(self):
        return self

    async def collection_exists(self, name):
        return name in self._cols

    async def create_collection(self, **kw):
        self._cols.add(kw.get("collection_name", ""))
        self.created.append(kw.get("collection_name", ""))

    async def search_dense(self, *a, **kw):
        return []

    async def search_sparse(self, *a, **kw):
        return []

    def _to_thread(self, f, *a, **kw):
        return f(*a, **kw)  # wrapper 的线程池调用：fake 直调

    async def upsert(self, collection_name=None, points=None, **kw):
        self.upserted.append((collection_name, [str(p.id) for p in points]))
        return True


class FakeReranker:
    def __init__(self):
        self.name = ""


def make_service(monkeypatch, collections=()):
    svc = retrieval.RetrievalService.__new__(retrieval.RetrievalService)
    svc._embed_client = FakeEmbed()
    svc._qdrant = FakeQdrant(collections)
    svc._reranker = FakeReranker()
    # 注：这里**故意不再给 svc 塞 `_to_thread`**。以前塞了一个 lambda，
    # 等于替实现补了一个 `RetrievalService` 上根本不存在的成员 —— 于是
    # `ensure_task_resolutions_collection` 里 4 处 `self._to_thread(...)`
    # 的 AttributeError 被这行测试掩盖了两个月（生产 2026-10-15 才炸）。
    # 线程池调用应当走 `self._qdrant._to_thread`（FakeQdrant 里有）。

    async def ensure_clients():
        return None

    svc._ensure_clients = ensure_clients
    return svc


@pytest.fixture
def patch_config(monkeypatch):
    holder = {"override": ""}

    class Cfg:
        ticket_card_collection = ""

    cfg = Cfg()
    holder["cfg"] = cfg

    def fake_cfg():
        return cfg

    monkeypatch.setattr("ai.config.get_ai_config", fake_cfg)
    return holder


# ---------------- override 生效 ----------------

@pytest.mark.asyncio
async def test_index_writes_override_collection_and_test_id(patch_config, monkeypatch):
    patch_config["cfg"].ticket_card_collection = "test_ticket_resolutions"
    svc = make_service(monkeypatch, collections={"test_ticket_resolutions"})
    monkeypatch.setattr(retrieval, "generate_bm25_sparse", lambda t: {1: 1.0})

    ok = await svc.index_task_resolution(
        task_id="770", title="T", root_cause="r", solution_steps="s",
        problem_summary="p", review_status="approved")
    assert ok is True
    col, ids = svc._qdrant.upserted[0]
    assert col == "test_ticket_resolutions"
    # ID 带 test: 命名空间，与生产同号工单不撞
    assert ids[0] == str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    "test:yaorenba:task_resolution:770"))


@pytest.mark.asyncio
async def test_index_without_override_uses_pointer_and_plain_id(patch_config, monkeypatch):
    patch_config["cfg"].ticket_card_collection = ""
    svc = make_service(monkeypatch, collections={"company_20260922"})
    monkeypatch.setattr(retrieval, "generate_bm25_sparse", lambda t: {1: 1.0})
    monkeypatch.setattr("ai.config.get_active_collection_for", lambda d: "company_20260922")

    ok = await svc.index_task_resolution(
        task_id="770", title="T", root_cause="r", solution_steps="s",
        problem_summary="p", review_status="approved")
    assert ok is True
    col, ids = svc._qdrant.upserted[0]
    assert col == "company_20260922"
    assert ids[0] == str(uuid.uuid5(uuid.NAMESPACE_URL,
                                    "yaorenba:task_resolution:770"))


@pytest.mark.asyncio
async def test_retrieve_passes_override_collection(patch_config, monkeypatch):
    patch_config["cfg"].ticket_card_collection = "test_ticket_resolutions"
    svc = make_service(monkeypatch)
    captured = {}

    async def fake_domain(query, domain, top_k=3, sub_domain=None,
                          query_filter=None, skip_rerank=False, collection=""):
        captured["collection"] = collection
        captured["sub_domain"] = sub_domain
        return []

    monkeypatch.setattr(svc, "retrieve_domain", fake_domain)
    await svc.retrieve_task_resolutions("定位失败")
    assert captured["collection"] == "test_ticket_resolutions"
    assert captured["sub_domain"] == "ticket_resolutions"


@pytest.mark.asyncio
async def test_ensure_override_creates_without_writing_pointer(patch_config, monkeypatch):
    patch_config["cfg"].ticket_card_collection = "test_ticket_resolutions"
    svc = make_service(monkeypatch)
    written = []

    monkeypatch.setattr("ai.config.write_active_collection_for",
                        lambda d, n: written.append((d, n)))
    col = await svc.ensure_task_resolutions_collection()
    assert col == "test_ticket_resolutions"
    assert svc._qdrant.created == ["test_ticket_resolutions"]
    assert written == []  # 绝不写 company 指针


@pytest.mark.asyncio
async def test_ensure_without_override_still_uses_pointer(patch_config, monkeypatch):
    patch_config["cfg"].ticket_card_collection = ""
    svc = make_service(monkeypatch, collections={"company_x"})
    monkeypatch.setattr("ai.config.get_active_collection_for", lambda d: "company_x")
    col = await svc.ensure_task_resolutions_collection()
    assert col == "company_x"
    assert svc._qdrant.created == []
