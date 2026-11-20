"""@# 相似工单：向量结果去重 / 排除自身（不连 Qdrant）。"""
from ai.core.retrieval import RetrievalResult, rank_similar_tickets


def _hit(task_id, title, rrf=0.03, cosine=0.72, verified="confirmed"):
    return RetrievalResult(
        id=f"pt-{task_id}",
        score=rrf,
        vector_score=cosine,
        title=title,
        content="",
        verified=verified,
        task_id=str(task_id),
    )


def test_rank_similar_tickets_dedup_and_exclude():
    hits = [
        _hit(101, "充电失败", rrf=0.032, cosine=0.86),
        _hit(101, "充电失败（重复点）", rrf=0.028, cosine=0.80),
        _hit(202, "当前工单自己", rrf=0.025, cosine=0.70),
        _hit(303, "同类充电桩", rrf=0.020, cosine=0.61),
        _hit("", "无 task_id", rrf=0.015, cosine=0.50),
    ]
    out = rank_similar_tickets(hits, exclude_task_id="202", top_k=5)
    assert [x["task_id"] for x in out] == [101, 303]
    assert out[0]["title"] == "充电失败"
    assert out[0]["verified"] == "confirmed"
    # 出参 score = dense 余弦，不是 RRF 小分
    assert out[0]["score"] == 0.86
    assert out[0]["rrf_score"] == 0.032
    assert out[0]["score"] >= 0.5
    assert out[0]["rrf_score"] < 0.1


def test_rank_similar_tickets_empty_query_hits():
    assert rank_similar_tickets([], exclude_task_id="1", top_k=3) == []
