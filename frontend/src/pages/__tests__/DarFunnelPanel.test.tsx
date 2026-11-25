import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/api/dashboard', () => ({
  fetchDarLatest: vi.fn(),
}));
vi.mock('tdesign-mobile-react', () => ({ Loading: () => <div>loading</div> }));

import DarFunnelPanel from '../admin/DarFunnelPanel';

const FUNNEL_A = {
  total: 100, tester: 10, chitchat: 5, suggested: 8, real: 77,
  ticket: 7, undetermined: 3, judged: 67, correct: 20, wrong: 15, uncovered: 32,
  layers: { answered: 20, unanswered: 15, uncovered: 32, ticket: 7, chitchat: 5, undetermined: 3, suggested: 8, tester: 10 },
};
const FUNNEL_B = {
  total: 60, tester: 6, chitchat: 2, suggested: 4, real: 48,
  ticket: 5, undetermined: 1, judged: 42, correct: 14, wrong: 9, uncovered: 19,
  layers: { answered: 14, unanswered: 9, uncovered: 19, ticket: 5, chitchat: 2, undetermined: 1, suggested: 4, tester: 6 },
};

const REPORT = {
  week_date: '20261124', env: 'prod',
  dar_rates: {}, kb_gap: null, manual_progress: null, wow: {}, this_week: {}, updated_at: null,
  funnel: FUNNEL_A,
  funnel_weekly: [
    {
      week: '2026-W46', label: '11/9–11/15', week_start: '2026-11-09', partial: false, funnel: FUNNEL_A,
      fail_top: [{ type: '操作指引', n: 20, wrong: 8, uncovered: 12, samples: [{ k: '未直答', q: '示例问题A' }] }],
    },
    {
      week: '2026-W47', label: '11/16–11/22', week_start: '2026-11-16', partial: false, funnel: FUNNEL_B,
      // 切周时这卡跟着变，所以周桶里也要带一份
      fail_top: [
        { type: '故障处置', n: 12, wrong: 7, uncovered: 5, samples: [{ k: '未直答', q: '示例问题B' }] },
        { type: '操作指引', n: 6, wrong: 2, uncovered: 4, samples: [] },
      ],
    },
  ],
  fail_top: [{ type: '故障处置', n: 12, samples: ['车辆没有上线到调度系统'] }],
};

describe('开发者模式 · AI 直答率漏斗', () => {
  it('默认展示最近一周的漏斗与直答率', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce(REPORT as never);
    await act(async () => { render(<DarFunnelPanel />); });

    expect(screen.getByText('AI 直答率 · 每周漏斗')).toBeInTheDocument();
    // 最近一周 = FUNNEL_B：14/42 = 33.3%
    // 卡片和走势末点标注都会出现这个数，取全部
    expect(screen.getAllByText('33.3%').length).toBeGreaterThan(0);
    expect(screen.getByText('14 / 42 段')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    // 漏斗层
    expect(screen.getByText('全部段')).toBeInTheDocument();
    expect(screen.getByText('60 段')).toBeInTheDocument();
    // 未直答 Top
    // 类型名与段数现在是两个元素（左右分栏）
    expect(screen.getByText('故障处置')).toBeInTheDocument();
    expect(screen.getByText('12 段')).toBeInTheDocument();
  });

  it('切到另一周，数字跟着变', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce(REPORT as never);
    await act(async () => { render(<DarFunnelPanel />); });

    fireEvent.click(screen.getByText('11/9–11/15'));
    // 卡片和分段条图例都会出现 29.9%（20/67）
    expect(screen.getAllByText('29.9%').length).toBeGreaterThan(0);
    expect(screen.getByText('20 / 67 段')).toBeInTheDocument();
  });

  it('「全部合计」用累计漏斗（所有历史数据），不是 8 周相加', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce(REPORT as never);
    await act(async () => { render(<DarFunnelPanel />); });

    fireEvent.click(screen.getByText('全部合计'));
    // 按钮 + 上方「当前区间」都会出现「全部合计」，取全部
    expect(screen.getAllByText(/全部合计/).length).toBeGreaterThan(0);
    expect(screen.getByText('100 段')).toBeInTheDocument();      // 累计 funnel.total
    expect(screen.getByText('20 / 67 段')).toBeInTheDocument();  // 累计 20/67，不是 34/109
  });

  it('周报没有漏斗字段时给出提示', async () => {
    const { fetchDarLatest } = await import('@/api/dashboard');
    vi.mocked(fetchDarLatest).mockResolvedValueOnce({ ...REPORT, funnel: undefined, funnel_weekly: [] } as never);
    await act(async () => { render(<DarFunnelPanel />); });
    expect(screen.getByText(/没有漏斗数据/)).toBeInTheDocument();
  });
});
