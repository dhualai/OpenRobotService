"""wxpay 模块（微信支付 · 商家转账）。

业务形态：**奖励记录 + 立即提现**（无卡包 / 无余额账户）。
用户侧唯一入口是「我的历史奖励」；奖励先记账，用户点「立即提现」时才创建转账单。

职责划分：
- 签名 / 验签 / 加解密 → 独立 Go 微服务 `wxpay-service`（密钥只落在 Go 侧）
- 持久化与业务状态机 → 本模块（MySQL + SQLAlchemy）

详见 `docs/微信支付接入技术方案.md`。

路由：
- `wxpay_router`    用户端 + 管理端，注册在 `/api` 前缀下（JWT 登录态）
- `internal_router` 内部接口，**无 `/api` 前缀**，供 Go 服务推送回调
"""

from fastapi import APIRouter

from app.modules.wxpay.api.admin import router as admin_router
from app.modules.wxpay.api.internal import router as internal_router
from app.modules.wxpay.api.rewards import router as rewards_router

# 用户端：/api/wxpay/rewards/*
wxpay_router = APIRouter(prefix="/wxpay", tags=["wxpay"])
wxpay_router.include_router(rewards_router)
# 管理端：/api/wxpay/admin/*（后台管理 → 其他 → 奖励管理）
wxpay_router.include_router(admin_router)

__all__ = ["wxpay_router", "internal_router"]
