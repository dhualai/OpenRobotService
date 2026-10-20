"""微信支付 ORM 模型（向后兼容入口）。

⚠️ 表定义已迁至 `app/models/wxpay.py`。原因：全项目 19 个模型文件均位于 `app/models/`，
   `app.modules.wxpay` 是唯一例外，导致 Alembic autogenerate 看不到这三张表
   （`alembic/env.py` 只导入 `app.models`）。

本模块仅做重导出，保证既有 `from app.modules.wxpay.models import ...` 不受影响；
新增/修改表结构请直接改 `app/models/wxpay.py`。
"""

from app.models.wxpay import (  # noqa: F401
    RewardConfig,
    RewardRecord,
    RewardStatus,
    TransferBill,
    TransferBillState,
    WxpayNotifyLog,
    derive_reward_status,
)

__all__ = [
    "RewardConfig",
    "RewardRecord",
    "RewardStatus",
    "TransferBill",
    "TransferBillState",
    "WxpayNotifyLog",
    "derive_reward_status",
]
