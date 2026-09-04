import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

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

const renderView = () => {
  return render(
    <MemoryRouter>
      <AdminEntries />
    </MemoryRouter>
  );
};

describe('AdminEntries', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
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
    expect(mockNavigate).toHaveBeenCalledWith('/admin/roles');
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
