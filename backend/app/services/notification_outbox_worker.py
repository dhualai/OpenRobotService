"""通知排程投递 Worker。

随 FastAPI startup 启动：周期性扫描 `notification_outbox` 中已到期的记录并投递。

为什么不在业务线程里 sleep 到工作时间：
    进程重启即丢失，且长期占用线程。改为落库 + 轮询，重启后自动续投。

为什么投递前还要再校验一次工作时间：
    排程时刻基于当时的日历算出；若期间管理员新增了节假日，到期当天可能仍是假期，
    此时应**重新排期**而不是丢弃。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from app.services.notification_outbox_service import (
    MAX_ATTEMPTS,
    RETRY_BACKOFF_MINUTES,
    NotificationOutboxService,
)
from app.services.working_time_service import WorkingTimeService

logger = logging.getLogger(__name__)

# 轮询间隔（秒）：分钟级足够，通知不要求秒级准时
POLL_SECONDS = 60
# 单轮最多处理条数，避免一次性拉爆线程池
BATCH_LIMIT = 100

_scheduler_task: Optional[asyncio.Task] = None


async def _dispatch_due() -> int:
    """投递本轮到期的记录，返回成功条数。"""
    records = NotificationOutboxService.fetch_due(BATCH_LIMIT)
    if not records:
        return 0

    now = WorkingTimeService.now_utc()
    sent = 0

    for record in records:
        outbox_id = record["id"]
        if not NotificationOutboxService.claim(outbox_id):
            continue  # 已被其他流程占位

        # 兜底校验：到期时仍非工作时间 → 重新排期，不丢弃
        # 临期预警返回 None = 已错过「截止前最后一个工作时段末尾」，取消本条，
        # 由截止后的逾期提醒接管，避免在非工作时间补发
        deadline = _parse_deadline(record.get("payload"))
        user = WorkingTimeService.get_user_context(record.get("recipient_id"))
        planned = WorkingTimeService.plan_send_time(
            user, record.get("notify_type"), deadline, now=now
        )
        if planned is None:
            NotificationOutboxService.mark_cancelled(
                outbox_id, "临期预警已过最后工作时段末尾，不予补发"
            )
            continue
        if planned > now:
            NotificationOutboxService.reschedule(
                outbox_id, planned, "到期时接收人非工作时间，已重新排期"
            )
            continue

        try:
            ok = await NotificationOutboxService.dispatch(record)
        except Exception as e:  # noqa: BLE001
            ok = False
            logger.error(f"[Outbox] 投递异常 id={outbox_id}: {e}")

        if ok:
            NotificationOutboxService.mark_sent(outbox_id)
            sent += 1
        else:
            attempts = (record.get("attempt_count") or 0) + 1
            if attempts >= MAX_ATTEMPTS:
                NotificationOutboxService.mark_failed(outbox_id, f"重试 {attempts} 次仍失败")
            else:
                NotificationOutboxService.reschedule(
                    outbox_id,
                    now + timedelta(minutes=RETRY_BACKOFF_MINUTES * attempts),
                    f"第 {attempts} 次投递失败，等待重试",
                )
    return sent


def _parse_deadline(payload) -> Optional[datetime]:
    """从 payload 取回截止时间（ISO 字符串，UTC）。"""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("deadline_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


async def _worker_loop() -> None:
    while True:
        await asyncio.sleep(POLL_SECONDS)
        try:
            sent = await _dispatch_due()
            if sent:
                logger.info(f"[Outbox] 本轮投递 {sent} 条")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("[Outbox] 投递循环异常，等待下轮重试")


def start_notification_outbox_worker() -> None:
    """启动投递 Worker（随 FastAPI startup 调用）。"""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_worker_loop())
        logger.info("[Outbox] 通知排程投递 Worker 已启动")


def stop_notification_outbox_worker() -> None:
    """停止投递 Worker（随 FastAPI shutdown 调用）。"""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
