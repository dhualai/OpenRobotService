// 我要摇人页的「新人红包」弹窗编排（本层只测编排，不测弹窗内部渲染）：
//   进页面 → 查后端口径 → 可领才弹（同一浏览器只提示一次）→ 领取成功后跳「我的奖励」。
//
// 打桩边界：
//   - 网络：@/api/ai、@/api/wxpay 打桩，不触网
//   - 弹窗：@/shared/components/NewcomerBonusDialog 打桩成 props 探针，
//     只验「能不能领的判定结果 + 传给弹窗的内容 + 领取回调接到哪」
import { type ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import type { NewcomerBonusDialogProps } from '@/shared/components/NewcomerBonusDialog';

const { mockToast, mockFetchNewcomerBonusStatus, mockClaimNewcomerBonus } = vi.hoisted(() => ({
  mockToast: vi.fn(),
  mockFetchNewcomerBonusStatus: vi.fn(),
  mockClaimNewcomerBonus: vi.fn(),
}));

/** 弹窗探针：记下编排层交来的 props；可见时留一个可点的「领取」锚点 */
let bonusProps: NewcomerBonusDialogProps | null = null;

vi.mock('@/api/ai', () => ({
  qaListTickets: vi.fn().mockResolvedValue({ data: { active_total: 0 } }),
}));

vi.mock('@/api/wxpay', () => ({
  fetchNewcomerBonusStatus: () => mockFetchNewcomerBonusStatus(),
  claimNewcomerBonus: () => mockClaimNewcomerBonus(),
}));

vi.mock('@/shared/components/NewcomerBonusDialog', () => ({
  default: (props: NewcomerBonusDialogProps) => {
    bonusProps = props;
    return props.visible ? (
      <div data-testid="bonus-dialog">
        <button type="button" data-testid="bonus-claim" onClick={() => props.onClaimed('reward-1')}>领取</button>
      </div>
    ) : null;
  },
}));

vi.mock('@/shared/components/ChatPanel', () => ({ default: () => <div data-testid="chat-panel" /> }));
// 扫码确认弹窗与本用例无关：轻量替身，避免它引 tdesign Popup 影响收集
vi.mock('@/shared/components/VehicleConfirmDialog', () => ({ default: () => null }));
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <div>{title}</div>,
  Toast: mockToast,
}));
vi.mock('@/shared/components/ConversationDrawer', () => ({ default: () => null }));
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/SubscriptionReminder', () => ({ default: () => null }));
vi.mock('@/shared/components/ActivityCenterDrawer', () => ({ default: () => null }));
vi.mock('@/shared/components/AnnouncementBar', () => ({ default: () => null }));

import CallView from '../call/CallView';

/** 外层探针：暴露当前路径，便于断言「领取后跳到我的奖励」 */
function Harness() {
  const location = useLocation();
  return (
    <>
      <div data-testid="pathname">{location.pathname}</div>
      <CallView />
    </>
  );
}

const eligibleStatus = {
  eligible: true,
  claimed: false,
  amount_fen: 50,
  title: '新人福利',
  desc: '新关注摇人吧，0.5 元现金红包可领。',
  reason: '',
  campaign_code: 'ors1086',
  reward_id: '',
};

describe('我要摇人 · 新人红包弹窗（ORS-1086）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    bonusProps = null;
    window.localStorage.clear();
  });

  it('可领时弹窗，并把金额与说明交给弹窗', async () => {
    mockFetchNewcomerBonusStatus.mockResolvedValue(eligibleStatus);
    render(<MemoryRouter><Harness /></MemoryRouter>);

    await waitFor(() => expect(screen.getByTestId('bonus-dialog')).toBeTruthy());
    expect(bonusProps?.visible).toBe(true);
    expect(bonusProps?.status?.amount_fen).toBe(50);
    expect(bonusProps?.status?.title).toBe('新人福利');
  });

  it('领取成功后跳到我的奖励页', async () => {
    mockFetchNewcomerBonusStatus.mockResolvedValue(eligibleStatus);
    mockClaimNewcomerBonus.mockResolvedValue({ ok: true, claimed: true, created: true, reward_id: 'reward-1' });
    render(<MemoryRouter><Harness /></MemoryRouter>);

    await waitFor(() => expect(screen.getByTestId('bonus-dialog')).toBeTruthy());
    screen.getByTestId('bonus-claim').click();

    await waitFor(() => expect(screen.getByTestId('pathname').textContent).toBe('/rewards'));
  });

  it('不可领时不弹窗', async () => {
    mockFetchNewcomerBonusStatus.mockResolvedValue({
      ...eligibleStatus, eligible: false, reason: '非活动期新关注用户',
    });
    render(<MemoryRouter><Harness /></MemoryRouter>);

    await waitFor(() => expect(mockFetchNewcomerBonusStatus).toHaveBeenCalled());
    expect(screen.queryByTestId('bonus-dialog')).toBeNull();
  });

  it('同一浏览器只提示一次（已提示过就不再打扰）', async () => {
    window.localStorage.setItem('ors1086_newcomer_bonus_prompted', '1');
    mockFetchNewcomerBonusStatus.mockResolvedValue(eligibleStatus);
    render(<MemoryRouter><Harness /></MemoryRouter>);

    await waitFor(() => expect(mockFetchNewcomerBonusStatus).not.toHaveBeenCalled());
    expect(screen.queryByTestId('bonus-dialog')).toBeNull();
  });
});
