// 「我的历史奖励」页：列表渲染 / 提现三分支处理 / 状态筛选
//
// 重点守的是「不重复出资」相关的前端行为：
//   - confirm    必须把后端给的 mch_id/app_id/package 原样交给 requestMerchantTransfer
//   - processing 只提示、不重复建单（即不再调起确认页）
//   - success/expired 按钮禁用，点击不发请求
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';

const mockFetchRewards = vi.fn();
const mockFetchStats = vi.fn();
const mockWithdraw = vi.fn();
const mockTransfer = vi.fn();

vi.mock('@/api/wxpay', () => ({
  fetchRewards: (...args: unknown[]) => mockFetchRewards(...args),
  fetchRewardStats: (...args: unknown[]) => mockFetchStats(...args),
  withdrawReward: (...args: unknown[]) => mockWithdraw(...args),
  fenToYuan: (fen: number) => ((fen || 0) / 100).toFixed(2),
}));

vi.mock('@/shared/utils/merchantTransfer', () => ({
  requestMerchantTransfer: (...args: unknown[]) => mockTransfer(...args),
}));

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <nav data-testid="navbar">{title}</nav>,
  Loading: ({ text }: { text?: ReactNode }) => <div data-testid="loading">{text}</div>,
  Button: ({
    children,
    onClick,
    disabled,
    loading,
    'data-testid': testId,
  }: {
    children?: ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    loading?: boolean;
    'data-testid'?: string;
  }) => (
    <button data-testid={testId} onClick={onClick} disabled={disabled || loading}>
      {children}
    </button>
  ),
  Popup: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null,
  Input: ({
    value,
    onChange,
    placeholder,
  }: {
    value?: string;
    onChange?: (v: string) => void;
    placeholder?: string;
  }) => (
    <input
      data-testid="realname-input"
      placeholder={placeholder}
      value={value ?? ''}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
  Toast: vi.fn(),
}));

import MyRewards from '../rewards/MyRewards';

const REWARDS = [
  {
    id: 'r1',
    amount: 1250,
    scene_name: '邀新活动',
    reward_desc: '邀请好友注册奖励',
    status: 'pending' as const,
    expire_at: '2026-11-05T00:00:00Z',
  },
  {
    id: 'r2',
    amount: 3000,
    scene_name: '签到奖励',
    reward_desc: '连续签到 7 天',
    status: 'success' as const,
    withdrawn_at: '2026-10-01T00:00:00Z',
  },
];

const renderView = () => render(<MemoryRouter><MyRewards /></MemoryRouter>);

describe('MyRewards', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchRewards.mockResolvedValue({ total: 2, page: 1, size: 20, items: REWARDS });
    // 汇总金额刻意与列表金额不同，便于断言时区分「汇总卡」与「奖励卡」
    mockFetchStats.mockResolvedValue({ pending_amount: 5000, pending_count: 1, withdrawn_amount: 7000 });
  });

  it('渲染奖励列表金额与汇总（分 → 元）', async () => {
    renderView();
    await waitFor(() => expect(screen.getByText('邀新活动')).toBeInTheDocument());
    expect(screen.getByText('¥12.50')).toBeInTheDocument();
    expect(screen.getByText('¥30.00')).toBeInTheDocument();
    // 汇总卡：待提现 ¥50.00 + 累计已到账 ¥70.00
    expect(screen.getByText('¥50.00')).toBeInTheDocument();
    expect(screen.getByText('¥70.00')).toBeInTheDocument();
    expect(screen.getByText('1 笔可提')).toBeInTheDocument();
    expect(screen.getByText(/有效期至 2026-11-05/)).toBeInTheDocument();
  });

  it('confirm 分支：原样透传 mch_id/app_id/package 调起确认页，ok 后提示等待到账', async () => {
    mockWithdraw.mockResolvedValue({
      action: 'confirm',
      message: '请完成收款确认',
      mch_id: '1230000109',
      app_id: 'wxabc',
      package: 'mch_id=123&package_info=xxx',
      out_bill_no: 'B1',
      state: 'WAIT_USER_CONFIRM',
    });
    mockTransfer.mockResolvedValue({ result: 'ok', raw: 'requestMerchantTransfer:ok' });

    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-action-r1'));

    await waitFor(() =>
      expect(mockTransfer).toHaveBeenCalledWith({
        mchId: '1230000109',
        appId: 'wxabc',
        package: 'mch_id=123&package_info=xxx',
      }),
    );
    await waitFor(() =>
      expect(Toast).toHaveBeenCalledWith({ message: '已提交，到账后自动更新', theme: 'success' }),
    );
    // 提现后刷新列表（静默）：重新拉取奖励与汇总
    await waitFor(() => expect(mockFetchRewards.mock.calls.length).toBeGreaterThan(1));
  });

  it('processing 分支：只提示处理中，绝不重复调起确认页', async () => {
    mockWithdraw.mockResolvedValue({
      action: 'processing',
      message: '转账处理中，请稍后重试',
      mch_id: '', app_id: '', package: '', out_bill_no: 'B1', state: 'TRANSFERING',
    });

    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-action-r1'));

    await waitFor(() =>
      expect(Toast).toHaveBeenCalledWith({ message: '转账处理中，请稍后重试', theme: 'warning' }),
    );
    expect(mockTransfer).not.toHaveBeenCalled();
  });

  it('need_real_name 分支：弹出姓名输入框，填写后带 user_name 重新提现', async () => {
    mockWithdraw
      .mockResolvedValueOnce({
        action: 'need_real_name',
        message: '收款人姓名与微信实名信息不一致，请填写真实姓名',
      })
      .mockResolvedValueOnce({
        action: 'confirm',
        message: '请完成收款确认',
        mch_id: '1230000109',
        app_id: 'wxabc',
        package: 'mch_id=123&package_info=xxx',
        out_bill_no: 'B2',
        state: 'WAIT_USER_CONFIRM',
      });
    mockTransfer.mockResolvedValue({ result: 'ok', raw: 'requestMerchantTransfer:ok' });

    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-action-r1'));

    // 第一次不带姓名 -> 弹窗要求填写，且**不**调起确认页
    await waitFor(() => expect(screen.getByTestId('popup')).toBeInTheDocument());
    expect(mockWithdraw).toHaveBeenLastCalledWith('r1', undefined);
    expect(mockTransfer).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId('realname-input'), { target: { value: '张三' } });
    fireEvent.click(screen.getByText('提交并提现'));

    // 第二次带姓名重试 -> 拿到 package 后调起确认页
    await waitFor(() => expect(mockWithdraw).toHaveBeenLastCalledWith('r1', '张三'));
    await waitFor(() => expect(mockTransfer).toHaveBeenCalled());
  });

  it('姓名弹窗未填写时提交，提示且不发起提现', async () => {
    mockWithdraw.mockResolvedValue({
      action: 'need_real_name',
      message: '收款人姓名与微信实名信息不一致，请填写真实姓名',
    });

    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-action-r1'));
    await waitFor(() => expect(screen.getByTestId('popup')).toBeInTheDocument());

    const callsBefore = mockWithdraw.mock.calls.length;
    fireEvent.click(screen.getByText('提交并提现'));

    await waitFor(() =>
      expect(Toast).toHaveBeenCalledWith({ message: '请填写真实姓名', theme: 'warning' }),
    );
    expect(mockWithdraw.mock.calls.length).toBe(callsBefore);
  });

  it('已到账的奖励按钮禁用，点击不发起提现', async () => {
    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r2')).toBeInTheDocument());
    const btn = screen.getByTestId('reward-action-r2');
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent('已到账');
    fireEvent.click(btn);
    expect(mockWithdraw).not.toHaveBeenCalled();
  });

  it('切换状态筛选时按 status 重新拉取', async () => {
    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-tab-pending')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-tab-pending'));

    await waitFor(() =>
      expect(mockFetchRewards).toHaveBeenCalledWith(expect.objectContaining({ status: 'pending' })),
    );
  });

  it('提现接口失败时给出错误提示', async () => {
    mockWithdraw.mockRejectedValue(new Error('单笔金额超过上限，请联系客服'));
    renderView();
    await waitFor(() => expect(screen.getByTestId('reward-action-r1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('reward-action-r1'));

    await waitFor(() => {
      const calls = vi.mocked(Toast).mock.calls as { message: string }[][];
      expect(calls.some((c) => String(c[0]?.message).includes('单笔金额超过上限'))).toBe(true);
    });
  });
});
