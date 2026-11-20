"""微信支付 ORM 模型：奖励记录 + 转账单 + 回调流水。

对应技术方案 `docs/微信支付接入技术方案.md` 第 7 章。

⚠️ 本文件是全项目 ORM 模型的唯一归属目录 `app/models/` 的一部分。
   `app/modules/wxpay/models.py` 仅作向后兼容的重导出，请勿在此之外新增表定义，
   否则 Alembic autogenerate 会看不到（详见 `alembic/env.py` 顶部说明）。

核心设计（方案 4.2）：
- `RewardRecord` 是**商户侧记账**，用户「我的历史奖励」的唯一数据源；
  发放奖励时**不创建转账单**，只有用户点「立即提现」才建单。
- `TransferBill` 是**微信侧单据镜像**；一笔奖励在重试场景下可对应**多笔**转账单，
  但同一时刻**至多一笔非终态**（这是防重复出资的关键约束）。
- 两张表的状态取值域不同，`RewardRecord.status` 由「当前有效转账单」推导，
  禁止把微信 `state` 直接写进奖励表。
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.models.base import Base


class RewardStatus:
    """奖励记录状态（商户侧，与微信转账单状态取值域不同）。

    审核决策引擎分流：
    - pending_audit  强制财务审核（默认态，需人工操作）
    - rejected       财务驳回（终态，不发钱）
    - pending        审核已通过，可提现（自动通过或人工通过后进入，可直接调 withdraw）
    - withdrawing    提现中
    - success        已到账（终态）
    - expired        已失效（终态，超期未提现）
    """

    PENDING_AUDIT = "pending_audit"  # 待财务审核
    REJECTED = "rejected"             # 已驳回（终态）
    PENDING = "pending"               # 审核通过，可提现
    WITHDRAWING = "withdrawing"       # 提现中
    SUCCESS = "success"               # 已到账（终态）
    EXPIRED = "expired"               # 已失效（终态）

    # 不可提现状态（WithdrawService 前置校验用）
    NON_WITHDRAWABLE = frozenset({PENDING_AUDIT, REJECTED})


class TransferBillState:
    """微信侧转账单状态。"""

    ACCEPTED = "ACCEPTED"
    PROCESSING = "PROCESSING"
    WAIT_USER_CONFIRM = "WAIT_USER_CONFIRM"
    TRANSFERING = "TRANSFERING"
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    CANCELING = "CANCELING"
    CANCELLED = "CANCELLED"

    # 终态只有三个
    TERMINAL = frozenset({SUCCESS, FAIL, CANCELLED})

    @classmethod
    def is_terminal(cls, state) -> bool:
        """是否为终态。"""
        return (state or "") in cls.TERMINAL

    @classmethod
    def can_reuse_package(cls, state) -> bool:
        """package_info 是否仍可用于调起用户确认收款页。

        仅 WAIT_USER_CONFIRM 可复用。用户取消确认页后再次点击应走「去确认」分支，
        复用原 package_info，而不是创建新转账单（否则重复出资）。
        """
        return (state or "") == cls.WAIT_USER_CONFIRM


def derive_reward_status(bill_state) -> str:
    """由转账单状态推导奖励状态（方案 4.2 映射规则）。"""
    state = (bill_state or "").upper()
    if state == TransferBillState.SUCCESS:
        return RewardStatus.SUCCESS
    if TransferBillState.is_terminal(state):
        # FAIL / CANCELLED：允许换单重新提现
        return RewardStatus.PENDING
    return RewardStatus.WITHDRAWING


class RewardRecord(Base):
    """奖励记录（用户「我的历史奖励」数据源）。"""

    __tablename__ = "reward_record"

    id = Column(String(64), primary_key=True, comment="奖励记录 ID")
    user_id = Column(String(64), nullable=False, index=True, comment="关联 users.id")
    # 冗余 openid：发起转账时直接取用，避免每次联表
    openid = Column(String(128), nullable=False, index=True, comment="收款人 openid")

    amount = Column(Integer, nullable=False, comment="奖励金额，单位：分")

    # 报备信息（现金营销场景固定两条，缺一即报 PARAM_ERROR）
    scene_name = Column(String(128), nullable=False, comment="报备信息①活动名称")
    reward_desc = Column(String(128), nullable=False, comment="报备信息②奖励说明")
    remark = Column(String(64), nullable=True, comment="transfer_remark，用户可见，≤32 字符")

    source_type = Column(String(32), nullable=False, default="", comment="奖励来源类型")
    source_id = Column(String(64), nullable=True, comment="来源业务 ID，防重复发放")
    role = Column(
        String(32),
        nullable=True,
        comment="奖励角色: reporter/assignee/reviewer/liker/ranker/manual",
    )

    status = Column(
        String(20),
        nullable=False,
        default=RewardStatus.PENDING_AUDIT,
        index=True,
        comment="pending_audit/rejected/pending/withdrawing/success/expired",
    )
    expire_at = Column(DateTime, nullable=True, comment="奖励有效期，超期未提现 → expired")
    withdrawn_at = Column(DateTime, nullable=True, comment="到账时间")

    # 审核字段：自动通过时 audit_by="AUTO"，audit_at 写入当前时间
    audit_by = Column(String(64), nullable=True, comment="审核人 users.id（自动通过时为 AUTO）")
    audit_at = Column(DateTime, nullable=True, comment="审核时间")
    audit_remark = Column(String(255), nullable=True, comment="审核备注（驳回原因或风控标记）")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间（UTC）")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间（UTC）"
    )

    __table_args__ = (
        Index("ix_reward_user_status", "user_id", "status"),
        Index("ix_reward_audit_status", "status"),
        Index("ix_reward_task_role", "source_id", "role"),
        Index("ix_reward_user_created", "user_id", "created_at"),
        # 防重复发放；role 参与唯一约束，同一工单不同角色各发一次
        UniqueConstraint(
            "source_type", "source_id", "role", name="uq_reward_source_role"
        ),
    )


class TransferBill(Base):
    """商家转账单（微信侧单据镜像）。

    一笔 reward 可关联多笔 bill（重试会换 out_bill_no）；
    同一时刻至多一笔非终态，由 reward_id 行锁 + 业务层保证。
    """

    __tablename__ = "transfer_bill"

    id = Column(String(64), primary_key=True, comment="转账单 ID")
    reward_id = Column(String(64), nullable=False, index=True, comment="关联 reward_record.id")
    out_bill_no = Column(String(64), nullable=False, unique=True, comment="商户单号，仅数字+字母")
    transfer_bill_no = Column(String(64), nullable=True, index=True, comment="微信转账单号")

    appid = Column(String(32), nullable=True)
    mchid = Column(String(32), nullable=True)
    openid = Column(String(128), nullable=True)
    amount = Column(Integer, nullable=True, comment="转账金额，单位：分")

    state = Column(String(32), nullable=True, index=True, comment="微信侧状态，见 TransferBillState")
    package_info = Column(Text, nullable=True, comment="调起确认页用，仅 WAIT_USER_CONFIRM 有效")
    package_expire_at = Column(DateTime, nullable=True, comment="package_info 失效时间（创建+24h）")
    fail_reason = Column(String(255), nullable=True, comment="失败原因")

    is_terminal = Column(
        Boolean, nullable=False, default=False, index=True, comment="是否终态，查单与决策过滤用"
    )

    # 兜底查单控制
    last_query_at = Column(DateTime, nullable=True)
    query_count = Column(Integer, nullable=False, default=0)

    # 微信返回时间（字符串，原样保存）
    wx_create_time = Column(String(64), nullable=True)
    wx_update_time = Column(String(64), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间（UTC）")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间（UTC）"
    )

    __table_args__ = (Index("ix_transfer_bill_reward_terminal", "reward_id", "is_terminal"),)


class WxpayNotifyLog(Base):
    """微信支付回调流水（幂等去重 + 排障留痕）。

    `notify_id` 唯一索引是回调幂等的最终保证（Go 侧内存去重只是第一道防线）。
    """

    __tablename__ = "wxpay_notify_log"

    id = Column(String(64), primary_key=True)
    notify_id = Column(String(64), nullable=False, unique=True, comment="通知 ID（EV-...），天然去重")
    event_type = Column(String(32), nullable=True, comment="如 MCHTRANSFER.BILL.FINISHED")
    resource_type = Column(String(32), nullable=True, comment="固定 encrypt-resource")
    biz_no = Column(String(64), nullable=True, index=True, comment="out_bill_no")
    raw_body = Column(Text, nullable=True, comment="原始报文（注意脱敏）")
    decrypted = Column(JSON, nullable=True, comment="解密后内容")
    process_result = Column(String(16), nullable=True, comment="DONE / SKIPPED")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间（UTC）")
    processed_at = Column(DateTime, nullable=True, comment="处理完成时间（UTC）")


class RewardConfig(Base):
    """奖励规则配置（JSON 灵活结构，每行一种规则）。

    预设四类：
    - task_archive   基础闭环奖励金额（reporter/assignee/reviewer 各档分值）
    - monthly_rank    月度排名阶梯（top3 / top4-10 分值）
    - quality_like    优质点赞（per_like_fen + daily_limit）
    - audit_policy    审核决策引擎阈值（自动通过条件 + 强制审核条件）
    """

    __tablename__ = "reward_config"

    config_key = Column(
        String(64), primary_key=True, comment="配置键，如 task_archive / audit_policy"
    )
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    # ⚠️ 用 Text 而非 JSON：asyncmy 在 MySQL 上对 JSON dict 内嵌 list 的 escape_dict 有 bug，
    # 报 TypeError: Argument 'val' has incorrect type (expected tuple, got list)。
    # 读写时手动 json.dumps/loads。MySQL JSON 列对字符串输入自动兼容，无需 DDL 迁移。
    config = Column(Text, nullable=False, default="{}", comment="规则 JSON 字符串，结构由 config_key 决定")
    updated_by = Column(String(64), nullable=True, comment="最近修改人 users.id")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间（UTC）"
    )
