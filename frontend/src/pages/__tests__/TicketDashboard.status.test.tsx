import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/dashboard', () => ({
  fetchDashboardSummaryAll: vi.fn(),
  fetchDarLatest: vi.fn().mockResolvedValue(null),
  fetchTicketCreatorOrgAnalysis: vi.fn().mockResolvedValue({ dimension: 'company', companies: [], by_org: [] }),
}));
vi.mock('tdesign-mobile-react', () => ({ Navbar: () => null }));
vi.mock('../admin/QuestionModuleCard', () => ({ default: () => null }));

import { fetchDashboardSummaryAll } from '@/api/dashboard';
import { getTicketStatusCount, TICKET_STATUS_LIST } from '@/shared/constants/dashboard';
import { useAuthStore } from '@/stores/auth';
import TicketDashboard from '../admin/TicketDashboard';

const byStatus = {
  new: 1, in_progress: 2, pending_requested: 3, paused: 4,
  unsolvable_requested: 5, unsolvable: 6, resolved: 7, closed: 8,
  cancelled: 9, archiving: 10, archived: 11,
};

describe('工单看板状态分组', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({ username: 'status-test', projectIds: ['P1'], permissions: [] });
    vi.mocked(fetchDashboardSummaryAll).mockResolvedValue({
      tickets: { total: 66, pending_count: 6, overdue_count: 0, resolved_rate: 0, by_status: byStatus },
    } as Awaited<ReturnType<typeof fetchDashboardSummaryAll>>);
  });

  it('所有状态恰好统计一次，兼容空数据和旧缓存', () => {
    expect(TICKET_STATUS_LIST.reduce((total, status) => total + getTicketStatusCount(byStatus, status), 0)).toBe(66);
    for (const status of TICKET_STATUS_LIST) {
      expect(getTicketStatusCount(undefined, status)).toBe(0);
      expect(getTicketStatusCount({}, status)).toBe(0);
    }
    const archive = TICKET_STATUS_LIST.find((status) => status.key === 'archive')!;
    expect(getTicketStatusCount({ archived: 2 }, archive)).toBe(2);
  });

  it('展示进行中、已挂起和合并后的无法解决、知识沉淀数量', async () => {
    const { container } = await act(async () => render(<MemoryRouter><TicketDashboard /></MemoryRouter>));
    expect(screen.queryByText('处理中')).not.toBeInTheDocument();
    expect(screen.queryByText('暂停/挂起')).not.toBeInTheDocument();
    for (const [label, value, percent] of [
      ['进行中', 2, '3%'], ['挂起确认中', 3, '5%'], ['已挂起', 4, '6%'],
      ['无法解决', 11, '17%'], ['知识沉淀', 21, '32%'],
    ] as const) {
      const item = screen.getByText(label).closest('li')!;
      expect(within(item).getByText(String(value))).toBeInTheDocument();
      expect(within(item).getByText(percent)).toBeInTheDocument();
    }
    expect(container.querySelector('.mac-donut__value')).toHaveTextContent('66');
    expect(container.querySelectorAll('.mac-donut__svg circle[stroke-dasharray]')).toHaveLength(TICKET_STATUS_LIST.length);
  });

  it.each([['无法解决', 'unsolvable'], ['知识沉淀', 'archive'], ['已挂起', 'paused']])(
    '点击%s图例进入对应状态明细', async (label, key) => {
      await act(async () => render(
        <MemoryRouter initialEntries={['/admin/ticket-dashboard']}>
          <Routes>
            <Route path="/admin/ticket-dashboard" element={<TicketDashboard />} />
            <Route path={`/admin/dashboard/tickets/${key}`} element={<div>状态明细页</div>} />
          </Routes>
        </MemoryRouter>,
      ));
      fireEvent.click(screen.getByText(label));
      expect(screen.getByText('状态明细页')).toBeInTheDocument();
    },
  );
});
