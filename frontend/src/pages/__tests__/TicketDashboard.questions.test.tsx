import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/dashboard', () => ({
  fetchDashboardSummaryAll: vi.fn().mockResolvedValue({}),
  // AI 直答率区块（1016）：无数据时区块整体隐藏，权限用例不关心它
  fetchDarLatest: vi.fn().mockResolvedValue(null),
  // 提单人公司/部门分布（#320）：返回空结构，权限用例不关心该区块
  fetchTicketCreatorOrgAnalysis: vi.fn().mockResolvedValue({ dimension: 'company', companies: [], by_org: [] }),
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

  it('默认展示全部直答率（带分子分母），点某一周切到该周，其余指标不展示', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce({
      week_date: '20261009', env: 'prod',
      dar_rates: {}, kb_gap: '30.5%（说明）', manual_progress: '541/526 段已标',
      // 同分母原文自带分子分母；weekly_series 是最近 8 个自然周（时间正序）
      dar_rates_same_base: { 'L2_人工': '28.0%（298/1064）' },
      weekly_series: [
        { from: '09-28', to: '10-04', base: '1014 段', 'L2_人工': '28.7%（291/1014）' },
        { from: '10-05', to: '10-11', base: '1064 段', 'L2_人工': '28.0%（298/1064）' },
      ],
      wow: {
        'L2_人工': { prev: 28.7, curr: 28.0, delta: -0.7 },
        'L3_AI同段': { prev: 35.2, curr: 33.2, delta: -2.0 },
      },
      this_week: { n_convs: 19, n_segs: 25 },
      updated_at: null,
    });
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.getByText('AI 直答率')).toBeInTheDocument();
    // 区间选择：全部 + 每一周
    expect(screen.getByText('全部')).toBeInTheDocument();
    expect(screen.getByText('09-28')).toBeInTheDocument();
    expect(screen.getByText('10-05')).toBeInTheDocument();
    // 周选项按时间从近到远：全部 → 10-05（近）→ 09-28（远）
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(
      ['全部', '10-05', '09-28'],
    );
    // 默认选中「全部」：大数字 + 分子分母
    expect(screen.getByText('28.0%')).toBeInTheDocument();
    expect(screen.getByText('298 / 1064 段 · 全部累计')).toBeInTheDocument();
    // 点上周（09-28~10-04）→ 大数字换成那一周的口径
    fireEvent.click(screen.getByText('09-28'));
    expect(screen.getByText('28.7%')).toBeInTheDocument();
    expect(screen.getByText('291 / 1014 段 · 09-28~10-04')).toBeInTheDocument();
    // 领导不关注的指标一律不再展示
    expect(screen.queryByText('L1 机器信号')).not.toBeInTheDocument();
    expect(screen.queryByText('L3 AI 预标')).not.toBeInTheDocument();
    expect(screen.queryByText('KB 缺口率')).not.toBeInTheDocument();
    expect(screen.queryByText(/人工标注进度/)).not.toBeInTheDocument();
  });

  it('没有 weekly_series 的老周报只展示全部，不渲染周选择与趋势', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce({
      week_date: '20261009', env: 'prod',
      dar_rates: { 'L2_人工': '端到端 29.2%（115/394）｜确定 33.0%｜已标 394 段' },
      kb_gap: null, manual_progress: null,
      wow: {}, this_week: {}, updated_at: null,
    });
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.getByText('29.2%')).toBeInTheDocument();
    expect(screen.getByText('115 / 394 段 · 全部累计')).toBeInTheDocument();
    expect(screen.queryByText('全部')).not.toBeInTheDocument();
  });

  it('无周报数据时整块隐藏（不占位）', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce(null);
    await act(async () => { render(<MemoryRouter><TicketDashboard /></MemoryRouter>); });
    expect(screen.queryByText('AI 直答率')).not.toBeInTheDocument();
  });
});
