from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session

from app.models.wxpay import RewardRecord, RewardStatus
from app.modules.wxpay.service import RewardService


class TestRewardStats(IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine('sqlite:///:memory:')
        metadata = MetaData()
        RewardRecord.__table__.to_metadata(metadata)
        metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.db = SimpleNamespace(execute=AsyncMock(side_effect=self.session.execute))
        self.pending_amounts = [101 + index for index in range(25)]
        for index, amount in enumerate(self.pending_amounts):
            self.add_reward(f'pending-{index}', RewardStatus.PENDING, amount)
        self.withdrawing_amounts = [1100 + index for index in range(25)]
        for index, amount in enumerate(self.withdrawing_amounts):
            self.add_reward(f'withdrawing-{index}', RewardStatus.WITHDRAWING, amount)
        for status in [RewardStatus.PENDING_AUDIT, RewardStatus.REJECTED, RewardStatus.EXPIRED]:
            self.add_reward(status, status, 5000)
        self.add_reward('success', RewardStatus.SUCCESS, 7000)
        self.add_reward('other-pending', RewardStatus.PENDING, 99000, 'other-user')
        self.add_reward('other-withdrawing', RewardStatus.WITHDRAWING, 77000, 'other-user')
        self.add_reward('other-success', RewardStatus.SUCCESS, 88000, 'other-user')
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def add_reward(self, reward_id, status, amount, user_id='current-user'):
        self.session.add(RewardRecord(
            id=reward_id, user_id=user_id, openid=user_id, amount=amount,
            scene_name='测试活动', reward_desc='测试奖励', status=status,
        ))

    async def test_pending_total_matches_all_detail_pages_and_only_current_user(self):
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.pending_amount, sum(self.pending_amounts))
        self.assertEqual(stats.pending_count, 25)
        self.assertEqual(stats.withdrawn_amount, 7000)
        items = []
        for page in [1, 2]:
            rows, total = await RewardService.list_rewards(self.db, 'current-user', RewardStatus.PENDING, page, 20)
            self.assertEqual(total, stats.pending_count)
            items.extend(rows)
        self.assertEqual(len(items), 25)
        self.assertEqual(sum(reward.amount for reward in items), stats.pending_amount)

    async def test_withdrawal_changes_pending_total_without_counting_non_pending_rewards(self):
        reward = self.session.get(RewardRecord, 'pending-0')
        reward.status = RewardStatus.WITHDRAWING
        self.session.commit()
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.pending_amount, sum(self.pending_amounts[1:]))
        self.assertEqual(stats.pending_count, 24)
        self.assertEqual(stats.withdrawn_amount, 7000)
        reward.status = RewardStatus.SUCCESS
        self.session.commit()
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.pending_amount, sum(self.pending_amounts[1:]))
        self.assertEqual(stats.withdrawn_amount, 7000 + self.pending_amounts[0])

    async def test_user_without_rewards_has_zero_totals(self):
        stats = await RewardService.stats(self.db, 'no-rewards')
        self.assertEqual(stats.pending_amount, 0)
        self.assertEqual(stats.pending_count, 0)
        self.assertEqual(stats.withdrawn_amount, 0)
        self.assertEqual(stats.withdrawing_amount, 0)
        self.assertEqual(stats.withdrawing_count, 0)

    async def test_withdrawing_total_matches_all_detail_pages_and_only_current_user(self):
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.withdrawing_amount, sum(self.withdrawing_amounts))
        self.assertEqual(stats.withdrawing_count, 25)
        items = []
        for page in [1, 2]:
            rows, total = await RewardService.list_rewards(self.db, 'current-user', RewardStatus.WITHDRAWING, page, 20)
            self.assertEqual(total, stats.withdrawing_count)
            items.extend(rows)
        self.assertEqual(len(items), 25)
        self.assertEqual(sum(reward.amount for reward in items), stats.withdrawing_amount)

    async def test_withdrawing_total_tracks_confirmation_and_return_to_pending(self):
        reward = self.session.get(RewardRecord, 'withdrawing-0')
        reward.status = RewardStatus.SUCCESS
        self.session.commit()
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.withdrawing_amount, sum(self.withdrawing_amounts[1:]))
        self.assertEqual(stats.withdrawing_count, 24)
        self.assertEqual(stats.withdrawn_amount, 7000 + self.withdrawing_amounts[0])
        reward = self.session.get(RewardRecord, 'withdrawing-1')
        reward.status = RewardStatus.PENDING
        self.session.commit()
        stats = await RewardService.stats(self.db, 'current-user')
        self.assertEqual(stats.withdrawing_amount, sum(self.withdrawing_amounts[2:]))
        self.assertEqual(stats.withdrawing_count, 23)
        self.assertEqual(stats.pending_amount, sum(self.pending_amounts) + self.withdrawing_amounts[1])
