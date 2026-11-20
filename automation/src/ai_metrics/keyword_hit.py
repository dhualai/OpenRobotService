"""L1 deterministic keyword hit metric for AI agent outputs.

Measures how many expected keywords appear in the agent's reply.
Used to verify the reply covers required information points without
requiring exact LLM output matching.
"""

from typing import Iterable, List, Optional


def hit_ratio(text: str, keywords: Iterable[str]) -> float:
    """Fraction of keywords found in text (0.0 ~ 1.0)."""
    if not text:
        return 0.0
    text_lower = text.lower()
    found = 0
    total = 0
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        total += 1
        if kw.lower() in text_lower:
            found += 1
    return found / total if total else 1.0


def missing_keywords(text: str, keywords: Iterable[str]) -> List[str]:
    """Return keywords that are not present in text."""
    text_lower = text.lower()
    return [kw for kw in keywords if kw.strip() and kw.lower() not in text_lower]


def keyword_hit_passed(
    text: str,
    keywords: Iterable[str],
    threshold: float = 0.8,
    min_hits: Optional[int] = None,
) -> bool:
    """Check whether keyword coverage meets the threshold.

    Args:
        text: Agent reply to check.
        keywords: Required information-point keywords.
        threshold: Minimum hit ratio (default 0.8).
        min_hits: Optional absolute minimum hit count; overrides ratio when set.
    """
    ratio = hit_ratio(text, keywords)
    if min_hits is not None:
        found = len(keywords) - len(missing_keywords(text, keywords)) if keywords else 0
        return found >= min_hits
    return ratio >= threshold
