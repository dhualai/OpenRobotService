"""评论区参与人头像外显 —— 批量聚合服务。

背景：
    工单列表卡片需要展示「评论区参与讨论人员」的头像堆叠（发起人 | 堆叠 | 处理人），
    数据源是 ``task_participants`` 表（评论成功后同事务幂等 upsert，见
    ``api/task.py`` 的评论端点）。

本模块解决三件事（全部为「批量」实现，列表页严禁 N+1）：
    1. **名单 + 排序**：按参与者在本工单的**评论条数降序**排列，条数相同按
       **最近一次评论时间降序**（用户 2026-09-18 明确要求的口径）。
    2. **红点**：某参与者「发布了当前登录用户未读的评论」→ 该头像需要亮红点。
       未读判定以读游标 ``TaskCommentRead.last_read_comment_id`` 为准
       （与该工单详情页已读回执同口径）：存在 ``comment_id > 我的游标``
       且作者是该参与者的评论 ⇒ 未读。
    3. **展示名 / 头像**：复用 ``read_receipt._user_display_map``，与已读名单口径一致。

传输无关：全部为同步 Session 操作，调用方负责用
``starlette.concurrency.run_in_threadpool`` 移出事件循环（与 read_receipt 一致）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.task import TaskComment, TaskCommentRead, TaskParticipant
from app.modules.tasks.read_receipt import _user_display_map

logger = logging.getLogger(__name__)

# 单工单回填的参与人上限（防止超巨型工单把列表接口撑爆；超出部分截断）
MAX_PARTICIPANTS_PER_TASK = 20


def _chunked(seq: Sequence[Any], size: int = 200) -> Iterable[List[Any]]:
    """按块切分，控制单条 SQL 的 IN 参数规模。"""
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


def fetch_participants_map(
    task_ids: Sequence[int],
    current_username: Optional[str] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """自带独立同步会话的入口（供 async 调用方丢线程池使用）。

    与 ``read_receipt.report_read`` 同构：不混用请求级 AsyncSession，
    自己开 ``SessionLocal`` 并在 finally 关闭。
    """
    db = SessionLocal()
    try:
        return build_participants_map(db, task_ids, current_username=current_username)
    finally:
        db.close()


def build_participants_map(
    db: Session,
    task_ids: Sequence[int],
    current_username: Optional[str] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """批量构造多张工单的参与人列表（列表接口专用，一次 IN 查询，无 N+1）。

    参数：
        db: 同步 Session（调用方负责 run_in_threadpool）。
        task_ids: 本页工单 id 列表。
        current_username: 当前登录用户；用于计算每个参与人的 ``has_unread`` 红点。
            传 None 时 ``has_unread`` 恒为 False（无用户上下文，红点不显示）。

    返回：
        ``{task_id: [{username, name, avatar_resource_id, comment_count, has_unread}, ...]}``
        组内已按「评论数降序 → 最近评论时间降序」排序，且已截断到
        ``MAX_PARTICIPANTS_PER_TASK``。无参与人的工单不出现在返回字典中
        （调用方用 ``.get(task_id, [])`` 兜底）。
    """
    ids = [int(t) for t in task_ids if t]
    if not ids:
        return {}

    result: Dict[int, List[Dict[str, Any]]] = {}

    # ── 1. 批量取参与人（task_id IN ...，分块防 IN 过大）──
    participant_rows: List[TaskParticipant] = []
    for chunk in _chunked(ids):
        rows = db.execute(
            select(TaskParticipant).where(TaskParticipant.task_id.in_(chunk))
        ).scalars().all()
        participant_rows.extend(rows)

    if not participant_rows:
        return {}

    # 全部相关用户名（用于统一解析展示名/头像，一次查询）
    all_usernames = sorted({r.username for r in participant_rows})

    # ── 2. 批量统计「作者 → 该工单的评论条数 + 最近评论时间」──
    #     只统计参与人发的评论；一条 SQL 覆盖本页所有工单。
    count_rows: List[Any] = []
    for chunk in _chunked(ids):
        rows = db.execute(
            select(
                TaskComment.task_id,
                TaskComment.created_by,
                func.count(TaskComment.id).label("cnt"),
                func.max(TaskComment.created_at).label("last_at"),
            )
            .where(
                TaskComment.task_id.in_(chunk),
                TaskComment.created_by.in_(all_usernames),
            )
            .group_by(TaskComment.task_id, TaskComment.created_by)
        ).all()
        count_rows.extend(rows)

    # (task_id, username) → (评论数, 最近评论时间)
    stats: Dict[Any, Any] = {
        (int(r.task_id), r.created_by): (int(r.cnt or 0), r.last_at)
        for r in count_rows
    }

    # ── 3. 批量算「红点」：当前用户的读游标 → 各参与人是否有其未读评论 ──
    #     口径：作者 ≠ 我，且 comment_id > 我的游标 ⇒ 该作者有我没读的评论。
    #     游标缺失（我从没读过该工单）按 0 处理 → 所有非我本人的参与者都亮红点，
    #     与「进详情页前都是未读」的直觉一致。
    unread_authors: Dict[int, set] = {}
    if current_username:
        cursor_rows = db.execute(
            select(TaskCommentRead.task_id, TaskCommentRead.last_read_comment_id)
            .where(
                TaskCommentRead.username == current_username,
                TaskCommentRead.task_id.in_(ids),
            )
        ).all()
        cursor_map: Dict[int, int] = {
            int(r.task_id): int(r.last_read_comment_id or 0) for r in cursor_rows
        }

        # 用「该作者在本工单的最大 comment_id > 我的游标」判定未读，
        # 比逐行比较更省；作者 ≠ 我（自己发的评论不算自己未读）。
        for chunk in _chunked(ids):
            rows = db.execute(
                select(
                    TaskComment.task_id,
                    TaskComment.created_by,
                    func.max(TaskComment.id).label("max_id"),
                )
                .where(
                    TaskComment.task_id.in_(chunk),
                    TaskComment.created_by.in_(all_usernames),
                    TaskComment.created_by != current_username,
                )
                .group_by(TaskComment.task_id, TaskComment.created_by)
            ).all()
            for r in rows:
                tid = int(r.task_id)
                if int(r.max_id or 0) > cursor_map.get(tid, 0):
                    unread_authors.setdefault(tid, set()).add(r.created_by)

    # ── 4. 解析展示名 / 头像（一次查询，与已读名单一套口径）──
    name_map, avatar_map = _user_display_map(db, all_usernames)

    # ── 5. 组装并按用户要求排序：评论数降序 → 最近评论时间降序 ──
    for row in participant_rows:
        tid = int(row.task_id)
        cnt, last_at = stats.get((tid, row.username), (0, None))
        result.setdefault(tid, []).append({
            "username": row.username,
            "name": name_map.get(row.username, row.username),
            "avatar_resource_id": avatar_map.get(row.username),
            "comment_count": cnt,
            "last_comment_at": last_at.isoformat() if last_at else None,
            "has_unread": row.username in unread_authors.get(tid, set()),
        })

    for tid, items in result.items():
        items.sort(
            key=lambda x: (
                x["comment_count"],
                x["last_comment_at"] or "",
            ),
            reverse=True,
        )
        del items[MAX_PARTICIPANTS_PER_TASK:]

    return result
