"""归档审核人派审：提交归档报告时按问题领域 + 在审负荷选人。

不进入 DispatchFlow，不写 task_dispatch_log。

本包 __init__ 保持轻量：后端只读配置时应 ``from ...archive_review.store import ...``，
避免顶层拉起 service。
"""

from __future__ import annotations

__all__ = [
    "ArchiveReviewAssignment",
    "ArchiveReviewError",
    "assign_archive_reviewer",
    "effective_archive_reviewers",
    "save_archive_reviewers_overrides",
    "normalize_archive_reviewers",
    "invalidate_archive_reviewers_cache",
    "DOMAINS",
]


def __getattr__(name: str):
    if name in ("ArchiveReviewAssignment", "ArchiveReviewError"):
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review.schemas import (
            ArchiveReviewAssignment,
            ArchiveReviewError,
        )

        return {
            "ArchiveReviewAssignment": ArchiveReviewAssignment,
            "ArchiveReviewError": ArchiveReviewError,
        }[name]
    if name == "assign_archive_reviewer":
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review.service import (
            assign_archive_reviewer,
        )

        return assign_archive_reviewer
    if name in (
        "effective_archive_reviewers",
        "save_archive_reviewers_overrides",
        "normalize_archive_reviewers",
        "invalidate_archive_reviewers_cache",
    ):
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review import store as _store

        return getattr(_store, name)
    if name == "DOMAINS":
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domains import DOMAINS

        return DOMAINS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
