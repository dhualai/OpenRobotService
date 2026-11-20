"""归档审核人派审入口：领域 → 名单 →（多人才脱敏，无人回落）→ 按负荷概率抽签。"""

from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from ai.agents.AiDiagnosisPlatform.assigner.archive_review.config import (
    ArchiveReviewersConfig,
    load_from_assigner_config,
    parse_archive_reviewers,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domain import infer_domain
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.load import (
    compute_probabilities,
    pick_by_load_probability,
    snapshot_loads,
)
from ai.agents.AiDiagnosisPlatform.assigner.archive_review.schemas import (
    ArchiveReviewAssignment,
    ArchiveReviewError,
)


def assign_archive_reviewer(
    *,
    title: str = "",
    description: str = "",
    report_content: str = "",
    project_name: str = "",
    product: str = "",
    department: str = "",
    operator_id: str,
    exclude_ids: Sequence[str] | None = None,
    load_counts: Mapping[str, int] | None = None,
    config: ArchiveReviewersConfig | Mapping[str, Any] | None = None,
    domain_hint: str | None = None,
    project_markers: Sequence[tuple[str, str]] | None = None,
    rng: random.Random | None = None,
) -> ArchiveReviewAssignment:
    """按配置选出一名审核人。失败抛 ArchiveReviewError。

    load_counts: reviewer_id → 当前 submit_status=submitted 的在审数量。
    选人按 weight=1/(load+1) 加权抽签，负荷越低概率越高，不是必派给最少的人。

    脱敏（仅领域名单 ≥2 人时）：operator_id 与 exclude_ids（处理人/提单人等）
    优先从候选剔除；剔除后无人则回落原名单。名单仅 1 人时跳过脱敏直接派。

    调用方（后端接入时）负责查库传入；本函数不连库。
    """
    cfg = _resolve_config(config)
    operator = (operator_id or "").strip()
    exclude = _normalize_exclude_ids(operator, exclude_ids)

    # 只从「配置里出现过的领域」中判（有名单的优先，否则用配置键）
    allowed = cfg.configured_domains()

    domain = (domain_hint or "").strip() or None
    if domain and domain not in cfg.domains:
        domain = None
    if not domain:
        domain = infer_domain(
            title=title,
            description=description,
            report_content=report_content,
            project_name=project_name,
            product=product,
            department=department,
            allowed_domains=allowed,
            project_markers=project_markers,
        )
    if not domain:
        raise ArchiveReviewError(
            "domain_unknown",
            "无法判定问题领域，请完善工单或报告后再提交",
        )

    candidates = cfg.reviewers_for(domain)
    if not candidates:
        raise ArchiveReviewError(
            "no_reviewers",
            f"问题领域「{domain}」未配置审核人",
        )

    # 先定领域名单；仅多人时脱敏，单人直接派（无需避嫌开销）
    desensitize = len(candidates) >= 2
    fallback = False
    if desensitize:
        preferred = [uid for uid in candidates if uid not in exclude]
        if preferred:
            pool = preferred
        else:
            pool = list(candidates)
            fallback = True
    else:
        pool = list(candidates)

    loads = snapshot_loads(pool, load_counts)
    probs = compute_probabilities(pool, loads)
    picked = pick_by_load_probability(pool, loads, rng=rng)
    if not picked:
        raise ArchiveReviewError("no_available", "没有可用审核人")

    prob_text = {uid: round(p, 4) for uid, p in probs.items()}
    if not desensitize:
        pool_note = f"名单仅1人跳过脱敏；候选={pool}"
    elif fallback:
        pool_note = f"脱敏排除={sorted(exclude)}；脱敏后无人，回落原名单={pool}"
    else:
        pool_note = f"脱敏排除={sorted(exclude)}；脱敏后候选={pool}"
    reason = (
        f"领域={domain}；{pool_note}；"
        f"负荷={loads}；抽签概率={prob_text}；选中={picked}"
        f"（权重=1/(在审+1)，按概率抽签）"
    )
    return ArchiveReviewAssignment(
        domain=domain,
        reviewer_id=picked,
        load_snapshot=loads,
        probability_snapshot=probs,
        candidate_ids=list(pool),
        reason=reason,
    )


def _normalize_exclude_ids(
    operator: str,
    exclude_ids: Sequence[str] | None,
) -> set[str]:
    out: set[str] = set()
    if operator:
        out.add(operator)
    for raw in exclude_ids or ():
        s = (raw or "").strip()
        if s:
            out.add(s)
    return out


def _resolve_config(
    config: ArchiveReviewersConfig | Mapping[str, Any] | None,
) -> ArchiveReviewersConfig:
    if isinstance(config, ArchiveReviewersConfig):
        return config
    if isinstance(config, Mapping):
        return parse_archive_reviewers(config)
    return load_from_assigner_config(None)
