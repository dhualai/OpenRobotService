"""用户端「我的历史奖励」接口（需 JWT 登录态）。

路由前缀 /api/wxpay/rewards。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.core.security import decode_token
from app.models.identity import UserDB
from app.modules.wxpay.schemas import (
    RewardListOut,
    RewardOut,
    RewardStatsOut,
    WithdrawIn,
    WithdrawOut,
)
from app.modules.wxpay.service import RewardService, WithdrawService
from app.services.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/rewards", tags=["wxpay-rewards"])


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> UserDB:
    """从 Authorization: Bearer <token> 解析当前用户。"""
    auth_header = request.headers.get("Authorization")
    token = auth_header[7:] if auth_header and auth_header.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="未授权")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="无效的token")

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="无法获取用户信息")

    user = (
        await db.execute(select(UserDB).where(UserDB.username == username))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


@router.get("/", response_model=RewardListOut, summary="我的历史奖励")
async def list_rewards(
    status: Optional[str] = Query(None, description="pending/withdrawing/success/expired"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(current_user),
):
    rows, total = await RewardService.list_rewards(db, user.id, status, page, size)
    items = [await RewardService.to_out(db, r) for r in rows]
    return RewardListOut(total=total, page=page, size=size, items=items)


@router.get("/stats", response_model=RewardStatsOut, summary="奖励汇总")
async def reward_stats(
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(current_user),
):
    return await RewardService.stats(db, user.id)


@router.get("/{reward_id}", response_model=RewardOut, summary="奖励详情")
async def get_reward(
    reward_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(current_user),
):
    reward = await RewardService.get(db, reward_id, user.id)
    if reward is None:
        raise HTTPException(status_code=404, detail="奖励不存在")
    return await RewardService.to_out(db, reward)


@router.post("/{reward_id}/withdraw", response_model=WithdrawOut, summary="立即提现 / 去确认")
async def withdraw(
    reward_id: str,
    body: Optional[WithdrawIn] = None,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(current_user),
):
    """提现入口。

    内部走方案 4.3 的三分支决策：
    - confirm         用返回的 package 调起 requestMerchantTransfer
    - processing      转账单处于其它非终态，稍后重试
    - need_real_name  收款人姓名未通过微信实名校验，前端弹窗让用户填写真实姓名后
                      带着 body.user_name 重新调用本接口
    - success / expired / rejected 按 message 提示
    """
    return await WithdrawService.withdraw(
        db, reward_id, user.id, (body.user_name if body else "") or ""
    )
