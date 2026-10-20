"""微信支付后台定时任务：兜底查单 / 奖励过期 / 一致性巡检 / PENDING 奖励发起转账。

微信**不保证回调送达**，兜底查单是必选项而非可选项。
调度器随 FastAPI startup/shutdown 启停（与 wechat 快照调度器同风格）。

奖励发起转账的设计依据（规范 §5.2）：
  审核事务（归档审核 / 财务审核 / 审核决策引擎）**只记账 + 改 status**，
  不直接调用微信转账 API。真正的转账在事务外由本定时任务统一发起，
  避免：幽灵转账（commit 前 crash 导致 DB 无记录但微信已到账）、
  审核事务被外部 HTTP 调用拖慢。
"""

import asyncio
from datetime import datetime

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.modules.wxpay.models import RewardRecord, RewardStatus, TransferBill, TransferBillState
from app.modules.wxpay.service import (
    check_consistency,
    expire_overdue_rewards,
    reconcile_open_bills,
    WithdrawService,
)
from app.services.logging import get_logger

logger = get_logger(__name__)

_scheduler_task: asyncio.Task | None = None


async def _flush_pending_rewards_to_withdraw() -> dict:
    """扫 status=PENDING 且**没有有效非终态 TransferBill** 的奖励，发起 withdraw。

    返回 {"ok": int, "fail": int, "skipped_no_openid": int, "skipped_bill_open": int}。
    """
    result = {"ok": 0, "fail": 0, "skipped_no_openid": 0, "skipped_bill_open": 0}

    if not getattr(settings, "WXPAY_ENABLED", False):
        return result

    # 单次上限：避免一次任务拖太久
    limit = 100
    pending_rewards = []
    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(RewardRecord)
                    .where(RewardRecord.status == RewardStatus.PENDING)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

        for r in rows:
            # 已有有效转账单 → 跳过（由 reconcile_open_bills 兜底）
            has_open_bill = (
                await db.execute(
                    select(TransferBill).where(
                        TransferBill.reward_id == r.id,
                        TransferBill.is_terminal.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if has_open_bill:
                result["skipped_bill_open"] += 1
                continue
            # 无 openid → 跳过（用户未绑定微信）
            if not r.openid:
                result["skipped_no_openid"] += 1
                continue
            pending_rewards.append(r)

    # 独立事务逐条发起 withdraw（每条自己 commit，互不影响）
    for r in pending_rewards:
        try:
            async with AsyncSessionLocal() as db:
                out = await WithdrawService.withdraw(db, r.id, r.user_id)
                if getattr(out, "action", "") == "success":
                    result["ok"] += 1
                else:
                    logger.warning(
                        f"[wxpay] 定时任务奖励发起转账未走到成功: reward_id={r.id} action={out.action} msg={out.message}"
                    )
                    result["fail"] += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                f"[wxpay] 定时任务奖励发起转账异常: reward_id={r.id}"
            )
            result["fail"] += 1

    return result


async def _run_once() -> None:
    async with AsyncSessionLocal() as db:
        try:
            expired = await expire_overdue_rewards(db)
            flushed = await _flush_pending_rewards_to_withdraw()
            refreshed = await reconcile_open_bills(db)
            stats = await check_consistency(db)
            logger.info(
                f"[wxpay] 定时任务完成 expired={expired} "
                f"flush_ok={flushed['ok']} flush_fail={flushed['fail']} "
                f"flush_skip_bill={flushed['skipped_bill_open']} flush_skip_openid={flushed['skipped_no_openid']} "
                f"reconciled={refreshed} withdrawing={stats.get('withdrawing')} "
                f"fixed={stats.get('fixed')} stuck={stats.get('stuck')}"
            )
        except Exception:  # noqa: BLE001
            logger.exception("[wxpay] 定时任务执行异常，等待下个周期重试")


async def run_wxpay_tasks_once() -> None:
    """手动触发一次（便于测试与运维介入）。"""
    await _run_once()


async def _scheduler_loop() -> None:
    interval = max(1, int(getattr(settings, "WXPAY_RECONCILE_INTERVAL_MINUTES", 10) or 10))
    if not getattr(settings, "WXPAY_ENABLED", False):
        logger.info("[wxpay] 未启用（WXPAY_ENABLED=False），不启动兜底任务调度器")
        return

    logger.info(f"[wxpay] 兜底任务调度器已启动，间隔 {interval} 分钟")
    while True:
        try:
            await _run_once()
        except Exception:  # noqa: BLE001
            logger.exception("[wxpay] 调度循环异常")
        await asyncio.sleep(interval * 60)


def start_wxpay_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())


def stop_wxpay_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None


def scheduler_status() -> dict:
    return {
        "enabled": bool(getattr(settings, "WXPAY_ENABLED", False)),
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "interval_minutes": int(
            getattr(settings, "WXPAY_RECONCILE_INTERVAL_MINUTES", 10) or 10
        ),
        "checked_at": datetime.utcnow().isoformat(),
    }
