"""工单卡集合解析（TICKET_CARD_COLLECTION override）的就近单测。

背景：test 环境把 override 指向隔离集合 `test_ticket_resolutions`，而该集合是
「第一次写卡时惰性创建」的（ensure_task_resolutions_collection 的 override 分支）。
旧写法 `col = override or company指针` 把 override 当成「Qdrant 里已存在」，
于是 ensure 永远不被调用 → 集合永远建不出来 → 写卡与检索全部 404，
审核页「相似工单参考」显示「检索暂不可用」。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.core import retrieval as retrieval_mod
from ai.core.retrieval import RetrievalService


class _Vec(list):
    """假的嵌入向量：只需要 .tolist()。"""

    def tolist(self):
        return list(self)


def _service(ensure_returns: str):
    svc = RetrievalService()
    svc._ensure_clients = AsyncMock()
    svc._embed_client = SimpleNamespace(embed=AsyncMock(return_value=_Vec([0.1, 0.2])))
    upsert = AsyncMock()
    client = SimpleNamespace(upsert=upsert)

    async def _to_thread(fn, *args, **kwargs):
        return await fn(*args, **kwargs)

    svc._qdrant = SimpleNamespace(
        is_unavailable=False,
        _ensure_client=AsyncMock(return_value=client),
        _to_thread=_to_thread,
    )
    svc.ensure_task_resolutions_collection = AsyncMock(return_value=ensure_returns)
    return svc, upsert


async def _write(svc: RetrievalService) -> bool:
    return await svc.index_task_resolution(
        task_id="770", title="充电桩无法启动",
        root_cause="通信板故障", solution_steps="换通信板",
    )


async def test_override_collection_is_ensured_before_write(monkeypatch):
    """override 只是声明「该用哪个集合」，Qdrant 里可能还没有 —— 写卡前必须 ensure。"""
    monkeypatch.setattr(retrieval_mod, "_ticket_card_collection",
                        lambda: "test_ticket_resolutions")
    svc, upsert = _service("test_ticket_resolutions")

    assert await _write(svc) is True
    svc.ensure_task_resolutions_collection.assert_awaited_once()
    assert upsert.await_args.kwargs["collection_name"] == "test_ticket_resolutions"


async def test_ensure_override_branch_initializes_clients_first(monkeypatch):
    """ensure 的 override 分支必须先把 client 建出来。

    新构造的 service 上 `self._qdrant` 是 None，旧代码在 `_ensure_clients()` 之前
    就取它 → AttributeError 被 except 吞掉、返回 ""，于是集合永远建不出来
    （test 环境 `test_ticket_resolutions` 从未创建的真因）。
    """
    monkeypatch.setattr(retrieval_mod, "_ticket_card_collection",
                        lambda: "test_ticket_resolutions")
    svc = RetrievalService()
    created: dict = {}

    class _Client:
        async def collection_exists(self, name):
            return False

        async def create_collection(self, **kwargs):
            created.update(kwargs)

    async def _to_thread(fn, *args, **kwargs):
        return await fn(*args, **kwargs)

    async def _ensure_clients():          # 复刻真实职责：把客户端建出来
        svc._qdrant = SimpleNamespace(
            _ensure_client=AsyncMock(return_value=_Client()),
            _to_thread=_to_thread,
        )
        svc._embed_client = SimpleNamespace(
            get_dimension=AsyncMock(return_value=1024))

    svc._ensure_clients = _ensure_clients

    assert await svc.ensure_task_resolutions_collection() == "test_ticket_resolutions"
    assert created["collection_name"] == "test_ticket_resolutions"
    assert set(created["vectors_config"]) == {"dense"}      # 与主入库链路同 schema
    assert set(created["sparse_vectors_config"]) == {"sparse"}


async def test_company_pointer_path_does_not_touch_collections(monkeypatch):
    """生产无 override：已有 company 指针就直接写，不触发建集合（保持原行为）。"""
    monkeypatch.setattr(retrieval_mod, "_ticket_card_collection", lambda: "")
    # get_active_collection_for 是在函数体内从 ai.config 现取的，得打在它身上
    import ai.config as ai_config
    monkeypatch.setattr(ai_config, "get_active_collection_for",
                        lambda domain: "company_20260101_000000")
    svc, upsert = _service("company_20260101_000000")

    assert await _write(svc) is True
    svc.ensure_task_resolutions_collection.assert_not_awaited()
    assert upsert.await_args.kwargs["collection_name"] == "company_20260101_000000"
