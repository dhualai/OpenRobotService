"""@# 相似工单：向量结果去重 / 排除自身（不连 Qdrant）。"""
from ai.core.retrieval import RetrievalResult, rank_similar_tickets


def _hit(task_id, title, score=1.0, verified="confirmed"):
    return RetrievalResult(
        id=f"pt-{task_id}",
        score=score,
        title=title,
        content="",
        verified=verified,
        task_id=str(task_id),
    )


def test_rank_similar_tickets_dedup_and_exclude():
    hits = [
        _hit(101, "充电失败", 0.9),
        _hit(101, "充电失败（重复点）", 0.8),
        _hit(202, "当前工单自己", 0.7),
        _hit(303, "同类充电桩", 0.6),
        _hit("", "无 task_id", 0.5),
    ]
    out = rank_similar_tickets(hits, exclude_task_id="202", top_k=5)
    assert [x["task_id"] for x in out] == [101, 303]
    assert out[0]["title"] == "充电失败"
    assert out[0]["verified"] == "confirmed"


def test_rank_similar_tickets_empty_query_hits():
    assert rank_similar_tickets([], exclude_task_id="1", top_k=3) == []
