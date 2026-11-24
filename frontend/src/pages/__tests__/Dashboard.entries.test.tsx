import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PERM_LEARNING } from '@/api/learning';
import { useAuthStore } from '@/stores/auth';
import AdminEntries from '../admin/AdminEntries';
import Dashboard from '../admin/Dashboard';

vi.mock('@/api/dashboard', () => ({
  fetchDashboardSummaryAll: vi.fn().mockRejectedValue(new Error('看板暂不可用')),
  syncWecomProjects: vi.fn(),
}));
vi.mock('@/api/wxpay', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/wxpay')>(),
  fetchRewardStats: vi.fn().mockResolvedValue(null),
}));
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/SubscriptionReminder', () => ({ default: () => null }));
vi.mock('../admin/KnowledgeContributionCard', () => ({ default: () => null }));
vi.mock('../admin/HighValueQuestionCard', () => ({ default: () => null }));
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title: string }) => <header>{title}</header>,
  Popup: () => null,
  Toast: vi.fn(),
}));

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <Routes>
        <Route path="/admin" element={<Dashboard />} />
        <Route path="/admin/entries" element={<AdminEntries />} />
        <Route path="/admin/entries/:category" element={<AdminEntries />} />
        <Route path="/admin/question-generation" element={<p>题目生成页面</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('后台首页其他入口权限', () => {
  beforeEach(() => {
    useAuthStore.setState({
      username: 'entries-test', projectIds: [], permissions: [], isAdmin: false,
      canViewKnowledgeContribution: false, canManageKnowledgeChallenges: false,
    });
  });

  it.each([
    ['原其他权限', ['frontend:admin:other:show']],
    ['题目管理权限', [PERM_LEARNING]],
    ['管理员', ['admin']],
    ['全局通配权限', ['*']],
    ['后台通配权限', ['frontend:admin:*:*']],
  ])('%s可从首页进入其他分类', async (_label, permissions) => {
    useAuthStore.setState({ permissions });
    await act(async () => { renderDashboard(); });
    fireEvent.click(screen.getByText('其他'));
    expect(screen.getByRole('button', { name: /^运营与数据/ })).toBeInTheDocument();
  });

  it.each([[], ['frontend:task:all'], ['frontend:admin:roles:show']])('无相关授权时继续隐藏其他入口：%j', async (...permissions) => {
    useAuthStore.setState({ permissions });
    await act(async () => { renderDashboard(); });
    expect(screen.queryByText('其他')).not.toBeInTheDocument();
  });

  it('仅题目管理权限可经其他、运营与数据进入题目生成，不开放受限的运营工具', async () => {
    useAuthStore.setState({ permissions: [PERM_LEARNING] });
    await act(async () => { renderDashboard(); });
    fireEvent.click(screen.getByText('其他'));
    fireEvent.click(screen.getByRole('button', { name: /^运营与数据/ }));
    expect(screen.queryByRole('button', { name: /^公告管理/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^奖励管理/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^题目生成/ }));
    expect(screen.getByText('题目生成页面')).toBeInTheDocument();
  });

  it('仅原其他权限不显示题目生成', async () => {
    useAuthStore.setState({ permissions: ['frontend:admin:other:show'] });
    await act(async () => { renderDashboard(); });
    fireEvent.click(screen.getByText('其他'));
    fireEvent.click(screen.getByRole('button', { name: /^运营与数据/ }));
    expect(screen.queryByRole('button', { name: /^题目生成/ })).not.toBeInTheDocument();
  });

  it('题目管理授权与撤销后同步更新父入口', async () => {
    await act(async () => { renderDashboard(); });
    expect(screen.queryByText('其他')).not.toBeInTheDocument();
    act(() => { useAuthStore.setState({ permissions: [PERM_LEARNING] }); });
    expect(screen.getByText('其他')).toBeInTheDocument();
    act(() => { useAuthStore.setState({ permissions: [] }); });
    expect(screen.queryByText('其他')).not.toBeInTheDocument();
  });
});
