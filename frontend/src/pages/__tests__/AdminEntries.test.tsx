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

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => (
    <nav data-testid="navbar">{title}</nav>
  ),
  // 组件数据加载中会渲染 <Loading/>，mock 需提供否则渲染即抛错
  Loading: ({ text }: { text?: ReactNode }) => <div data-testid="loading">{text}</div>,
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

  it('should render line icons for each entry card', () => {
    renderView();
    // 仅校验管理工具入口卡片含图标；页面另有时间筛选「重置」按钮等无图标按钮，不在断言范围
    const entryCards = screen
      .getAllByRole('button')
      .filter((card) => card.className.includes('admin-entries-card'));
    expect(entryCards.length).toBeGreaterThanOrEqual(3);
    entryCards.forEach((card) => {
      expect(card.querySelector('svg')).not.toBeNull();
    });
  });
});
