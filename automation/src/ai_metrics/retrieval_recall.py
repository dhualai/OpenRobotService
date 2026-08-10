"""L2 retrieval recall metric for RAG evaluation.

Works on retrieval results (duck-typed: objects with .title / .content,
or dicts with those keys). Pure Python, no external dependencies.

Expected-hit semantics: a collection counts as "hit" when any result in
the top-k returned by that collection contains any of the expected terms.
"""

from typing import Any, Dict, Iterable, List


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        return f"{item.get('title', '')} {item.get('content', '')}"
    return f"{getattr(item, 'title', '')} {getattr(item, 'content', '')}"


def collection_hit(results: List[Any], terms: Iterable[str]) -> bool:
    """Whether any result (title/content) contains any expected term."""
    if not results:
        return False
    lowered = [t.lower() for t in terms if t.strip()]
    if not lowered:
        return False
    for item in results:
        text = _item_text(item).lower()
        if any(t in text for t in lowered):
            return True
    return False


def recall_score(hits: Dict[str, Any], expected: Dict[str, List[str]]) -> Dict[str, Any]:
    """Summarize recall across expected collections.

    Args:
        hits: {collection: True | False | None}. None marks "unavailable"
            (e.g. retrieval service down) and is excluded from scoring.
        expected: {collection: [terms...]} - collections under test.

    Returns:
        {"recall": float, "hit": [..], "missed": [..], "skipped": [..]}
    """
    expected = dict(expected)
    hit_cols = [c for c in expected if hits.get(c) is True]
    miss_cols = [c for c in expected if hits.get(c) is False]
    skip_cols = [c for c in expected if hits.get(c) is None]
    scored = len(expected) - len(skip_cols)
    recall = len(hit_cols) / scored if scored else 1.0
    return {
        "recall": recall,
        "hit": hit_cols,
        "missed": miss_cols,
        "skipped": skip_cols,
    }
