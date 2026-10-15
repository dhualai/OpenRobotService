"""微信支付后台定时任务：兜底查单 / 奖励过期 / 一致性巡检。

微信**不保证回调送达**，兜底查单是必选项而非可选项。
调度器随 FastAPI startup/shutdown 启停（与 wechat 快照调度器同风格）。
"""

import asyncio
from datetime import datetime

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.modules.wxpay.service import (
    check_consistency,
    expire_overdue_rewards,
    reconcile_open_bills,
)
from app.services.logging import get_logger

logger = get_logger(__name__)

_scheduler_task: asyncio.Task | None = None


async def _run_once() -> None:
    async with AsyncSessionLocal() as db:
        try:
            expired = await expire_overdue_rewards(db)
            refreshed = await reconcile_open_bills(db)
            stats = await check_consistency(db)
            logger.info(
                f"[wxpay] 定时任务完成 expired={expired} reconciled={refreshed} "
                f"withdrawing={stats.get('withdrawing')} fixed={stats.get('fixed')} "
                f"stuck={stats.get('stuck')}"
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
