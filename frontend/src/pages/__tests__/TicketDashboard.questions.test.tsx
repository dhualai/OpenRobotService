import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/dashboard', () => ({
  fetchDashboardSummaryAll: vi.fn().mockResolvedValue({}),
  // AI 直答率区块（1016）：无数据时区块整体隐藏，权限用例不关心它
  fetchDarLatest: vi.fn().mockResolvedValue(null),
}));
vi.mock('@/shared/components/macaronBits', () => ({ MacDonut: () => null, MacLegend: () => null, macTone: () => '#000' }));
vi.mock('tdesign-mobile-react', () => ({ Navbar: () => null }));
vi.mock('../admin/QuestionModuleCard', () => ({ default: () => <div>问题模块</div> }));

import TicketDashboard from '../admin/TicketDashboard';
import { useAuthStore } from '@/stores/auth';

describe('工单看板问题模块权限', () => {
  beforeEach(() => useAuthStore.setState({ username: 'question-test', projectIds: ['P1'], permissions: [] }));

  it('仅有关联项目和其他统计权限时不展示全量问题', async () => {
    useAuthStore.setState({ permissions: ['frontend:admin:other:show'] });
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.queryByText('问题模块')).not.toBeInTheDocument();
  });

  it.each([['backend:project:all'], ['admin']])('全量查看权限或管理员可见：%s', async (...permissions) => {
    useAuthStore.setState({ permissions });
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.getByText('问题模块')).toBeInTheDocument();
  });
});

describe('AI 直答率区块（ORS-#981）', () => {
  beforeEach(() => useAuthStore.setState({
    username: 'dar-test', projectIds: [], permissions: ['frontend:admin:other:show'],
  }));

  it('有周报数据时渲染主口径大数字与环比', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce({
      week_date: '20261009', env: 'prod',
      dar_rates: {}, kb_gap: '30.5%（说明）', manual_progress: '541/526 段已标',
      wow: {
        'L2_人工': { prev: 28.7, curr: 28.0, delta: -0.7 },
        'L3_AI同段': { prev: 35.2, curr: 33.2, delta: -2.0 },
      },
      this_week: { n_convs: 19, n_segs: 25 },
      updated_at: null,
    });
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.getByText('AI 直答率')).toBeInTheDocument();
    expect(screen.getByText('28.0%')).toBeInTheDocument();
    expect(screen.getByText('↓ 0.7pp')).toBeInTheDocument();
    expect(screen.getByText('2026-10-09 周报')).toBeInTheDocument();
  });

  it('无周报数据时整块隐藏（不占位）', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce(null);
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.queryByText('AI 直答率')).not.toBeInTheDocument();
  });
});
