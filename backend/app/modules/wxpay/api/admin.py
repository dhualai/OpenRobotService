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

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.models.identity import UserDB
from app.models.wxpay import (
    RewardConfig,
    RewardRecord,
    RewardStatus,
    TransferBill,
    TransferBillState,
)
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
    BatchAuditIn,
    BatchAuditOut,
    GrantRewardIn,
    GrantRewardOut,
    RewardAdminStatsOut,
    RewardConfigOut,
    RewardConfigUpdateIn,
    WithdrawIn,
    WithdrawOut,
)
from app.modules.wxpay.service import AuditDecisionEngine, RewardService, WithdrawService
from app.modules.wxpay.archive_finance import (
    ARCHIVE_FINANCE_SOURCE, archive_finance_detail, audit_archive_rewards,
)
from app.modules.admin.services.knowledge_service import actor_id
from app.services.identity_service import IdentityService
from app.services.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-wxpay"])

PERM = "frontend:admin:wxpay-rewards:show"          # 页面入口（转账测试 + 转账单 + 奖励记录）
PERM_AUDIT = "frontend:admin:reward-audit:write"     # 财务审核（通过/驳回 + 手动触发自动通过）
PERM_CONFIG = "frontend:admin:reward-config:write"   # 规则配置编辑


def ensure_permission() -> None:
    """确保三个权限码已注册（幂等）。"""
    try:
        IdentityService.add_permission(
            permission_id="perm_frontend_admin_wxpay_rewards_show",
            code=PERM,
            name="奖励管理（页面入口）",
            resource_type="frontend",
            action="show",
            description="后台管理-其他-奖励管理（转账测试 / 转账单运维 / 奖励记录查看）",
        )
        IdentityService.add_permission(
            permission_id="perm_frontend_admin_reward_audit_write",
            code=PERM_AUDIT,
            name="奖励财务审核",
            resource_type="frontend",
            action="write",
            description="审核待财务审核的奖励，通过/驳回/手动触发自动通过",
        )
        IdentityService.add_permission(
            permission_id="perm_frontend_admin_reward_config_write",
            code=PERM_CONFIG,
            name="奖励规则配置",
            resource_type="frontend",
            action="write",
            description="编辑闭环奖励金额 / 月度排名阶梯 / 点赞单价 / 审核策略",
        )
    except Exception as e:  # noqa: BLE001
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
        None, description="pending_audit/rejected/pending/withdrawing/success/expired"
    ),
    role: Optional[str] = Query(None, description="按角色过滤: reporter/assignee/reviewer/liker/ranker/manual"),
    source_type: Optional[str] = Query(None, description="按来源类型过滤: task/task_like/monthly_rank/admin_test"),
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
    if role:
        stmt = stmt.where(RewardRecord.role == role)
    if source_type:
        stmt = stmt.where(RewardRecord.source_type == source_type)
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
                role=r.role,
                status=r.status,
                audit_by=r.audit_by,
                audit_at=r.audit_at,
                audit_remark=r.audit_remark,
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
    record, duplicated = await RewardService.grant(
        db, payload, role="manual", status=RewardStatus.PENDING
    )
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


# ── 奖励审核与规则配置 ──


@router.get("/rewards/stats", response_model=RewardAdminStatsOut, summary="奖励状态分布概览")
async def reward_stats(
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    """管理端 Tab 顶部角标数据（各状态笔数 / 待审核总额）。"""
    ensure_permission()
    rows = (
        await db.execute(
            select(
                RewardRecord.status,
                func.count().label("cnt"),
                func.coalesce(func.sum(RewardRecord.amount), 0).label("amt"),
            ).group_by(RewardRecord.status)
        )
    ).all()
    by_status: dict[str, tuple[int, int]] = {
        s or "": (int(cnt), int(amt)) for s, cnt, amt in rows
    }
    pending_audit_cnt, pending_audit_amt = by_status.get(
        RewardStatus.PENDING_AUDIT, (0, 0)
    )
    rejected_cnt, _ = by_status.get(RewardStatus.REJECTED, (0, 0))
    pending_cnt, _ = by_status.get(RewardStatus.PENDING, (0, 0))
    withdrawing_cnt, _ = by_status.get(RewardStatus.WITHDRAWING, (0, 0))
    success_cnt, _ = by_status.get(RewardStatus.SUCCESS, (0, 0))
    expired_cnt, _ = by_status.get(RewardStatus.EXPIRED, (0, 0))
    return RewardAdminStatsOut(
        pending_audit_count=pending_audit_cnt,
        pending_audit_amount=pending_audit_amt,
        rejected_count=rejected_cnt,
        pending_count=pending_cnt,
        withdrawing_count=withdrawing_cnt,
        success_count=success_cnt,
        expired_count=expired_cnt,
    )


@router.get("/archive-finance/{task_id}", summary="工单财务审核详情")
async def get_archive_finance_detail(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM_AUDIT),
):
    return await archive_finance_detail(db, task_id)


@router.post("/rewards/batch-audit", response_model=BatchAuditOut, summary="批量审核通过")
async def batch_audit(
    payload: BatchAuditIn,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM_AUDIT),
):
    """将一批 pending_audit 奖励审核通过 → 状态置为 pending（可提现）。

    仅 pending_audit 状态可处理，其它状态跳过。
    到账发起由 wxpay 定时任务 `_flush_pending_rewards_to_withdraw` 统一扫事务外执行，
    不在审核事务内调微信转账 API（规范 §5.2）。
    """
    ensure_permission()
    ids = list(dict.fromkeys(payload.ids))  # 去重
    operator_id = await actor_id(db, current_user)
    archive_rewards = await audit_archive_rewards(db, ids, operator_id, True, payload.audit_remark)

    rewards = (
        (
            await db.execute(
                select(RewardRecord).where(
                    RewardRecord.id.in_(ids),
                    RewardRecord.status == RewardStatus.PENDING_AUDIT,
                    RewardRecord.source_type != ARCHIVE_FINANCE_SOURCE,
                )
            )
        )
        .scalars()
        .all()
    )
    valid_ids = {r.id for r in [*rewards, *archive_rewards]}
    skipped = [i for i in ids if i not in valid_ids]

    now = func.now()
    for r in rewards:
        r.status = RewardStatus.PENDING
        r.audit_by = operator_id
        r.audit_at = now

    await db.commit()

    return BatchAuditOut(
        ok_count=len(rewards) + len(archive_rewards),
        skipped_ids=skipped,
        message=(
            f"审核通过 {len(rewards) + len(archive_rewards)} 条，跳过 {len(skipped)} 条非待审核。"
            f"工单闭环奖励按三方整单审核，通过后归档入榜。"
            f"转账将由系统定时任务异步发起。"
        ),
    )


@router.post("/rewards/batch-reject", response_model=BatchAuditOut, summary="批量驳回")
async def batch_reject(
    payload: BatchAuditIn,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM_AUDIT),
):
    """将一批 pending_audit 奖励驳回（终态，不发钱）。"""
    ensure_permission()
    ids = list(dict.fromkeys(payload.ids))
    if not payload.audit_remark.strip():
        raise HTTPException(422, "财务驳回必须填写审核意见")
    operator_id = await actor_id(db, current_user)
    archive_rewards = await audit_archive_rewards(db, ids, operator_id, False, payload.audit_remark)

    rewards = (
        (
            await db.execute(
                select(RewardRecord).where(
                    RewardRecord.id.in_(ids),
                    RewardRecord.status == RewardStatus.PENDING_AUDIT,
                    RewardRecord.source_type != ARCHIVE_FINANCE_SOURCE,
                )
            )
        )
        .scalars()
        .all()
    )
    valid_ids = {r.id for r in [*rewards, *archive_rewards]}
    skipped = [i for i in ids if i not in valid_ids]

    now = func.now()
    for r in rewards:
        r.status = RewardStatus.REJECTED
        r.audit_by = operator_id
        r.audit_at = now
        r.audit_remark = payload.audit_remark or "财务驳回"

    await db.commit()

    return BatchAuditOut(
        ok_count=len(rewards) + len(archive_rewards),
        skipped_ids=skipped,
        message=f"驳回 {len(rewards) + len(archive_rewards)} 条，工单闭环奖励按三方整单驳回，不发奖、不入榜",
    )


@router.post("/rewards/run-auto-pass", response_model=BatchAuditOut, summary="手动触发自动通过")
async def run_auto_pass(
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM_AUDIT),
):
    """对所有 pending_audit 奖励重新跑审核决策引擎，符合条件的自动推进。

    场景：财务调低 auto_pass_max_fen 阈值后，把之前因旧阈值卡进 pending_audit
          但现在可自动通过的奖励"释放"出来。
    """
    ensure_permission()
    policy = await RewardService.resolve_config(db, "audit_policy")
    if not policy:
        return BatchAuditOut(ok_count=0, skipped_ids=[], message="审核策略未启用")

    pending_audit = (
        (
            await db.execute(
                select(RewardRecord).where(
                    RewardRecord.status == RewardStatus.PENDING_AUDIT,
                    RewardRecord.source_type != ARCHIVE_FINANCE_SOURCE,
                )
            )
        )
        .scalars()
        .all()
    )

    auto_passed: list[RewardRecord] = []
    for r in pending_audit:
        if await AuditDecisionEngine.evaluate(db, r.user_id, r.amount, policy):
            r.status = RewardStatus.PENDING
            r.audit_by = "AUTO"
            r.audit_at = func.now()
            auto_passed.append(r)

    await db.commit()

    return BatchAuditOut(
        ok_count=len(auto_passed),
        skipped_ids=[r.id for r in pending_audit if r not in auto_passed],
        message=(
            f"自动通过 {len(auto_passed)} 条"
            f"（其余 {len(pending_audit) - len(auto_passed)} 条仍需人工审核）。"
            f"转账将由系统定时任务异步发起。"
        ),
    )


# ── 规则配置 ──


@router.get("/reward-config", summary="规则配置列表")
async def list_reward_config(
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM),
):
    ensure_permission()
    rows = (
        (
            await db.execute(select(RewardConfig).order_by(RewardConfig.config_key))
        )
        .scalars()
        .all()
    )
    return [
        RewardConfigOut(
            config_key=r.config_key,
            enabled=r.enabled,
            config=json.loads(r.config) if r.config else {},
            updated_by=r.updated_by,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.put(
    "/reward-config/{config_key}",
    response_model=RewardConfigOut,
    summary="更新规则配置",
)
async def update_reward_config(
    config_key: str,
    payload: RewardConfigUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user=require_permission(PERM_CONFIG),
):
    ensure_permission()
    row = (
        await db.execute(
            select(RewardConfig).where(RewardConfig.config_key == config_key)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"配置项 {config_key} 不存在")

    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.config is not None:
        row.config = json.dumps(payload.config, ensure_ascii=False)
    row.updated_by = current_user
    await db.commit()
    await db.refresh(row)

    return RewardConfigOut(
        config_key=row.config_key,
        enabled=row.enabled,
        config=json.loads(row.config) if row.config else {},
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )
