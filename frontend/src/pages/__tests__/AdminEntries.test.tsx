import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';

// 入口显隐走真 auth store 的 hasPermission；跳转走真路由，断言落在真实地址上

// 用户统计区域依赖 Loading（加载态）与 ReactECharts（两个分组共四张图表），
// jsdom 无 canvas，均以占位组件 mock
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => (
    <nav data-testid="navbar">{title}</nav>
  ),
  Loading: ({ text }: { text?: ReactNode }) => (
    <div data-testid="loading">{text}</div>
  ),
}));

vi.mock('echarts-for-react', () => ({
  default: () => <div data-testid="echarts" />,
}));

import AdminEntries from '../admin/AdminEntries';
import { useAuthStore } from '@/stores/auth';

/** 地址栏探针：入口点击落到真实 URL 上 */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="path">{location.pathname}</div>;
}

const renderView = () => {
  return render(
    <MemoryRouter initialEntries={['/admin']}>
      <LocationProbe />
      <Routes>
        <Route path="*" element={<AdminEntries />} />
      </Routes>
    </MemoryRouter>
  );
};

describe('AdminEntries', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // admin 权限码直通（就是线上用后端派生权限码判定入口显隐的那条路径）
    useAuthStore.setState({ username: 'admin', permissions: ['admin'] });
  });

  it('shows developer mode only when permitted', () => {
    renderView();
    expect(screen.getByText('开发者模式')).toBeInTheDocument();
    fireEvent.click(screen.getByText('开发者模式'));
    expect(screen.getByTestId('path').textContent).toBe('/admin/dispatch-dev');
  });

  it('hides developer mode without permission', () => {
    useAuthStore.setState({ permissions: ['frontend:admin:roles:show'] });
    renderView();
    expect(screen.queryByText('开发者模式')).not.toBeInTheDocument();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
  });

  it('should render navbar with title', () => {
    renderView();
    expect(screen.getByTestId('navbar')).toHaveTextContent('其他');
  });

  it('should show the three admin tool entries', () => {
    renderView();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
    expect(screen.getByText('权限管理')).toBeInTheDocument();
    expect(screen.getByText('操作记录')).toBeInTheDocument();
  });

  it('should navigate on entry card click', () => {
    renderView();
    fireEvent.click(screen.getByText('角色管理'));
    expect(screen.getByTestId('path').textContent).toBe('/admin/roles');
  });

  it('should render the two user stats groups and all four charts', () => {
    renderView();
    expect(screen.getByText('用户增减趋势')).toBeInTheDocument();
    expect(screen.getByText('关注来源分布')).toBeInTheDocument();
    expect(screen.getByText('当前用户构成')).toBeInTheDocument();
    expect(screen.getByText('用户来源分布')).toBeInTheDocument();
    expect(screen.getByText('重置')).toBeInTheDocument();
  });

  it('should render line icons for each entry card', () => {
    renderView();
    // 页面还有用户统计区的「重置」等按钮，这里只校验入口卡片
    const cards = screen
      .getAllByRole('button')
      .filter((b) => b.classList.contains('admin-entries-card'));
    expect(cards.length).toBeGreaterThanOrEqual(3);
    cards.forEach((card) => {
      expect(card.querySelector('svg')).not.toBeNull();
    });
  });
});
