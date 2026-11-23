"""通知排程投递 Worker。

随 FastAPI startup 启动：周期性扫描 `notification_outbox` 中已到期的记录并投递。

为什么不在业务线程里 sleep 到工作时间：
    进程重启即丢失，且长期占用线程。改为落库 + 轮询，重启后自动续投。

为什么投递前还要再校验一次工作时间：
    排程时刻基于当时的日历算出；若期间管理员新增了节假日，到期当天可能仍是假期，
    此时应**重新排期**而不是丢弃。

为什么 fetch_due 后还要内部分组去重：
    防 Worker reschedule 导致的「原本 scheduled_at 不同的多条记录被改成同一时刻」场景——
    历史事故：修复前逾期提醒的 planned 永远是 deadline 后第一个工作日，DAG 周末命中
    scheduled_at 落在非工作时间 → Worker reschedule 到周一 09:00 → 周六/周日/周一
    各一条 outbox 被改成同一时刻 → 到点一起投 → 用户收到 3 条重复。

    主修复（working_time_service.plan_send_time）已让 scheduled_at 第一次就算对，
    Worker 不再被迫 reschedule。此处是兜底：即使 reschedule 了，也不会重复投。
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
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


def _dedupe_records(records: list[dict]) -> set[str]:
    """一轮 fetch_due 内部按 (task, type, recipient, scheduled_at) 分组去重。

    幂等键 occurrence=dayN 每天不同，导致同一工单+同一接收人+同一逾期天可能排多条
    outbox；若它们 scheduled_at 相同（周末堆积 + Worker reschedule），一起投就是
    重复。本函数在 claim 之前把重复的挑出来，调用方直接 mark_cancel 跳过，不进
    投递流程。

    返回应被取消的 outbox_id 集合。
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        key = (
            r.get("task_id"),
            r.get("notify_type"),
            r.get("recipient_id"),
            str(r.get("scheduled_at")),  # 转 str 做 dict key（datetime 不可 hash）
        )
        groups[key].append(r)

    to_cancel: set[str] = set()
    for key, bucket in groups.items():
        if len(bucket) <= 1:
            continue
        # 每组按 id 字典序选最早创建的（uuid hex 字典序 ≈ 创建时间序）
        bucket.sort(key=lambda r: r["id"])
        keep = bucket[0]
        for dup in bucket[1:]:
            to_cancel.add(dup["id"])
            logger.warning(
                f"[Outbox-Dedup] 同 (task={key[0]}, type={key[1]}, "
                f"recipient={key[2]}, scheduled={key[3]}) 发现重复，"
                f"取消 {dup['id']}，保留 {keep['id']}"
            )
    return to_cancel


async def _dispatch_due() -> int:
    """投递本轮到期的记录，返回成功条数。"""
    records = NotificationOutboxService.fetch_due(BATCH_LIMIT)
    if not records:
        return 0

    # Worker 侧防御：同刻重复合并。主修复在 plan_send_time，这里是兜底。
    dedup_ids = _dedupe_records(records)

    now = WorkingTimeService.now_utc()
    sent = 0

    for record in records:
        outbox_id = record["id"]

        # 跳过本轮去重判定为重复的记录
        if outbox_id in dedup_ids:
            NotificationOutboxService.mark_cancelled(
                outbox_id, "Worker 去重：同 task+type+recipient+scheduled_at 重复，已取消"
            )
            continue

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

        # 到期时仍处非工作时间（如 Worker 停机到夜里才恢复）→ 顺延到下一工作时段。
        # 逾期类型的 planned 恒 <= now，仅靠下面的 planned > now 判断拦不住。
        if not WorkingTimeService.is_working_time(user, now):
            NotificationOutboxService.reschedule(
                outbox_id,
                WorkingTimeService.next_working_start(user, now),
                "到期时接收人非工作时间，已重新排期",
            )
            continue

        if planned > now:
            NotificationOutboxService.reschedule(
                outbox_id, planned, "到期时接收人非工作时间，已重新排期"
            )
            continue

        try:
            result = await NotificationOutboxService.dispatch(record)
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "retryable": True, "error": str(e)}
            logger.error(f"[Outbox] 投递异常 id={outbox_id}: {e}")

        if result.get("ok"):
            NotificationOutboxService.mark_sent(outbox_id)
            sent += 1
        else:
            attempts = (record.get("attempt_count") or 0) + 1
            error = result.get("error") or "投递失败"
            # 配置/绑定类失败（未绑定微信、模板错误）重试无意义，直接置 failed
            if not result.get("retryable") or attempts >= MAX_ATTEMPTS:
                NotificationOutboxService.mark_failed(outbox_id, error)
            else:
                retry_at = now + timedelta(minutes=RETRY_BACKOFF_MINUTES * attempts)
                # 退避点若已落到非工作时间（如下班前失败、退避到夜里）→ 顺延到下一工作时段
                if not WorkingTimeService.is_working_time(user, retry_at):
                    retry_at = WorkingTimeService.next_working_start(user, retry_at)
                NotificationOutboxService.reschedule(
                    outbox_id,
                    retry_at,
                    f"第 {attempts} 次未送达：{error}",
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
