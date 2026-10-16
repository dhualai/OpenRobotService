import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title, onLeftClick }: { title?: ReactNode; onLeftClick?: () => void }) => (
    <nav data-testid="navbar">
      {title}
      <button onClick={onLeftClick}>返回</button>
    </nav>
  ),
  Loading: ({ text }: { text?: ReactNode }) => (
    <div data-testid="loading">{text}</div>
  ),
}));

vi.mock('@/shared/components/ReactECharts', () => ({
  default: () => <div data-testid="echarts" />,
}));

vi.mock('@/api/wechat', () => ({
  fetchUserSummary: vi.fn(),
  fetchBatchUserInfo: vi.fn(),
  USER_SOURCE_LABELS: { 0: '其他合计' },
}));

import AdminEntries from '../admin/AdminEntries';
import { fetchBatchUserInfo, fetchUserSummary } from '@/api/wechat';
import { PERM_DISPATCH_DEV } from '@/shared/constants/dispatchDev';
import { PERM_TASK_POLICY } from '@/pages/admin/TaskPolicyPage';
import { PERM_WECOM_SHEETS } from '@/api/wecomSheets';
import { PERM_WXPAY_ADMIN } from '@/api/wxpay';
import { PERM_ANNOUNCEMENT } from '@/api/announcement';
import { useAuthStore } from '@/stores/auth';

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="path">{location.pathname}</div>;
}

const renderView = (path = '/admin/entries') => {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LocationProbe />
      <Routes>
        <Route path="/admin/entries" element={<AdminEntries />} />
        <Route path="/admin/entries/:category" element={<AdminEntries />} />
        <Route path="*" element={<div>功能页面</div>} />
      </Routes>
    </MemoryRouter>
  );
};

describe('AdminEntries', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ username: 'admin', permissions: ['admin'] });
    vi.mocked(fetchUserSummary).mockResolvedValue({
      success: true,
      total: 1,
      list: [{ ref_date: '2026-10-14', user_source: 0, new_user: 2, cancel_user: 1 }],
    });
    vi.mocked(fetchBatchUserInfo).mockResolvedValue({
      success: true,
      total: 3,
      real: 2,
      virtual: 1,
      scene_distribution: [{ scene: 'ADD_SCENE_SEARCH', value: 2 }],
    });
  });

  it('shows only four core categories on the secondary page', () => {
    renderView();
    expect(screen.getByTestId('navbar')).toHaveTextContent('其他');
    ['用户与权限', '工单与派单', '运营与数据', '系统工具'].forEach((label) => {
      expect(screen.getByRole('button', { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    });
    const cards = screen.getAllByRole('button').filter((button) => button.classList.contains('admin-entries-card'));
    expect(cards).toHaveLength(4);
    expect(screen.queryByRole('button', { name: /^用户管理/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^开发者模式/ })).not.toBeInTheDocument();
    expect(screen.queryByText('用户统计')).not.toBeInTheDocument();
    expect(fetchUserSummary).not.toHaveBeenCalled();
    expect(fetchBatchUserInfo).not.toHaveBeenCalled();
  });

  it.each([
    ['用户与权限', 'users', ['用户管理', '角色管理', '权限管理', '分配角色', '设置用户']],
    ['工单与派单', 'dispatch', ['责任模块树', '双树试做', '工单关联规则']],
    ['运营与数据', 'operations', ['二维码管理', '企微表格', '公告管理', '奖励管理']],
    ['系统工具', 'system', ['操作记录', '开发者模式']],
  ])('opens the %s category with only its related tools', async (label, category, entries) => {
    renderView();
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}`) }));
    expect(screen.getByTestId('path')).toHaveTextContent(`/admin/entries/${category}`);
    expect(screen.getByTestId('navbar')).toHaveTextContent(label);
    const cards = screen.getAllByRole('button').filter((button) => button.classList.contains('admin-entries-card'));
    expect(cards).toHaveLength(entries.length);
    entries.forEach((entry) => {
      expect(screen.getByRole('button', { name: new RegExp(`^${entry}`) })).toBeInTheDocument();
    });
    if (category === 'users') await screen.findByText('用户统计');
    else expect(fetchUserSummary).not.toHaveBeenCalled();
  });

  it.each([
    ['users', '用户管理', '/admin/users'],
    ['users', '角色管理', '/admin/roles'],
    ['users', '权限管理', '/admin/permissions'],
    ['users', '分配角色', '/admin/assign-role'],
    ['users', '设置用户', '/admin/user-setup'],
    ['dispatch', '责任模块树', '/admin/module-tree'],
    ['dispatch', '双树试做', '/admin/dual-tree'],
    ['dispatch', '工单关联规则', '/admin/task-policy'],
    ['operations', '二维码管理', '/admin/qrcode-manage'],
    ['operations', '企微表格', '/admin/wecom-sheets'],
    ['operations', '公告管理', '/admin/announcements'],
    ['operations', '奖励管理', '/admin/wxpay-rewards'],
    ['system', '操作记录', '/admin/operation-logs'],
    ['system', '开发者模式', '/admin/dispatch-dev'],
  ])('keeps the existing destination for %s / %s', (category, label, destination) => {
    renderView(`/admin/entries/${category}`);
    fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${label}`) }));
    expect(screen.getByTestId('path').textContent).toBe(destination);
  });

  it.each([
    ['system', '开发者模式', PERM_DISPATCH_DEV],
    ['dispatch', '工单关联规则', PERM_TASK_POLICY],
    ['operations', '企微表格', PERM_WECOM_SHEETS],
    ['operations', '奖励管理', PERM_WXPAY_ADMIN],
    ['operations', '公告管理', PERM_ANNOUNCEMENT],
  ])('preserves permission filtering and updates for %s / %s', (category, label, permission) => {
    useAuthStore.setState({ permissions: ['frontend:admin:roles:show'] });
    renderView(`/admin/entries/${category}`);
    expect(screen.queryByRole('button', { name: new RegExp(`^${label}`) })).not.toBeInTheDocument();
    act(() => { useAuthStore.setState({ permissions: [permission] }); });
    expect(screen.getByRole('button', { name: new RegExp(`^${label}`) })).toBeInTheDocument();
    act(() => { useAuthStore.setState({ permissions: [] }); });
    expect(screen.queryByRole('button', { name: new RegExp(`^${label}`) })).not.toBeInTheDocument();
  });

  it('does not advertise restricted tools in category descriptions', () => {
    useAuthStore.setState({ permissions: ['frontend:admin:roles:show'] });
    renderView();
    ['开发者模式', '工单关联规则', '企微表格', '公告管理', '奖励管理'].forEach((label) => {
      expect(screen.queryByText(new RegExp(label))).not.toBeInTheDocument();
    });
  });

  it('returns from the secondary page to the dashboard', () => {
    renderView();
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(screen.getByTestId('path').textContent).toBe('/admin');
  });

  it('returns from a category to the secondary page even on a direct visit', () => {
    renderView('/admin/entries/system');
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(screen.getByTestId('path').textContent).toBe('/admin/entries');
    expect(screen.getByTestId('navbar')).toHaveTextContent('其他');
  });

  it('redirects unknown categories to the secondary page', () => {
    renderView('/admin/entries/unknown');
    expect(screen.getByTestId('path').textContent).toBe('/admin/entries');
    expect(screen.getByRole('button', { name: /^用户与权限/ })).toBeInTheDocument();
  });

  it('moves the user statistics and all four charts into the user category', async () => {
    renderView('/admin/entries/users');
    await screen.findByText('用户统计');
    expect(screen.getByText('用户增减趋势')).toBeInTheDocument();
    expect(screen.getByText('关注来源分布')).toBeInTheDocument();
    expect(screen.getByText('当前用户构成')).toBeInTheDocument();
    expect(screen.getByText('用户来源分布')).toBeInTheDocument();
    expect(screen.getByText('重置')).toBeInTheDocument();
    await waitFor(() => { expect(screen.getAllByTestId('echarts')).toHaveLength(4); });
    expect(fetchUserSummary).toHaveBeenCalledTimes(1);
    expect(fetchBatchUserInfo).toHaveBeenCalledTimes(1);
  });

  it('renders line icons for each category card', () => {
    renderView();
    const cards = screen
      .getAllByRole('button')
      .filter((b) => b.classList.contains('admin-entries-card'));
    expect(cards).toHaveLength(4);
    cards.forEach((card) => {
      expect(card.querySelector('svg')).not.toBeNull();
    });
  });
});
