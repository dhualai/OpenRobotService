"""微信支付内部接口（供 Go 服务调用 / 内部发奖）。

⚠️ 路由注册时**不能**加 `/api` 前缀，必须严格等于 Go 侧配置的
   `{WXPAY_FASTAPI_BASE_URL}/internal/wxpay/notify/transfer`。

鉴权：X-Internal-Token（不给公网暴露，无 JWT 登录态）。
"""

import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_async_db as get_db
from app.modules.wxpay.schemas import (
    GrantRewardIn,
    GrantRewardOut,
    NotifyAckOut,
    TransferNotifyIn,
)
from app.modules.wxpay.service import NotifyService, RewardService
from app.services.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/internal/wxpay", tags=["wxpay-internal"])


def verify_internal_token(x_internal_token: Optional[str] = Header(None)) -> None:
    expected = getattr(settings, "WXPAY_INTERNAL_TOKEN", "") or ""
    if not expected:
        raise HTTPException(status_code=503, detail="内部令牌未配置")
    if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(status_code=401, detail="无效的内部令牌")


@router.post(
    "/notify/transfer",
    response_model=NotifyAckOut,
    summary="接收 Go 服务推送的商家转账回调",
    dependencies=[Depends(verify_internal_token)],
)
async def transfer_notify(
    payload: TransferNotifyIn,
    db: AsyncSession = Depends(get_db),
):
    """回调落库。

    - 必须返回 2xx，否则 Go 侧应答 5xx 触发微信重试
    - 幂等由 wxpay_notify_log.notify_id 唯一索引保证
    """
    duplicated, reason = await NotifyService.handle(db, payload)
    return NotifyAckOut(code="OK", message=reason, duplicated=duplicated)


@router.post(
    "/rewards/grant",
    response_model=GrantRewardOut,
    summary="发放奖励（仅记账，不创建转账单）",
    dependencies=[Depends(verify_internal_token)],
)
async def grant_reward(
    payload: GrantRewardIn,
    db: AsyncSession = Depends(get_db),
):
    record, duplicated = await RewardService.grant(db, payload)
    return GrantRewardOut(
        id=record.id,
        user_id=record.user_id,
        amount=record.amount,
        status=record.status,
        expire_at=record.expire_at,
        duplicated=duplicated,
    )
