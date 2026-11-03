"""新人红包（ORS-1086）：新关注公众号的用户进「我要摇人」可领 0.5 元。

口径（2026-11-03 拍板）：

- **新用户** = 首次关注公众号的时间 >= 活动开始时间（`start_at`，UTC 存储）。
  取不到关注时间就**不发**（宁缺勿错，避免误发）。
- **每人限领一次**：幂等靠 `reward_record` 的唯一约束
  `(source_type, source_id, role)`，重复点领取只会拿回同一条记录，不会重复发钱。
- **到账方式**：这里只记账（`status=pending`），用户到「我的奖励」页自己点提现，
  由那边调起微信确认收款页（见 `frontend/src/pages/rewards/MyRewards.tsx`）。
  所以本模块**不碰**微信转账 API。

活动开关 / 金额 / 起算时间走 `RewardConfig['newcomer_bonus']`（JSON），**零 DDL**；
配置行不存在时用内置默认值，配置行 `enabled=false` 则整个活动关闭。
"""

import json
import logging
from datetime import date, datetime, time, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.identity import UserDB
from app.models.user_info import UserInfo
from app.models.wechat_activity import WechatUserActivity
from app.models.wxpay import RewardConfig, RewardRecord, RewardStatus
from app.modules.wxpay.schemas import GrantRewardIn
from app.modules.wxpay.service import RewardService

logger = logging.getLogger(__name__)

# 规则配置键（RewardConfig.config_key）
CONFIG_KEY = "newcomer_bonus"
# 奖励来源类型 + 角色（参与唯一约束，幂等的核心）
SOURCE_TYPE = "newcomer_bonus"
ROLE = "newcomer"

# 内置默认值（配置行缺失时生效）
DEFAULT_AMOUNT_FEN = 50          # 0.5 元
DEFAULT_CAMPAIGN = "ors1086"
DEFAULT_START_AT = "2026-11-03T04:00:00Z"   # 2026-11-03 12:00 北京时间
DEFAULT_TITLE = "新人福利"
DEFAULT_DESC = "新关注摇人吧，0.5 元现金红包可领，点「去领取」到「我的奖励」提现到微信零钱。"
DEFAULT_SCENE_NAME = "新人红包"
DEFAULT_REWARD_DESC = "新人福利 0.5 元"


def _utcnow() -> datetime:
    """当前 UTC 时间（带时区）。统一走这里，避免各处 datetime.now() 混用。"""
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    """解析 ISO8601 时间串（接受尾部 Z），统一返回 UTC aware datetime。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class NewcomerBonusConfig:
    """活动配置（来自 RewardConfig['newcomer_bonus']，缺项用默认值）。"""

    def __init__(self, raw: Optional[Dict[str, Any]] = None):
        raw = raw or {}
        self.amount_fen = int(raw.get("amount_fen") or DEFAULT_AMOUNT_FEN)
        self.campaign_code = str(raw.get("campaign_code") or DEFAULT_CAMPAIGN)
        self.start_at = _parse_dt(raw.get("start_at")) or _parse_dt(DEFAULT_START_AT)
        self.end_at = _parse_dt(raw.get("end_at"))
        self.title = str(raw.get("title") or DEFAULT_TITLE)
        self.desc = str(raw.get("desc") or DEFAULT_DESC)
        self.scene_name = str(raw.get("scene_name") or DEFAULT_SCENE_NAME)
        self.reward_desc = str(raw.get("reward_desc") or DEFAULT_REWARD_DESC)


async def load_config(db: AsyncSession) -> Tuple[bool, NewcomerBonusConfig]:
    """读活动配置。返回 (是否启用, 配置)。

    配置行不存在 → 用默认值并视为启用；配置行存在但 `enabled=false` → 活动关闭。
    """
    row = (
        await db.execute(select(RewardConfig).where(RewardConfig.config_key == CONFIG_KEY))
    ).scalar_one_or_none()
    if row is None:
        return True, NewcomerBonusConfig()
    if not row.enabled:
        return False, NewcomerBonusConfig()
    raw: Dict[str, Any] = {}
    try:
        raw = json.loads(row.config) if isinstance(row.config, str) else dict(row.config or {})
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("[newcomer_bonus] 配置 JSON 解析失败，回退默认配置")
        raw = {}
    return True, NewcomerBonusConfig(raw)


def resolve_openid(user: UserDB) -> str:
    """可收款的 openid。

    微信登录用户的 `users.id` 本身就是 openid；业务账号需绑 `wechat_openid`。
    两者都没有 → 返回空串（不可领）。
    """
    if (user.wechat_openid or "").strip():
        return user.wechat_openid.strip()
    if (user.username or "").startswith("wechat_"):
        return user.id
    return ""


async def resolve_subscribe_time(db: AsyncSession, openid: str) -> Optional[datetime]:
    """首次关注公众号的时间（UTC）。取不到返回 None（视为不可领）。

    三级取数，任何一级命中即返回：
    1. 微信实时接口 `/cgi-bin/user/info` 的 `subscribe_time`（最准，含未关注=0 的判定）；
    2. `user_info` 整点快照里的 `subscribe_time`（微信接口抖动时兜底）；
    3. `wechat_user_activity` 里最早的 `source='event'` 日期（关注/扫码事件，近似值）。
    """
    try:
        from app.wechat.services.wechat_service import wechat_service

        info = await wechat_service.get_user_info(openid)
        if info:
            if info.get("subscribe") == 0:
                return None  # 已取关，不算新用户
            stamp = info.get("subscribe_time")
            if stamp:
                return datetime.fromtimestamp(int(stamp), tz=timezone.utc)
    except Exception as exc:  # noqa: BLE001 —— 外部接口失败不应阻断弹窗
        logger.warning("[newcomer_bonus] 查微信关注信息失败，走兜底: %s", exc)

    try:
        row = (
            await db.execute(select(UserInfo).order_by(UserInfo.created_time.desc()).limit(1))
        ).scalar_one_or_none()
        payload = (row.user_info if row else None) or {}
        for item in payload.get("user_info_list") or []:
            if item.get("openid") != openid:
                continue
            if item.get("subscribe") == 0:
                return None
            stamp = item.get("subscribe_time")
            if stamp:
                return datetime.fromtimestamp(int(stamp), tz=timezone.utc)
            break
    except Exception as exc:  # noqa: BLE001
        logger.warning("[newcomer_bonus] 读用户信息快照失败: %s", exc)

    try:
        earliest = (
            await db.execute(
                select(func.min(WechatUserActivity.ref_date)).where(
                    WechatUserActivity.openid == openid,
                    WechatUserActivity.source == "event",
                )
            )
        ).scalar_one_or_none()
        if isinstance(earliest, date):
            return datetime.combine(earliest, time.min, tzinfo=timezone.utc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[newcomer_bonus] 读微信活跃记录失败: %s", exc)

    return None


async def find_claimed(
    db: AsyncSession, openid: str, campaign_code: str
) -> Optional[RewardRecord]:
    """查是否已领过（同一活动同一 openid 只发一次）。"""
    return (
        await db.execute(
            select(RewardRecord).where(
                RewardRecord.source_type == SOURCE_TYPE,
                RewardRecord.source_id == f"{campaign_code}:{openid}",
            )
        )
    ).scalar_one_or_none()


async def evaluate(db: AsyncSession, user: UserDB) -> Dict[str, Any]:
    """弹窗状态：能不能领、领没领过、领多少。

    `reason` 说明不可领的原因（仅用于前端提示与排查，不做权限判定）。
    """
    enabled, config = await load_config(db)
    state: Dict[str, Any] = {
        "eligible": False,
        "claimed": False,
        "amount_fen": config.amount_fen,
        "title": config.title,
        "desc": config.desc,
        "reason": "",
        "campaign_code": config.campaign_code,
        "reward_id": "",
    }
    if not enabled:
        state["reason"] = "活动未开启"
        return state

    openid = resolve_openid(user)
    if not openid:
        state["reason"] = "仅微信用户可领取"
        return state

    claimed = await find_claimed(db, openid, config.campaign_code)
    if claimed is not None:
        state["claimed"] = True
        state["reward_id"] = claimed.id
        state["reason"] = "已领取过"
        return state

    subscribed_at = await resolve_subscribe_time(db, openid)
    if subscribed_at is None:
        state["reason"] = "未取到关注时间，暂不可领取"
        return state
    if subscribed_at < config.start_at:
        state["reason"] = "非活动期新关注用户"
        return state
    if config.end_at is not None and subscribed_at > config.end_at:
        state["reason"] = "非活动期新关注用户"
        return state

    state["eligible"] = True
    return state


async def claim(db: AsyncSession, user: UserDB) -> Dict[str, Any]:
    """领取：发放一笔新人奖励（只记账，用户到「我的奖励」自己提现）。"""
    state = await evaluate(db, user)
    result: Dict[str, Any] = {
        "ok": False,
        "claimed": state["claimed"],
        "created": False,
        "reward_id": state["reward_id"],
        "amount_fen": state["amount_fen"],
        "title": state["title"],
        "desc": state["desc"],
        "message": state["reason"],
    }
    if state["claimed"]:
        # 已领过：幂等返回同一条记录（不重复发钱），前端据此提示「已领取」
        result.update(
            ok=True,
            claimed=True,
            created=False,
            reward_id=state["reward_id"],
            message="已领取过，去「我的奖励」提现",
        )
        return result
    if not state["eligible"]:
        return result

    # 微信支付未启用时不记账：记了账也提不出来（withdraw 会被同一开关拦下）
    if not getattr(settings, "WXPAY_ENABLED", False):
        result["message"] = "微信支付功能未启用"
        return result

    _, config = await load_config(db)
    openid = resolve_openid(user)
    record, duplicated = await RewardService.grant(
        db,
        GrantRewardIn(
            user_id=user.id,
            openid=openid,
            amount=config.amount_fen,
            scene_name=config.scene_name,
            reward_desc=config.reward_desc,
            remark=config.title,
            source_type=SOURCE_TYPE,
            source_id=f"{config.campaign_code}:{openid}",
        ),
        role=ROLE,
        status=RewardStatus.PENDING,
    )
    result.update(
        ok=True,
        claimed=True,
        # grant 返回的布尔是「是否重复发放」，这里取反成「本次是否新建」
        created=not duplicated,
        reward_id=record.id,
        amount_fen=record.amount,
        message="领取成功，去「我的奖励」提现到微信零钱",
    )
    return result
