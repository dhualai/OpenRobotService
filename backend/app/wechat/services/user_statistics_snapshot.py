"""user_statistics 表整点统计任务。

每个整点调用与 /api/wechat/user-summary 接口同源的服务（微信
getusersummary，T+1 数据已就绪），拉取昨日用户增减数据，将返回值 list 中
每一行（ref_date/user_source/new_user/cancel_user 渠道明细）原样写入
user_statistics 表，不做聚合——前端柱状图按日期求和、饼图按渠道分组均可
从表中还原微信接口的完整返回。

幂等：当微信返回非空 list 时，先删除昨日 ref_date 的已有记录，再写入本次最新
渠道明细；若微信返回空 list，则保留该日期旧记录不动。调度：随 FastAPI startup
启动的 asyncio 后台任务，睡眠到下一个整点执行；进程重启后从下一个整点继续，
错过的整点不补偿。失败自动重试。
"""
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from app.core.database import db_manager
from app.models.user_statistics import UserStatistics
from app.wechat.services.wechat_service import wechat_service

logger = logging.getLogger(__name__)

# 每小时整点执行一次（分钟/秒固定为 0）
_RUN_MINUTE = 0
# 失败重试次数（含首次共 N 次）
_MAX_ATTEMPTS = 5
# 重试间隔（秒）
_RETRY_INTERVAL_SECONDS = 10 * 60

_scheduler_task: Optional[asyncio.Task] = None


def _replace_rows_for_date(target_date: date, items: List[Dict]) -> int:
    """用本次结果覆盖指定日期的渠道明细，返回最终写入行数。

    会先删除 target_date 的旧记录，再写入 items 中 ref_date 与 target_date
    相同的行。调用方应确保 items 非空；空结果不应覆盖旧记录。
    """
    rows: List[Dict] = []
    for item in items:
        try:
            ref_date = datetime.strptime(str(item.get('ref_date')), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            logger.warning(f"跳过无法解析 ref_date 的行: {item}")
            continue
        if ref_date != target_date:
            logger.warning(
                "跳过与目标日期不一致的 user_statistics 行: target_date=%s, item=%s",
                target_date.strftime('%Y-%m-%d'),
                item,
            )
            continue
        rows.append(item)

    db = db_manager.get_db()
    try:
        db.query(UserStatistics).filter(
            UserStatistics.ref_date == target_date,
        ).delete(synchronize_session=False)
        for item in rows:
            db.add(UserStatistics(
                ref_date=target_date,
                user_source=int(item.get('user_source') or 0),
                new_user=int(item.get('new_user') or 0),
                cancel_user=int(item.get('cancel_user') or 0),
            ))
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_user_statistics_job() -> Optional[List[Dict]]:
    """拉取昨日用户增减数据，用本次结果覆盖写入 user_statistics 表。

    返回写入的明细行列表；微信拉取失败返回 None（由调用方决定重试）。
    """
    yesterday = date.today() - timedelta(days=1)
    date_str = yesterday.strftime('%Y-%m-%d')

    logger.info(f'开始拉取用户增减统计（{date_str}）')
    result = await wechat_service.get_user_summary(date_str, date_str)
    if result is None or 'list' not in result:
        logger.error(f'拉取用户增减数据失败，本次不落库: {result}')
        return None

    items = result.get('list') or []
    if not items:
        logger.info(f'微信返回空 list（{date_str} 无新渠道明细），保留历史记录不变')
        return []

    stored = await asyncio.to_thread(_replace_rows_for_date, yesterday, items)
    logger.info(f'user_statistics 已刷新为最新渠道明细 {stored} 行（ref_date={date_str}）')
    return items


async def _run_with_retry() -> None:
    """执行一次整点任务，失败按间隔重试，重试用尽则放弃等待下个整点。"""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = await run_user_statistics_job()
            if result is not None:
                return
        except Exception:
            logger.exception(f'用户增减统计任务第 {attempt} 次执行异常')
        if attempt < _MAX_ATTEMPTS:
            logger.warning(
                f'用户增减统计任务失败，{_RETRY_INTERVAL_SECONDS // 60} 分钟后重试'
                f'（{attempt}/{_MAX_ATTEMPTS}）'
            )
            await asyncio.sleep(_RETRY_INTERVAL_SECONDS)
    logger.error(f'用户增减统计任务连续 {_MAX_ATTEMPTS} 次失败，放弃，等待下一个整点')


def _seconds_until_next_run() -> float:
    """距下一个整点的秒数（本地时间）。"""
    now = datetime.now()
    next_run = now.replace(minute=_RUN_MINUTE, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(hours=1)
    return (next_run - now).total_seconds()


async def _user_statistics_scheduler_loop() -> None:
    logger.info(
        'user_statistics 整点统计任务调度器已启动'
        '（每个整点拉取昨日用户增减数据；非空结果覆盖为该日期最新渠道明细）'
    )
    while True:
        delay = _seconds_until_next_run()
        next_run = datetime.now() + timedelta(seconds=delay)
        logger.info(f'下次 user_statistics 执行时间: {next_run.strftime("%Y-%m-%d %H:%M:%S")}')
        await asyncio.sleep(delay)
        try:
            await _run_with_retry()
        except Exception:
            logger.exception('user_statistics 整点任务执行异常，等待下个整点重试')


def start_user_statistics_scheduler() -> None:
    """启动整点统计后台任务（随 FastAPI startup 调用）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_user_statistics_scheduler_loop())


def stop_user_statistics_scheduler() -> None:
    """停止每日统计后台任务（随 FastAPI shutdown 调用）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
