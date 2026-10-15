import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/dashboard', () => ({ fetchDashboardSummaryAll: vi.fn().mockResolvedValue({}) }));
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
