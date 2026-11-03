"""新人红包（ORS-1086）判据与幂等测试。

口径：新用户 = 首次关注公众号时间 >= 活动开始时间（UTC）；
每人限领一次（reward_record 唯一约束 source_type + source_id + role）。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.models.identity import UserDB
from app.models.user_info import UserInfo
from app.models.wechat_activity import WechatUserActivity
from app.models.wxpay import RewardConfig, RewardRecord, RewardStatus
from app.modules.wxpay import newcomer_bonus
# ⚠️ `app.wechat.services.wechat_service` 是**模块**，其下的 `wechat_service` 才是服务实例；
#    而 `from app.wechat.services import wechat_service` 拿到的是实例（__init__ 用同名覆盖了子模块属性）。
from app.wechat.services.wechat_service import wechat_service as wechat_service_instance

START_AT = datetime(2026, 11, 3, 4, 0, 0, tzinfo=timezone.utc)   # 11-03 12:00 北京时间


def wechat_user(openid: str = "o_newcomer") -> UserDB:
    """微信登录用户：users.id 即 openid。"""
    return UserDB(id=openid, username=f"wechat_{openid}", password_hash="x", status="active")


def staff_user() -> UserDB:
    """业务账号：没有微信身份，不能领。"""
    return UserDB(id="user_admin", username="admin", password_hash="x", status="active")


class TestNewcomerBonus(IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        metadata = MetaData()
        for model in (RewardRecord, RewardConfig, UserInfo, WechatUserActivity):
            model.__table__.to_metadata(metadata)
        metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.db = SimpleNamespace(
            execute=AsyncMock(side_effect=self.session.execute),
            add=self.session.add,
            flush=AsyncMock(side_effect=self.session.flush),
            commit=AsyncMock(side_effect=self.session.commit),
            refresh=AsyncMock(),
            rollback=self.session.rollback,
        )
        self._wechat_patcher = None

    def tearDown(self):
        if self._wechat_patcher is not None:
            self._wechat_patcher.stop()
        self.session.close()
        self.engine.dispose()

    def stub_wechat_info(self, payload):
        """桩掉微信实时接口（返回 None 表示取不到，走兜底）。"""
        self._wechat_patcher = patch.object(
            wechat_service_instance, "get_user_info", AsyncMock(return_value=payload)
        )
        self._wechat_patcher.start()

    async def test_subscribe_before_start_is_not_eligible(self):
        self.stub_wechat_info({
            "subscribe": 1,
            "subscribe_time": int((START_AT - timedelta(days=1)).timestamp()),
        })
        state = await newcomer_bonus.evaluate(self.db, wechat_user())

        self.assertFalse(state["eligible"])
        self.assertIn("非活动期", state["reason"])

    async def test_subscribe_at_start_boundary_is_eligible(self):
        self.stub_wechat_info({"subscribe": 1, "subscribe_time": int(START_AT.timestamp())})
        state = await newcomer_bonus.evaluate(self.db, wechat_user())

        self.assertTrue(state["eligible"])
        self.assertEqual(state["amount_fen"], 50)
        self.assertEqual(state["reward_id"], "")

    async def test_unsubscribed_user_is_not_eligible(self):
        self.stub_wechat_info({"subscribe": 0})
        state = await newcomer_bonus.evaluate(self.db, wechat_user())

        self.assertFalse(state["eligible"])
        self.assertFalse(state["claimed"])

    async def test_staff_account_without_wechat_identity_is_not_eligible(self):
        self.stub_wechat_info({"subscribe": 1, "subscribe_time": int(START_AT.timestamp())})
        state = await newcomer_bonus.evaluate(self.db, staff_user())

        self.assertFalse(state["eligible"])
        self.assertIn("仅微信用户", state["reason"])

    async def test_missing_subscribe_time_is_not_eligible(self):
        self.stub_wechat_info(None)
        state = await newcomer_bonus.evaluate(self.db, wechat_user())

        self.assertFalse(state["eligible"])
        self.assertIn("未取到关注时间", state["reason"])

    async def test_claimed_user_sees_claimed_and_cannot_claim_again(self):
        user = wechat_user()
        self.session.add(RewardRecord(
            id="reward-1", user_id=user.id, openid=user.id, amount=50,
            scene_name="新人红包", reward_desc="新人福利 0.5 元",
            source_type=newcomer_bonus.SOURCE_TYPE, source_id=f"ors1086:{user.id}",
            role=newcomer_bonus.ROLE, status=RewardStatus.PENDING,
        ))
        self.session.commit()

        state = await newcomer_bonus.evaluate(self.db, user)
        self.assertTrue(state["claimed"])
        self.assertFalse(state["eligible"])
        self.assertEqual(state["reward_id"], "reward-1")

    async def test_claim_grants_once_and_is_idempotent(self):
        user = wechat_user()
        self.stub_wechat_info({"subscribe": 1, "subscribe_time": int(START_AT.timestamp())})

        # 微信支付总开关关着时不记账（记了也提不出来），这里显式打开走发放路径
        with patch.object(settings, "WXPAY_ENABLED", True):
            first = await newcomer_bonus.claim(self.db, user)
            self.assertTrue(first["ok"])
            self.assertTrue(first["created"])
            self.assertEqual(first["amount_fen"], 50)

            second = await newcomer_bonus.claim(self.db, user)
            self.assertTrue(second["ok"])
            self.assertFalse(second["created"])
            self.assertEqual(second["reward_id"], first["reward_id"])

        rows = self.session.execute(
            select(RewardRecord).where(RewardRecord.source_type == newcomer_bonus.SOURCE_TYPE)
        ).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].amount, 50)
        self.assertEqual(rows[0].status, RewardStatus.PENDING)

    async def test_ineligible_user_claim_does_not_create_reward(self):
        user = wechat_user()
        self.stub_wechat_info({
            "subscribe": 1,
            "subscribe_time": int((START_AT - timedelta(days=3)).timestamp()),
        })

        result = await newcomer_bonus.claim(self.db, user)
        self.assertFalse(result["ok"])

        rows = self.session.execute(
            select(RewardRecord).where(RewardRecord.source_type == newcomer_bonus.SOURCE_TYPE)
        ).scalars().all()
        self.assertEqual(len(rows), 0)

    async def test_claim_does_not_grant_when_wxpay_disabled(self):
        """微信支付总开关关着时不记账：记了账也提不出来（withdraw 会被同一开关拦下）。"""
        user = wechat_user()
        self.stub_wechat_info({"subscribe": 1, "subscribe_time": int(START_AT.timestamp())})

        with patch.object(settings, "WXPAY_ENABLED", False):
            result = await newcomer_bonus.claim(self.db, user)

        self.assertFalse(result["ok"])
        rows = self.session.execute(
            select(RewardRecord).where(RewardRecord.source_type == newcomer_bonus.SOURCE_TYPE)
        ).scalars().all()
        self.assertEqual(len(rows), 0)

    async def test_disabled_config_closes_the_campaign(self):
        self.session.add(RewardConfig(
            config_key=newcomer_bonus.CONFIG_KEY, enabled=False, config="{}",
        ))
        self.session.commit()
        self.stub_wechat_info({"subscribe": 1, "subscribe_time": int(START_AT.timestamp())})

        state = await newcomer_bonus.evaluate(self.db, wechat_user())
        self.assertFalse(state["eligible"])
        self.assertIn("未开启", state["reason"])

    async def test_default_amount_and_start_time_follow_agreed_spec(self):
        self.assertEqual(newcomer_bonus.DEFAULT_AMOUNT_FEN, 50)
        self.assertEqual(newcomer_bonus.DEFAULT_START_AT, "2026-11-03T04:00:00Z")
        # 0.5 元必须能被提现：默认最低提现额不得高于 50 分
        self.assertLessEqual(
            Settings.model_fields["WXPAY_MIN_WITHDRAW_FEN"].default,
            newcomer_bonus.DEFAULT_AMOUNT_FEN,
        )
