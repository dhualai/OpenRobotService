"""微信支付 Pydantic 模型。"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ============ 奖励 ============


class RewardOut(BaseModel):
    id: str
    amount: int = Field(..., description="奖励金额，单位：分")
    scene_name: str
    reward_desc: str
    status: str = Field(..., description="pending/withdrawing/success/expired")
    expire_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    # 当前有效转账单（用于前端决定按钮文案）
    bill_state: Optional[str] = None
    out_bill_no: Optional[str] = None

    class Config:
        from_attributes = True


class RewardListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[RewardOut]


class RewardStatsOut(BaseModel):
    pending_amount: int = Field(0, description="待提现总额（分）")
    pending_count: int = 0
    withdrawn_amount: int = Field(0, description="累计已到账（分）")


# ============ 提现 ============


class WithdrawOut(BaseModel):
    """提现/去确认结果。

    action:
    - confirm         有待确认转账单，用 package 调起 requestMerchantTransfer
    - processing      转账单处于其它非终态，提示稍后再试
    - need_real_name  收款人姓名未通过微信实名校验，需用户填写真实姓名后重新提交
    - success         已到账
    - expired         奖励已失效
    - rejected        不满足提现条件（金额/状态），见 message
    """

    action: str
    message: str = ""
    mch_id: str = ""
    app_id: str = ""
    package: str = ""
    out_bill_no: str = ""
    state: str = ""


class WithdrawIn(BaseModel):
    """提现请求体。

    user_name 仅在后端返回 `action=need_real_name` 后由用户填写并再次提交。
    微信受理该姓名后会被固化为 `users.name`，后续支付自动复用，无需再次填写。
    """

    user_name: str = Field("", max_length=64, description="收款人真实姓名，须与微信实名一致")


# ============ 内部：发放奖励 ============


class GrantRewardIn(BaseModel):
    user_id: str = Field(..., description="users.id")
    openid: str = Field("", description="收款人 openid，留空则回退用户绑定的 wechat_openid")
    amount: int = Field(..., ge=1, description="奖励金额，单位：分")
    scene_name: str = Field(..., description="报备信息①活动名称")
    reward_desc: str = Field(..., description="报备信息②奖励说明")
    remark: str = Field("", max_length=32, description="转账备注，≤32 字符")
    source_type: str = Field("", description="来源类型，如 sign_in / invite")
    source_id: Optional[str] = Field(None, description="来源业务 ID，防重复发放")
    expire_days: Optional[int] = Field(None, ge=1, description="有效期天数，留空用全局配置")


class GrantRewardOut(BaseModel):
    id: str
    user_id: str
    amount: int
    status: str
    expire_at: Optional[datetime] = None
    duplicated: bool = Field(False, description="True 表示同 (source_type, source_id) 已发放过，返回既有记录")


# ============ 管理端：奖励 / 转账单运维（后台管理-其他-奖励管理）============


class AdminRewardOut(BaseModel):
    """管理端奖励记录。"""

    id: str
    user_id: str
    openid: str = ""
    amount: int = Field(..., description="奖励金额，单位：分")
    scene_name: str = ""
    reward_desc: str = ""
    remark: str = ""
    source_type: str = ""
    source_id: Optional[str] = None
    role: Optional[str] = None
    status: str = ""
    audit_by: Optional[str] = None
    audit_at: Optional[datetime] = None
    audit_remark: Optional[str] = None
    bill_state: Optional[str] = None
    out_bill_no: Optional[str] = None
    expire_at: Optional[datetime] = None
    withdrawn_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminRewardListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AdminRewardOut]


class AdminBillOut(BaseModel):
    """管理端转账单。"""

    id: str
    reward_id: str
    out_bill_no: str
    transfer_bill_no: Optional[str] = None
    appid: Optional[str] = None
    mchid: Optional[str] = None
    openid: Optional[str] = None
    amount: Optional[int] = Field(None, description="转账金额，单位：分")
    state: Optional[str] = None
    is_terminal: bool = False
    fail_reason: Optional[str] = None
    wx_create_time: Optional[str] = None
    wx_update_time: Optional[str] = None
    last_query_at: Optional[datetime] = None
    query_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 关联字段：列表里直接可见，免去前端二次请求
    reward_status: Optional[str] = None
    reward_amount: Optional[int] = None
    user_id: Optional[str] = None

    class Config:
        from_attributes = True


class AdminBillListOut(BaseModel):
    total: int
    page: int
    size: int
    items: list[AdminBillOut]


class AdminBillStatsOut(BaseModel):
    """转账单状态分布（页面顶部概览）。"""

    total: int = 0
    open_count: int = Field(0, description="非终态笔数")
    terminal_count: int = Field(0, description="终态笔数")
    by_state: dict = Field(default_factory=dict, description="微信侧状态 → 笔数")


class AdminBillActionOut(BaseModel):
    """转账单操作（查单刷新 / 撤销）结果。

    deleted=True 表示微信侧查无此单、占位记录已被清理（见
    WithdrawService._refresh_bill），此时 bill 为 None，前端应刷新列表而非更新行。
    """

    ok: bool = True
    message: str = ""
    deleted: bool = False
    bill: Optional[AdminBillOut] = None


class AdminUserOptionOut(BaseModel):
    """转账测试「收款用户」下拉候选（读 users 表）。

    openid 是可收款 openid：优先 users.wechat_openid；微信注册用户 users.id 本身
    就是 openid（见 wechat/services/auth_service.register_wechat_user），回退到 id。
    两者都没有 → openid 为空串，前端需提示不可直接转账（测试环境可手填任意值）。
    """

    id: str
    username: str = ""
    name: Optional[str] = None
    wechat_openid: Optional[str] = None
    openid: str = ""
    openid_source: str = Field(
        "", description="bound=绑定列；self=微信注册用户 users.id 即 openid；空=解析不到"
    )


class AdminUserOptionListOut(BaseModel):
    total: int = 0
    truncated: bool = Field(False, description="命中数超过本次返回条数时需用 keyword 缩小范围")
    items: list[AdminUserOptionOut]


# ============ 内部：Go 服务推送的回调 ============


class TransferNotifyIn(BaseModel):
    """Go 服务验签解密后推送的商家转账回调内容。"""

    notify_id: str
    event_type: str = ""
    notify_create_time: str = ""
    original_type: str = ""
    mch_id: str = ""
    appid: str = ""
    out_bill_no: str
    transfer_bill_no: str = ""
    openid: str = ""
    state: str
    transfer_amount: int = 0
    transfer_remark: str = ""
    fail_reason: str = ""
    create_time: str = ""
    update_time: str = ""


class NotifyAckOut(BaseModel):
    code: str = "OK"
    message: str = ""
    duplicated: bool = False


# ============ 管理端：审核 / 规则配置（红包激励机制）============


class BatchAuditIn(BaseModel):
    """批量审核请求（通过或驳回共用）。"""
    ids: list[str] = Field(..., min_length=1, description="奖励记录 ID 列表")
    audit_remark: str = Field("", max_length=255, description="审核备注（驳回必填）")


class BatchAuditOut(BaseModel):
    """批量审核结果。"""
    ok_count: int = Field(0, description="成功处理条数")
    skipped_ids: list[str] = Field(default_factory=list, description="跳过的 ID（非 pending_audit 状态）")
    message: str = ""


class RewardConfigOut(BaseModel):
    """规则配置出参。"""
    config_key: str
    enabled: bool
    config: dict
    updated_by: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RewardConfigUpdateIn(BaseModel):
    """规则配置更新入参。enabled 和 config 至少传一个。"""
    enabled: Optional[bool] = None
    config: Optional[dict] = None


class RewardAdminStatsOut(BaseModel):
    """管理端奖励状态分布（页面顶部概览）。"""
    pending_audit_count: int = 0
    pending_audit_amount: int = Field(0, description="待审核总额，单位：分")
    rejected_count: int = 0
    pending_count: int = 0
    withdrawing_count: int = 0
    success_count: int = 0
    expired_count: int = 0
