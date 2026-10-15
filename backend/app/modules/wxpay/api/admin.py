"""微信支付管理端接口（后台管理 → 其他 → 奖励管理）。

路由前缀 /api/wxpay/admin，鉴权：JWT + `frontend:admin:wxpay-rewards:show`。

覆盖范围说明（「增删改查」并非都能安全暴露）：
- 增：发放奖励 → 对该奖励发起转账，这两步合起来即一次「转账测试」
- 查：转账单列表 / 详情 / 状态分布、奖励列表
- 改：查单刷新（从微信侧同步最新状态）、撤销转账
- 删：**刻意不提供**。转账单是资金流水，兜底查单与一致性巡检都依赖
  `transfer_bill` 留存，删除会让本地与微信侧对不上账。测试产生的脏数据
  请走「撤销」而非删除。

⚠️ 撤销是「受理」语义：Go 侧返回成功只代表微信受理，必须再查单确认是否到终态，
   因此这里撤销后立刻回查一次并把最新状态返回（见 `cancel_bill`）。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.models.identity import UserDB
from app.models.wxpay import RewardRecord, TransferBill, TransferBillState
from app.modules.admin.api.auth import require_permission
from app.modules.wxpay.client import WxpayServiceError, wxpay_client
from app.modules.wxpay.schemas import (
    AdminBillActionOut,
    AdminBillListOut,
    AdminBillOut,
    AdminBillStatsOut,
    AdminRewardListOut,
    AdminRewardOut,
    AdminUserOptionListOut,
    AdminUserOptionOut,
    GrantRewardIn,
    GrantRewardOut,
    WithdrawIn,
    WithdrawOut,
)
from app.modules.wxpay.service import RewardService, WithdrawService
from app.services.identity_service import IdentityService
from app.services.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-wxpay"])

PERM = "frontend:admin:wxpay-rewards:show"


def ensure_permission() -> None:
    """确保权限码已注册到 permissions 表（幂等，已存在则跳过）。

    与 task_policy.py 同款写法：每次调用触发一次，避免新环境部署后
    权限表里没有该码导致管理员全部 403。
    """
    try:
        IdentityService.add_permission(
            permission_id="perm_frontend_admin_wxpay_rewards_show",
            code=PERM,
            name="奖励管理",
            resource_type="frontend",
            action="show",
            description="后台管理-其他-奖励管理（微信商家转账：转账测试 / 转账单运维）",
        )
    except Exception as e:  # noqa: BLE001
        # 权限注册失败不应阻断业务（例如首次部署时表尚未就绪）
        logger.warning(f"[wxpay] 权限码注册失败（忽略）: {e}")


def _bill_out(bill: TransferBill, reward: Optional[RewardRecord] = None) -> AdminBillOut:
    """转账单 → 出参（补齐关联的奖励/用户信息）。"""
    return AdminBillOut(
        id=bill.id,
        reward_id=bill.reward_id,
        out_bill_no=bill.out_bill_no,
        transfer_bill_no=bill.transfer_bill_no,
        appid=bill.appid,
        mchid=bill.mchid,
        openid=bill.openid,
        amount=bill.amount,
        state=bill.state,
        is_terminal=bool(bill.is_terminal),
        fail_reason=bill.fail_reason,
        wx_create_time=bill.wx_create_time,
        wx_update_time=bill.wx_update_time,
        last_query_at=bill.last_query_at,
        query_count=bill.query_count or 0,
        created_at=bill.created_at,
        updated_at=bill.updated_at,
        reward_status=reward.status if reward else None,
        reward_amount=reward.amount if reward else None,
        user_id=reward.user_id if reward else None,
    )


def _receivable_openid(uid: str, username: str, bound: Optional[str]) -> tuple[str, str]:
    """解析用户可收款 openid → (openid, source)。

    绑定列优先；微信注册用户 users.id 本身就是 openid，无需也不应该再占绑定列。
    """
    if bound and bound.strip():
        return bound.strip(), "bound"
    if (username or "").startswith("wechat_"):
        return uid, "self"
    return "", ""


# ── 转账测试：收款用户候选（下拉数据源，读 users 表）──


@router.get(
    "/user-options", response_model=AdminUserOptionListOut, summary="收款用户候选（转账测试下拉）"
)
async def list_user_options(
    keyword: Optional[str] = Query(None, description="id / 账号 / 姓名 / openid 模糊匹配"),
    only_receivable: bool = Query(False, description="true=仅返回能解析出 openid 的用户"),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    """转账测试表单的「用户 ID」下拉候选。

    刻意不复用 `GET /api/tasks/assignable-users`：它无 openid，且只需登录即可调用，
    把收款 openid 暴露给全体登录用户不合适；这里受同一 PERM 兜住。
    """
    ensure_permission()
    stmt = select(UserDB.id, UserDB.username, UserDB.name, UserDB.wechat_openid)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                UserDB.id.like(like),
                UserDB.username.like(like),
                UserDB.name.like(like),
                UserDB.wechat_openid.like(like),
            )
        )
    if only_receivable:
        stmt = stmt.where(
            or_(
                UserDB.wechat_openid.isnot(None),
                UserDB.wechat_openid != "",
                UserDB.username.like("wechat_%"),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar() or 0
    rows = (await db.execute(stmt.order_by(UserDB.username).limit(limit))).all()

    items = []
    for uid, username, name, bound in rows:
        openid, source = _receivable_openid(uid or "", username or "", bound)
        items.append(
            AdminUserOptionOut(
                id=uid or "",
                username=username or "",
                name=name,
                wechat_openid=bound,
                openid=openid,
                openid_source=source,
            )
        )
    # 可收款的排前面：转账下拉绝大多数时候就是在挑这些人
    items.sort(key=lambda o: (0 if o.openid else 1, o.username))
    return AdminUserOptionListOut(total=total, truncated=total > len(items), items=items)


# ── 概览：状态分布（必须声明在 /bills/{bill_id} 之前，否则被路径参数吃掉）──


@router.get("/bills/stats", response_model=AdminBillStatsOut, summary="转账单状态分布")
async def bill_stats(
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    # 必须 group_by：否则 MySQL 的 ONLY_FULL_GROUP_BY 会直接报错，
    # 且不加分组时 count(*) 会把整表聚成一行的「无意义计数」。
    rows = (
        await db.execute(
            select(TransferBill.state, func.count()).group_by(TransferBill.state)
        )
    ).all()
    by_state: dict = {}
    for state, cnt in rows:
        by_state[state or "(空)"] = cnt
    total = sum(by_state.values())
    terminal = sum(
        cnt for state, cnt in by_state.items() if TransferBillState.is_terminal(state)
    )
    return AdminBillStatsOut(
        total=total,
        terminal_count=terminal,
        open_count=total - terminal,
        by_state=dict(sorted(by_state.items(), key=lambda kv: -kv[1])),
    )


@router.get("/bills", response_model=AdminBillListOut, summary="转账单列表")
async def list_bills(
    state: Optional[str] = Query(None, description="按微信侧状态精确过滤"),
    only_open: Optional[bool] = Query(None, description="true=仅非终态；false=仅终态"),
    keyword: Optional[str] = Query(
        None, description="out_bill_no / transfer_bill_no / openid 模糊匹配"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    stmt = select(TransferBill)
    if state:
        stmt = stmt.where(TransferBill.state == state)
    if only_open is True:
        stmt = stmt.where(TransferBill.is_terminal.is_(False))
    elif only_open is False:
        stmt = stmt.where(TransferBill.is_terminal.is_(True))
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                TransferBill.out_bill_no.like(like),
                TransferBill.transfer_bill_no.like(like),
                TransferBill.openid.like(like),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar() or 0
    bills = (
        (
            await db.execute(
                stmt.order_by(TransferBill.created_at.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    # 批量取关联奖励，避免 N+1
    rewards = {}
    reward_ids = [b.reward_id for b in bills if b.reward_id]
    if reward_ids:
        rows = (
            (await db.execute(select(RewardRecord).where(RewardRecord.id.in_(reward_ids))))
            .scalars()
            .all()
        )
        rewards = {r.id: r for r in rows}

    return AdminBillListOut(
        total=total,
        page=page,
        size=size,
        items=[_bill_out(b, rewards.get(b.reward_id)) for b in bills],
    )


@router.get("/bills/{bill_id}", response_model=AdminBillOut, summary="转账单详情")
async def get_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    bill = await db.get(TransferBill, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="转账单不存在")
    reward = await db.get(RewardRecord, bill.reward_id) if bill.reward_id else None
    return _bill_out(bill, reward)


@router.post(
    "/bills/{bill_id}/refresh", response_model=AdminBillActionOut, summary="查单刷新"
)
async def refresh_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    """向微信查单并回写最新状态。

    微信侧查无此单（404）时 `WithdrawService._refresh_bill` 会删除占位记录，
    此时返回 deleted=True，前端应刷新列表而不是更新该行。
    """
    ensure_permission()
    bill = await db.get(TransferBill, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="转账单不存在")

    updated = await WithdrawService._refresh_bill(db, bill)
    if updated is None:
        return AdminBillActionOut(
            ok=True,
            deleted=True,
            message="微信侧查无此单，已清理本地占位记录",
        )
    reward = await db.get(RewardRecord, updated.reward_id) if updated.reward_id else None
    return AdminBillActionOut(
        ok=True,
        message=f"已刷新，当前状态 {updated.state or '(空)'}",
        bill=_bill_out(updated, reward),
    )


@router.post(
    "/bills/{bill_id}/cancel", response_model=AdminBillActionOut, summary="撤销转账"
)
async def cancel_bill(
    bill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    bill = await db.get(TransferBill, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="转账单不存在")
    if bill.is_terminal:
        raise HTTPException(
            status_code=400, detail=f"转账单已处于终态（{bill.state}），不可撤销"
        )

    try:
        await wxpay_client.cancel_transfer(bill.out_bill_no)
    except WxpayServiceError as e:
        raise HTTPException(
            status_code=502, detail=f"撤销失败：{e.message}（code={e.code}）"
        ) from e

    # 撤销是受理语义，必须回查一次才能拿到真实终态
    updated = await WithdrawService._refresh_bill(db, bill)
    if updated is None:
        return AdminBillActionOut(
            ok=True,
            deleted=True,
            message="已受理撤销，且微信侧已查无此单，占位记录已清理",
        )
    reward = await db.get(RewardRecord, updated.reward_id) if updated.reward_id else None
    return AdminBillActionOut(
        ok=True,
        message=f"撤销已受理，当前状态 {updated.state or '(空)'}"
        + ("" if updated.is_terminal else "（未到终态，可稍后再次查单）"),
        bill=_bill_out(updated, reward),
    )


# ── 奖励 ──


@router.get("/rewards", response_model=AdminRewardListOut, summary="奖励记录列表")
async def list_rewards(
    user_id: Optional[str] = Query(None, description="按用户 ID 过滤"),
    status: Optional[str] = Query(
        None, description="pending/withdrawing/success/expired"
    ),
    keyword: Optional[str] = Query(
        None, description="openid / scene_name / source_id 模糊匹配"
    ),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    stmt = select(RewardRecord)
    if user_id:
        stmt = stmt.where(RewardRecord.user_id == user_id)
    if status:
        stmt = stmt.where(RewardRecord.status == status)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                RewardRecord.openid.like(like),
                RewardRecord.scene_name.like(like),
                RewardRecord.source_id.like(like),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar() or 0
    rewards = (
        (
            await db.execute(
                stmt.order_by(RewardRecord.created_at.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        )
        .scalars()
        .all()
    )

    # 补当前有效转账单（与用户端 RewardService.to_out 同口径：取最近一笔非终态）
    items: list[AdminRewardOut] = []
    for r in rewards:
        bill = (
            (
                await db.execute(
                    select(TransferBill)
                    .where(
                        TransferBill.reward_id == r.id,
                        TransferBill.is_terminal.is_(False),
                    )
                    .order_by(TransferBill.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        items.append(
            AdminRewardOut(
                id=r.id,
                user_id=r.user_id,
                openid=r.openid or "",
                amount=r.amount,
                scene_name=r.scene_name or "",
                reward_desc=r.reward_desc or "",
                remark=r.remark or "",
                source_type=r.source_type or "",
                source_id=r.source_id,
                status=r.status,
                bill_state=bill.state if bill else None,
                out_bill_no=bill.out_bill_no if bill else None,
                expire_at=r.expire_at,
                withdrawn_at=r.withdrawn_at,
                created_at=r.created_at,
            )
        )

    return AdminRewardListOut(total=total, page=page, size=size, items=items)


@router.post("/rewards/grant", response_model=GrantRewardOut, summary="发放奖励（记账）")
async def grant_reward(
    payload: GrantRewardIn,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    """发放奖励，仅记账不建单；随后对返回的 id 调 withdraw 才会真正发起转账。"""
    ensure_permission()
    record, duplicated = await RewardService.grant(db, payload)
    return GrantRewardOut(
        id=record.id,
        user_id=record.user_id,
        amount=record.amount,
        status=record.status,
        expire_at=record.expire_at,
        duplicated=duplicated,
    )


@router.post(
    "/rewards/{reward_id}/withdraw", response_model=WithdrawOut, summary="发起转账"
)
async def withdraw(
    reward_id: str,
    body: Optional[WithdrawIn] = None,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    """对指定奖励发起转账（三分支决策由 WithdrawService 内部完成）。

    管理端发起与用户自提现走同一套决策逻辑，因此同样满足「同一奖励至多一笔
    非终态转账单」，不会因后台重复点击造成重复出资。
    """
    ensure_permission()
    reward = await db.get(RewardRecord, reward_id)
    if reward is None:
        raise HTTPException(status_code=404, detail="奖励记录不存在")
    return await WithdrawService.withdraw(
        db, reward_id, reward.user_id, (body.user_name if body else "") or ""
    )
