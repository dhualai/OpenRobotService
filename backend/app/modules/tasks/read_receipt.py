"""工单讨论区「已读回执」核心服务（与传输通道解耦）。

修复背景（已读名单更新不及时根因治理）：
- P1 批量幂等：一次上报用「批量 INSERT ... ON DUPLICATE KEY UPDATE」完成。
  旧实现逐条 SELECT → UPDATE → refresh，N 条评论 = 3N 次 DB 往返；
- P1 失败隔离：按块写入 + SAVEPOINT，单块失败只回滚该块。旧实现整批一个事务，
  任意一条撞唯一键（多标签页并发）就整批 rollback，导致整次上报全丢；
- P1 归属校验：上报的 comment_id 必须属于当前工单，杜绝跨工单伪造已读污染名单；
- P2 快照限流：名单快照支持条数上限，避免大工单 welcome 帧过大。

传输无关：WS 处理器与 REST 兜底接口都调用本模块。全部为同步 Session 操作，
调用方负责用 ``starlette.concurrency.run_in_threadpool`` 移出事件循环。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models.identity import UserDB
from app.models.task import TaskComment, TaskCommentRead, TaskCommentReadRecord

logger = logging.getLogger(__name__)

# 单帧/单请求允许上报的最大评论数（防刷，超出截断）
MAX_COMMENT_IDS_PER_REQUEST = 500
# welcome 名单快照条数上限（大工单保护，超出部分由 REST 按需拉取兜底）
READ_RECORDS_SNAPSHOT_LIMIT = 2000
# 单条评论名单拉取上限（弹层场景，正常远小于此）
READ_RECORDS_PER_COMMENT_LIMIT = 500
# 单批写入条数上限（控制单条 SQL 体积与锁范围）
_BULK_CHUNK_SIZE = 100


# ─────────────────────────────────────────────────────────────────────────────
# 入参清洗
# ─────────────────────────────────────────────────────────────────────────────

def normalize_comment_ids(raw: Any, limit: int = MAX_COMMENT_IDS_PER_REQUEST) -> List[int]:
    """清洗客户端上报的 comment_ids：只保留正整数，去重、保序、截断到上限。

    客户端可能混入字符串 / 负数 / null / 布尔值，一律丢弃（不抛异常，避免
    单个脏数据打断整次上报）。
    """
    if not isinstance(raw, (list, tuple, set)):
        return []
    result: List[int] = []
    seen = set()
    for item in raw:
        # bool 是 int 的子类，必须显式排除
        if isinstance(item, bool) or not isinstance(item, int):
            continue
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _chunked(seq: Sequence[int], size: int) -> Iterable[List[int]]:
    for i in range(0, len(seq), size):
        yield list(seq[i:i + size])


def _dialect_name(db: Session) -> str:
    try:
        bind = db.get_bind()
        return bind.dialect.name if bind is not None else ""
    except Exception:  # noqa: BLE001 - 取方言失败按非 MySQL 处理
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# 写入：批量幂等 upsert
# ─────────────────────────────────────────────────────────────────────────────

def filter_comment_ids_owned_by_task(db: Session, task_id: int, comment_ids: Sequence[int]) -> List[int]:
    """只保留确实属于该工单的评论 id（归属校验，防跨工单伪造已读）。"""
    ids = normalize_comment_ids(comment_ids)
    if not ids:
        return []
    rows = db.execute(
        select(TaskComment.id).where(
            TaskComment.task_id == task_id,
            TaskComment.id.in_(ids),
        )
    ).scalars().all()
    owned = {int(r) for r in rows}
    # 保持客户端传入顺序，便于日志/广播稳定
    return [cid for cid in ids if cid in owned]


def _bulk_upsert_chunk(db: Session, task_id: int, comment_ids: Sequence[int], username: str) -> None:
    """单批幂等写入：已存在则刷新 read_at（视为重新阅读），不存在则插入。

    MySQL 走 ``INSERT ... ON DUPLICATE KEY UPDATE read_at=NOW()``（1 次往返）；
    其余方言（SQLite 测试等）降级为逐条 upsert。
    新插入行的 read_at 由列的 ``server_default=func.now()`` 在数据库端生成。
    """
    rows = [
        {"task_id": task_id, "comment_id": cid, "username": username}
        for cid in comment_ids
    ]
    if not rows:
        return

    if _dialect_name(db) == "mysql":
        stmt = mysql_insert(TaskCommentReadRecord).values(rows)
        stmt = stmt.on_duplicate_key_update(read_at=func.now())
        db.execute(stmt)
        return

    # 降级路径：逐条 upsert（仅非 MySQL 方言会走到这里）
    for row in rows:
        existing = db.execute(
            select(TaskCommentReadRecord).where(
                TaskCommentReadRecord.comment_id == row["comment_id"],
                TaskCommentReadRecord.username == username,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.read_at = func.now()
        else:
            db.add(
                TaskCommentReadRecord(
                    task_id=task_id,
                    comment_id=row["comment_id"],
                    username=username,
                )
            )
    db.flush()


def _fetch_read_times(db: Session, task_id: int, comment_ids: Sequence[int], username: str) -> Dict[int, Optional[str]]:
    """一次性回查本批记录的 read_at（1 次往返，替代旧实现的逐条 refresh）。"""
    if not comment_ids:
        return {}
    rows = db.execute(
        select(TaskCommentReadRecord.comment_id, TaskCommentReadRecord.read_at).where(
            TaskCommentReadRecord.task_id == task_id,
            TaskCommentReadRecord.comment_id.in_(list(comment_ids)),
            TaskCommentReadRecord.username == username,
        )
    ).all()
    return {int(cid): (read_at.isoformat() if read_at else None) for cid, read_at in rows}


def bulk_mark_read(db: Session, task_id: int, comment_ids: Any, username: str) -> List[Dict[str, Any]]:
    """批量写入已读明细，返回本次写入的 ``[{comment_id, read_at}]`` 供广播。

    失败隔离：按 ``_BULK_CHUNK_SIZE`` 分块，每块一个 SAVEPOINT，
    某块失败只回滚该块，其余块照常落库（旧实现整批回滚导致整次上报全丢）。
    注意：本函数不 commit，由调用方统一提交。
    """
    ids = normalize_comment_ids(comment_ids)
    if not ids:
        return []

    written: List[int] = []
    for chunk in _chunked(ids, _BULK_CHUNK_SIZE):
        try:
            with db.begin_nested():
                _bulk_upsert_chunk(db, task_id, chunk, username)
            written.extend(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"已读明细批量写入失败（已跳过该批次，不影响其余块）"
                f"task_id={task_id} username={username} count={len(chunk)}: {exc}"
            )

    if not written:
        return []

    times = _fetch_read_times(db, task_id, written, username)
    return [{"comment_id": cid, "read_at": times.get(cid)} for cid in written]


def upsert_read_cursor(db: Session, task_id: int, username: str, last_read_comment_id: Any) -> bool:
    """更新「读到哪一条」游标（与明细表互补）。返回是否执行了写入。"""
    if isinstance(last_read_comment_id, bool) or not isinstance(last_read_comment_id, int):
        return False
    if last_read_comment_id <= 0:
        return False

    rec = db.execute(
        select(TaskCommentRead).where(
            TaskCommentRead.task_id == task_id,
            TaskCommentRead.username == username,
        )
    ).scalar_one_or_none()
    if rec:
        rec.last_read_comment_id = last_read_comment_id
        rec.updated_at = func.now()
    else:
        db.add(
            TaskCommentRead(
                task_id=task_id,
                username=username,
                last_read_comment_id=last_read_comment_id,
            )
        )
    db.flush()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 读取
# ─────────────────────────────────────────────────────────────────────────────

def _user_display_map(db: Session, usernames: Sequence[str]) -> tuple[Dict[str, str], Dict[str, Any]]:
    name_map: Dict[str, str] = {}
    avatar_map: Dict[str, Any] = {}
    if not usernames:
        return name_map, avatar_map
    users = db.execute(select(UserDB).where(UserDB.username.in_(list(usernames)))).scalars().all()
    for u in users:
        name_map[u.username] = u.name or u.username
        avatar_map[u.username] = getattr(u, "avatar_resource_id", None)
    return name_map, avatar_map


def read_records_map(
    db: Session,
    task_id: int,
    comment_ids: Optional[Sequence[int]] = None,
    limit: int = READ_RECORDS_SNAPSHOT_LIMIT,
) -> Dict[str, List[Dict[str, Any]]]:
    """按 comment_id 分组返回已读名单（含用户名/姓名/头像/阅读时间）。

    返回 ``{str(comment_id): [{username, name, avatar_resource_id, read_at}, ...]}``，
    组内按阅读时间倒序。``comment_ids`` 用于按需拉取单条/若干条评论的名单。
    """
    stmt = select(TaskCommentReadRecord).where(TaskCommentReadRecord.task_id == task_id)
    if comment_ids:
        stmt = stmt.where(TaskCommentReadRecord.comment_id.in_([int(c) for c in comment_ids]))
    stmt = stmt.order_by(
        TaskCommentReadRecord.read_at.desc(), TaskCommentReadRecord.id.desc()
    ).limit(limit)

    rows = db.execute(stmt).scalars().all()
    name_map, avatar_map = _user_display_map(db, sorted({r.username for r in rows}))

    result: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        result.setdefault(str(r.comment_id), []).append({
            "username": r.username,
            "name": name_map.get(r.username, r.username),
            "avatar_resource_id": avatar_map.get(r.username),
            "read_at": r.read_at.isoformat() if r.read_at else None,
        })
    return result


def read_cursor_map(db: Session, task_id: int) -> Dict[str, int]:
    rows = db.execute(select(TaskCommentRead).where(TaskCommentRead.task_id == task_id)).scalars().all()
    return {r.username: r.last_read_comment_id for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# 高层入口（WS 与 REST 共用）
# ─────────────────────────────────────────────────────────────────────────────

def build_read_receipt(
    written: Sequence[Dict[str, Any]],
    username: str,
    name: Optional[str],
    avatar_resource_id: Optional[int],
) -> List[Dict[str, Any]]:
    """把写入结果组装成 read_receipt 广播所需的 records。"""
    return [
        {
            "comment_id": int(item["comment_id"]),
            "username": username,
            "name": name or username,
            "avatar_resource_id": avatar_resource_id,
            "read_at": item.get("read_at"),
        }
        for item in written
    ]


def report_read_sync(
    db: Session,
    task_id: int,
    comment_ids: Any,
    username: str,
    name: Optional[str] = None,
    avatar_resource_id: Optional[int] = None,
    last_read_comment_id: Any = None,
) -> Dict[str, Any]:
    """一次完整的已读上报：归属校验 → 批量幂等写入 → 更新游标 → 组装广播 records。

    明细与游标分两次提交：游标失败（如并发冲突）不影响明细已落库。
    返回 ``{records, comment_ids, last_read_comment_id}``，records 可直接广播。
    """
    owned = filter_comment_ids_owned_by_task(db, task_id, comment_ids)
    written = bulk_mark_read(db, task_id, owned, username)
    db.commit()

    cursor: Optional[int] = None
    try:
        if upsert_read_cursor(db, task_id, username, last_read_comment_id):
            db.commit()
            cursor = last_read_comment_id
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning(f"已读游标更新失败（明细已落库）task_id={task_id} username={username}: {exc}")

    return {
        "records": build_read_receipt(written, username, name, avatar_resource_id),
        "comment_ids": owned,
        "last_read_comment_id": cursor,
    }


def report_read(
    task_id: int,
    username: str,
    comment_ids: Any,
    name: Optional[str] = None,
    avatar_resource_id: Optional[int] = None,
    last_read_comment_id: Any = None,
) -> Dict[str, Any]:
    """REST 兜底入口：自带独立同步会话（不混用请求级 AsyncSession）。"""
    db = SessionLocal()
    try:
        return report_read_sync(
            db,
            task_id=task_id,
            comment_ids=comment_ids,
            username=username,
            name=name,
            avatar_resource_id=avatar_resource_id,
            last_read_comment_id=last_read_comment_id,
        )
    finally:
        db.close()


def fetch_comment_read_list(task_id: int, comment_id: int) -> List[Dict[str, Any]]:
    """按需拉取单条评论的已读名单（弹层打开时刷新，兜底 welcome 快照截断）。"""
    db = SessionLocal()
    try:
        return read_records_map(
            db,
            task_id,
            comment_ids=[int(comment_id)],
            limit=READ_RECORDS_PER_COMMENT_LIMIT,
        ).get(str(int(comment_id)), [])
    finally:
        db.close()
