"""ORS-1010 相似工单余弦闸门 + 卡正文透出的单测。

背景：沉淀卡少时 RRF 排名第一≠语义相关（测试库 1 张卡，任何归档单都推荐它），
余弦分与库大小无关，用生产同款 bge-base-zh-v1.5 标定：无关 0.39~0.49 /
沾边 0.67 / 同题 0.86 → 缺省下限 0.55。
"""
import asyncio
import sys
import os
import pytest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.core.retrieval import (
    RetrievalResult,
    rank_similar_tickets,
    similar_cosine_floor,
)


def _hit(tid, rrf=0.02, vec=0.8, content="问题：…\n根因：…\n解决：…"):
    return RetrievalResult(
        id=str(tid), score=rrf, title=f"工单{tid}", content=content,
        vector_score=vec, task_id=str(tid), review_status="approved",
    )


class TestCosineFloor:
    def test_default_and_env_override(self, monkeypatch):
        monkeypatch.delenv("SIMILAR_TICKET_MIN_COSINE", raising=False)
        assert similar_cosine_floor() == 0.55
        monkeypatch.setenv("SIMILAR_TICKET_MIN_COSINE", "0.6")
        assert similar_cosine_floor() == 0.6
        monkeypatch.setenv("SIMILAR_TICKET_MIN_COSINE", "0")
        assert similar_cosine_floor() == 0.0
        monkeypatch.setenv("SIMILAR_TICKET_MIN_COSINE", "abc")
        assert similar_cosine_floor() == 0.55

    def test_search_filters_by_vector_score(self, monkeypatch):
        """无关卡（余弦 0.49）被过滤，相关卡（0.86）保留；纯稀疏命中（无 dense
        分）同样出局——没有语义证据的兜进来就是用户投诉的形态。"""
        from ai.core.retrieval import RetrievalService
        svc = object.__new__(RetrievalService)

        async def _fake_retrieve(q, top_k):
            return [
                _hit(462, vec=0.49),   # 无关（库小排第一，但语义不像）
                _hit(775, vec=0.86),   # 相关
                _hit(639, vec=0.0),    # 纯稀疏命中，无语义证据
            ]

        monkeypatch.setattr(svc, "retrieve_task_resolutions", _fake_retrieve)
        monkeypatch.setenv("SIMILAR_TICKET_MIN_COSINE", "0.55")
        out = asyncio.run(svc.search_similar_tickets("货叉防掉落", exclude_task_id="999"))
        tids = [r["task_id"] for r in out]
        assert tids == [775], f"应只留余弦过线的 775: {tids}"

    def test_floor_disabled_passes_all(self, monkeypatch):
        monkeypatch.setenv("SIMILAR_TICKET_MIN_COSINE", "0")
        from ai.core.retrieval import RetrievalService
        svc = object.__new__(RetrievalService)

        async def _fake_retrieve(q, top_k):
            return [_hit(462, vec=0.3), _hit(639, vec=0.0)]

        monkeypatch.setattr(svc, "retrieve_task_resolutions", _fake_retrieve)
        out = asyncio.run(svc.search_similar_tickets("任意", exclude_task_id="1"))
        assert [r["task_id"] for r in out] == [462, 639]


class TestSummaryPassthrough:
    def test_rank_includes_summary(self):
        out = rank_similar_tickets([_hit(462, content="问题：货叉防掉落\n根因：车端控制\n解决：确认车型")])
        assert out[0]["summary"].startswith("问题：货叉防掉落")

    def test_summary_truncated(self):
        out = rank_similar_tickets([_hit(1, content="长" * 2000)])
        assert len(out[0]["summary"]) == 600

    def test_summary_empty_when_no_content(self):
        out = rank_similar_tickets([_hit(2, content="")])
        assert out[0]["summary"] == ""

    def test_dedup_and_exclude_unchanged(self):
        """同事既有语义不回归：去重 + 排除自身。"""
        hits = [_hit(202), _hit(202), _hit(305)]
        out = rank_similar_tickets(hits, exclude_task_id="305")
        assert [r["task_id"] for r in out] == [202]
