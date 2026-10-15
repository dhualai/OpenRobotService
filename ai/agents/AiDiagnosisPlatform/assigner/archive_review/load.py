"""在审负荷：按负荷反比加权抽签，负荷越低被抽中概率越高。"""

from __future__ import annotations

import random
from typing import Mapping, Sequence


def _normalized_load(uid: str, loads: Mapping[str, int]) -> int:
    try:
        n = int(loads.get(uid, 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return max(0, n)


def load_weight(load: int) -> float:
    """在审数量 → 抽签权重。weight = 1 / (load + 1)。"""
    return 1.0 / (max(0, int(load)) + 1)


def compute_probabilities(
    candidate_ids: Sequence[str],
    load_counts: Mapping[str, int] | None = None,
) -> dict[str, float]:
    """返回每人被抽中的概率（总和为 1；空名单返回 {}）。"""
    loads = load_counts or {}
    pairs: list[tuple[str, float]] = []
    for uid in candidate_ids:
        uid = (uid or "").strip()
        if not uid:
            continue
        pairs.append((uid, load_weight(_normalized_load(uid, loads))))
    total = sum(w for _, w in pairs)
    if total <= 0 or not pairs:
        return {}
    return {uid: w / total for uid, w in pairs}


def pick_by_load_probability(
    candidate_ids: Sequence[str],
    load_counts: Mapping[str, int] | None = None,
    *,
    rng: random.Random | None = None,
) -> str | None:
    """按负荷反比加权随机选人；空名单返回 None。

    例：负荷 0 / 1 / 3 → 权重 1 / 0.5 / 0.25 → 概率约 57% / 29% / 14%。
    单人候选时必然选中。rng 可注入，便于单测复现。
    """
    probs = compute_probabilities(candidate_ids, load_counts)
    if not probs:
        return None
    # 保持与 candidate_ids 相同的遍历顺序，保证同一 seed 可复现
    ordered = [uid for uid in candidate_ids if (uid or "").strip() and uid.strip() in probs]
    # 去重保序（名单若重复只抽一次）
    seen: set[str] = set()
    ordered_unique: list[str] = []
    for uid in ordered:
        uid = uid.strip()
        if uid in seen:
            continue
        seen.add(uid)
        ordered_unique.append(uid)
    if not ordered_unique:
        return None
    if len(ordered_unique) == 1:
        return ordered_unique[0]

    weights = [probs[uid] for uid in ordered_unique]
    picker = rng if rng is not None else random
    return picker.choices(ordered_unique, weights=weights, k=1)[0]


def snapshot_loads(
    candidate_ids: Sequence[str],
    load_counts: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """为日志生成名单负荷快照。"""
    loads = load_counts or {}
    out: dict[str, int] = {}
    for uid in candidate_ids:
        uid = (uid or "").strip()
        if not uid:
            continue
        out[uid] = _normalized_load(uid, loads)
    return out
