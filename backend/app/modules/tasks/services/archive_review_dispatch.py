"""归档审核人派审桥接：后端 submit 时调用 assigner.archive_review。

不走工程师派单 DispatchFlow / task_dispatch_log。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import UserDB
from app.models.task import ArchiveReport, Task

logger = logging.getLogger(__name__)

# 模块级缓存：避免每次 submit 重复改 sys.path / import
_ASSIGNER_CACHE: tuple | None = None


def _ensure_repo_on_path() -> None:
    # .../backend/app/modules/tasks/services/this.py → repo root = parents[5]
    repo = Path(__file__).resolve().parents[5]
    root = str(repo)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_assigner():
    global _ASSIGNER_CACHE
    if _ASSIGNER_CACHE is not None:
        return _ASSIGNER_CACHE
    _ensure_repo_on_path()
    from ai.agents.AiDiagnosisPlatform.assigner.archive_review.schemas import (
        ArchiveReviewAssignment,
        ArchiveReviewError,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.archive_review.service import (
        assign_archive_reviewer,
    )
    from ai.agents.AiDiagnosisPlatform.assigner.archive_review.store import (
        effective_archive_reviewers,
    )

    _ASSIGNER_CACHE = (
        assign_archive_reviewer,
        ArchiveReviewError,
        ArchiveReviewAssignment,
        effective_archive_reviewers,
    )
    return _ASSIGNER_CACHE


async def expand_exclude_ids(
    db: AsyncSession,
    seeds: list[str] | None,
) -> list[str]:
    """把工单相关人扩成可比标识集合（users.id / username / wechat_openid）。

    名单里存的是 users.id，操作人/处理人/提单人可能是 openid 或 username；
    脱敏比较前统一展开，避免身份形态不一致导致避嫌失效。
    """
    raw = [str(x).strip() for x in (seeds or []) if (x or "").strip()]
    if not raw:
        return []

    out: set[str] = set(raw)
    # 同步身份表：id / username（及已缓存的 wechat_openid）
    try:
        from app.core.user_identity import identity_keys

        for seed in raw:
            out.update(identity_keys(seed))
    except Exception:  # noqa: BLE001 — 查身份失败时仍用原值
        pass

    # 异步再扫 wechat_openid：业务账号绑定 openid 时 identity 缓存可能尚未带该字段
    try:
        stmt = select(UserDB.id, UserDB.username, UserDB.wechat_openid).where(
            or_(
                UserDB.id.in_(list(out)),
                UserDB.username.in_(list(out)),
                UserDB.wechat_openid.in_(list(out)),
            )
        )
        result = await db.execute(stmt)
        for row in result.all():
            for val in (row.id, row.username, row.wechat_openid):
                s = (val or "").strip() if isinstance(val, str) else str(val or "").strip()
                if s:
                    out.add(s)
    except Exception:  # noqa: BLE001
        logger.exception("expand_exclude_ids 查用户失败，回落原 exclude")

    return sorted(out)


async def count_submitted_loads(
    db: AsyncSession,
    reviewer_ids: list[str] | None = None,
) -> dict[str, int]:
    """统计 submit_status=submitted 的在审数量。"""
    stmt = (
        select(ArchiveReport.reviewer, func.count())
        .where(ArchiveReport.submit_status == "submitted")
        .where(ArchiveReport.reviewer.is_not(None))
        .where(ArchiveReport.reviewer != "")
        .group_by(ArchiveReport.reviewer)
    )
    if reviewer_ids:
        stmt = stmt.where(ArchiveReport.reviewer.in_(list(reviewer_ids)))
    result = await db.execute(stmt)
    out: dict[str, int] = {}
    for reviewer, cnt in result.all():
        if reviewer:
            out[str(reviewer)] = int(cnt or 0)
    return out


async def dispatch_archive_reviewer(
    db: AsyncSession,
    *,
    task: Task,
    report: ArchiveReport,
    operator_id: str,
    content_override: Optional[str] = None,
) -> Any:
    """判定领域并按负荷概率选出审核人。失败抛 ArchiveReviewError。"""
    (
        assign_archive_reviewer,
        ArchiveReviewError,
        _Assignment,
        effective_archive_reviewers,
    ) = _import_assigner()

    try:
        cfg = effective_archive_reviewers()
    except Exception as exc:  # noqa: BLE001 — 配置异常视为服务失败
        logger.exception("加载 archive_reviewers 配置失败")
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc

    # 先取全量在审负荷；抽签只使用候选子集
    try:
        loads = await count_submitted_loads(db)
    except Exception as exc:  # noqa: BLE001
        logger.exception("统计归档在审负荷失败")
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc

    report_body = content_override if content_override is not None else (report.content or "")
    # metadata / AI 摘要里常含领域线索（产品、故障摘要），拼进判定文本
    meta_bits: list[str] = []
    meta = getattr(task, "metadata_info", None) or {}
    if isinstance(meta, dict):
        for key in ("product", "robot_type", "ai_summary", "resolution_summary"):
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                meta_bits.append(val.strip())
        diagnosis = meta.get("diagnosis")
        if isinstance(diagnosis, dict):
            for key in ("problem_summary",):
                val = diagnosis.get(key)
                if isinstance(val, str) and val.strip():
                    meta_bits.append(val.strip())
        product_hint = str(meta.get("product") or "").strip()
    else:
        product_hint = ""

    enriched_report = "\n".join([report_body, *meta_bits]).strip()
    # 工单相关人优先脱敏（不审自己的单）；名单只剩这些人时由 assign 侧回落仍可派
    exclude_ids = await expand_exclude_ids(
        db,
        [
            operator_id,
            getattr(task, "assigned_to", None),
            getattr(task, "created_by", None),
        ],
    )
    try:
        return assign_archive_reviewer(
            title=task.title or "",
            description=task.description or "",
            report_content=enriched_report,
            project_name=task.project_name or "",
            product=product_hint,
            department=(getattr(task, "team", None) or "") or "",
            operator_id=operator_id,
            exclude_ids=exclude_ids,
            load_counts=loads,
            config=cfg,
        )
    except ArchiveReviewError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("归档派审选人异常 task_id=%s report_id=%s", task.id, report.id)
        raise ArchiveReviewError("service_error", "归档派审服务异常，请稍后重试") from exc


def assignment_log_detail(assignment: Any) -> dict[str, Any]:
    """写入操作日志 detail 的结构化快照。"""
    probs = getattr(assignment, "probability_snapshot", None) or {}
    return {
        "domain": getattr(assignment, "domain", None),
        "reviewer": getattr(assignment, "reviewer_id", None),
        "candidates": list(getattr(assignment, "candidate_ids", None) or []),
        "load_snapshot": dict(getattr(assignment, "load_snapshot", None) or {}),
        "probability_snapshot": {k: round(float(v), 4) for k, v in probs.items()},
        "reason": getattr(assignment, "reason", None),
        "source": "archive_review_dispatch",
    }
