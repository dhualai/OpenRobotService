"""user_info 表整点快照服务。

每个整点拉取与 /api/wechat/batch-user-info 接口同源的数据（users 表全部真实
openid → 微信 batchget），将接口返回值 {"success": True, "user_info_list":
[...], "total": N} 整体存入 user_info 表。

保留策略：同一天多次快照仅保留最新一条（新快照落库时删除当天更早的记录），
历史日期各自的最新快照保留，可回看每日用户构成变化。

调度实现：随 FastAPI startup 启动的 asyncio 后台任务，睡眠到下一个整点执行；
进程重启后从下一个整点继续，错过的整点不补偿。
"""
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from app.core.database import db_manager, UserDB
from app.models.user_info import UserInfo
from app.wechat.services.wechat_service import wechat_service

logger = logging.getLogger(__name__)

_scheduler_task: Optional[asyncio.Task] = None


def _build_full_user_list() -> List[Dict]:
    """从 users 表获取全部真实用户 openid（与 batch-user-info 接口口径一致：
    过滤掉 id 以 'user_' 开头的虚拟用户，如 user_admin）。

    返回形如 [{"openid": "...", "lang": "zh_CN"}, ...]。
    """
    db = db_manager.get_db()
    try:
        rows = db.query(UserDB.id).filter(~UserDB.id.like('user_%')).all()
    finally:
        db.close()
    return [{'openid': row[0], 'lang': 'zh_CN'} for row in rows if row[0]]


def _store_snapshot(payload: Dict, snapshot_date: Optional[date] = None) -> int:
    """写入快照并清理当天旧快照，返回新行 id。

    同一天仅保留最新一条：插入新行后删除当天更早的记录；历史日期不动。
    """
    target_date = snapshot_date or date.today()
    db = db_manager.get_db()
    try:
        row = UserInfo(user_info=payload, created_time=target_date)
        db.add(row)
        db.flush()  # 先拿到新行 id，再按 id 排除做删除
        db.query(UserInfo).filter(
            UserInfo.created_time == target_date,
            UserInfo.id != row.id,
        ).delete(synchronize_session=False)
        db.commit()
        return row.id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def run_user_info_snapshot(snapshot_date: Optional[date] = None) -> Optional[Dict]:
    """执行一次快照：拉取 batch-user-info 同源数据并写入 user_info 表。

    返回落库的接口返回值 dict；users 表无真实用户或微信拉取失败时返回 None
    （失败不落库，下个整点重试）。
    """
    user_list = await asyncio.to_thread(_build_full_user_list)
    if not user_list:
        logger.warning('users 表无真实微信用户，跳过 user_info 快照')
        return None

    logger.info(f'开始拉取用户信息快照，共 {len(user_list)} 个 openid')
    result = await wechat_service.batch_get_user_info(user_list)
    if result is None or 'user_info_list' not in result:
        logger.error(f'拉取用户信息失败，本次快照不落库: {result}')
        return None

    payload = {
        'success': True,
        'user_info_list': result.get('user_info_list', []),
        'total': len(result.get('user_info_list', [])),
    }

    target_date = snapshot_date or date.today()
    row_id = await asyncio.to_thread(_store_snapshot, payload, target_date)
    logger.info(
        f'user_info 快照已存储 id={row_id}, total={payload["total"]}, '
        f'created_time={target_date}（当天旧快照已清理，历史日期保留）'
    )
    return payload


def _seconds_until_next_hour() -> float:
    """距下一个整点的秒数（本地时间）。"""
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


async def _snapshot_scheduler_loop() -> None:
    logger.info('user_info 整点快照调度器已启动（每个整点拉取 batch-user-info 并落库）')
    while True:
        delay = _seconds_until_next_hour()
        next_run = datetime.now() + timedelta(seconds=delay)
        logger.info(f'下次 user_info 快照执行时间: {next_run.strftime("%Y-%m-%d %H:%M:%S")}')
        await asyncio.sleep(delay)
        try:
            await run_user_info_snapshot()
        except Exception:
            logger.exception('user_info 整点快照执行异常，等待下个整点重试')


def start_user_info_snapshot_scheduler() -> None:
    """启动整点快照后台任务（随 FastAPI startup 调用）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_snapshot_scheduler_loop())


def stop_user_info_snapshot_scheduler() -> None:
    """停止整点快照后台任务（随 FastAPI shutdown 调用）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
