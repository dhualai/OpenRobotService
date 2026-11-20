"""读取 archive_reviewers 配置：领域 → 审核人名单。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ai.agents.AiDiagnosisPlatform.assigner.archive_review.domains import DOMAINS


@dataclass(frozen=True)
class ArchiveReviewersConfig:
    """领域 → 审核人 users.id 列表；on_empty 固定为 fail。"""

    domains: dict[str, list[str]] = field(default_factory=dict)
    on_empty: str = "fail"

    def reviewers_for(self, domain: str) -> list[str]:
        raw = self.domains.get(domain) or []
        # 去重保序
        seen: set[str] = set()
        out: list[str] = []
        for uid in raw:
            uid = (uid or "").strip()
            if not uid or uid in seen:
                continue
            seen.add(uid)
            out.append(uid)
        return out

    def configured_domains(self) -> list[str]:
        """配置里出现过且名单非空的领域；若全空则退回配置键（便于报「未配置」）。"""
        non_empty = [d for d, ids in self.domains.items() if any((x or "").strip() for x in ids)]
        if non_empty:
            return non_empty
        return list(self.domains.keys())


def parse_archive_reviewers(raw: Mapping[str, Any] | None) -> ArchiveReviewersConfig:
    """从 config.yaml 的 archive_reviewers 块解析。"""
    data = dict(raw or {})
    domains_raw = data.get("domains") or {}
    domains: dict[str, list[str]] = {}
    if isinstance(domains_raw, Mapping):
        for name, ids in domains_raw.items():
            key = str(name or "").strip()
            if not key:
                continue
            if isinstance(ids, str):
                ids = [ids]
            if not isinstance(ids, (list, tuple)):
                continue
            domains[key] = [str(x).strip() for x in ids if str(x).strip()]
    # 未配置任何领域时，用双树名单占位（全空，提交会走「未配置」）
    if not domains:
        domains = {d: [] for d in DOMAINS}
    on_empty = str(data.get("on_empty") or "fail").strip().lower() or "fail"
    return ArchiveReviewersConfig(domains=domains, on_empty=on_empty)


def load_from_assigner_config(assigner_config: Any | None = None) -> ArchiveReviewersConfig:
    """从 AssignerConfig 实例，或 archive_review.store 生效配置。"""
    if assigner_config is not None and hasattr(assigner_config, "archive_reviewers"):
        raw = getattr(assigner_config, "archive_reviewers", None)
        if isinstance(raw, ArchiveReviewersConfig):
            return raw
        if isinstance(raw, Mapping):
            return parse_archive_reviewers(raw)
    try:
        from ai.agents.AiDiagnosisPlatform.assigner.archive_review.store import (
            effective_archive_reviewers,
        )

        return parse_archive_reviewers(effective_archive_reviewers())
    except Exception:
        return parse_archive_reviewers({})
