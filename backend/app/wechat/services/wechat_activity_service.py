"""微信用户日活埋点服务。

在微信消息回调（发消息/点菜单/关注扫码事件）与 H5 登录处调用
record_wechat_activity()，幂等 upsert 到 wechat_user_activity 表
（唯一键 ref_date + openid），同一用户同一天只保留一行并刷新
last_active_at 与 source（当日最后活跃来源）。

埋点失败只记日志不抛异常，绝不阻断微信消息主流程。
"""
import asyncio
import logging
from datetime import date
from typing import Optional

from sqlalchemy import text

from app.core.database import db_manager

logger = logging.getLogger(__name__)

# 活跃来源编码（与 wechat_user_activity.source 列注释一致）
SOURCE_MESSAGE = "message"  # 公众号发文本消息
SOURCE_MENU = "menu"        # 点击菜单（CLICK / VIEW）
SOURCE_EVENT = "event"      # 关注 / 扫码等事件
SOURCE_H5 = "h5"            # 打开 H5 页面（微信登录）

# 幂等 upsert：已存在（同日期同 openid）则只刷新最后活跃时间与来源
_UPSERT_SQL = text(
    "INSERT INTO wechat_user_activity (ref_date, openid, source, last_active_at) "
    "VALUES (:ref_date, :openid, :source, NOW()) "
    "ON DUPLICATE KEY UPDATE last_active_at = NOW(), source = :source"
)


def _record_activity_sync(openid: str, source: str) -> bool:
    """同步写库（调用方应放入线程池执行，避免阻塞事件循环）。"""
    db = db_manager.get_db()
    try:
        db.execute(
            _UPSERT_SQL,
            {"ref_date": date.today(), "openid": openid, "source": source},
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.warning(
            "微信用户日活埋点失败: openid=%s, source=%s", openid, source, exc_info=True
        )
        return False
    finally:
        db.close()


async def record_wechat_activity(openid: Optional[str], source: str) -> bool:
    """记录微信用户当日活跃（幂等 upsert）。

    仅限 async 上下文调用；失败静默降级（记日志），不阻断微信消息主流程。
    """
    if not openid:
        return False
    try:
        return await asyncio.to_thread(_record_activity_sync, openid, source)
    except Exception:
        logger.warning(
            "微信用户日活埋点调度失败: openid=%s, source=%s", openid, source, exc_info=True
        )
        return False
