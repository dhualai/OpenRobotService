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
    Outlet: () => <div data-testid="outlet">Outlet Content</div>,
  };
});

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title, leftArrow, onLeftClick, children }: {
    title?: ReactNode; leftArrow?: boolean; onLeftClick?: () => void; children?: ReactNode;
  }) => (
    <nav data-testid="navbar">
      <span data-testid="navbar-title">{title}</span>
      {leftArrow && <button data-testid="navbar-back" onClick={onLeftClick}>Back</button>}
      <div data-testid="navbar-children">{children}</div>
    </nav>
  ),
  Loading: ({ text }: { text?: string }) => <div data-testid="loading">{text}</div>,
  Button: ({ onClick, children, variant, size }: {
    onClick?: () => void; children?: ReactNode; variant?: string; size?: string;
  }) => (
    <button data-testid={`btn-${variant || 'default'}-${size || 'default'}`} onClick={onClick}>
      {children}
    </button>
  ),
}));

import AdminLayout from '../AdminLayout';

const renderLayout = (route = '/admin/ticket-monitor') => {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AdminLayout />
    </MemoryRouter>
  );
};

describe('AdminLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  it('should render navbar with current page title', () => {
    renderLayout('/admin/ticket-monitor');
    expect(screen.getByTestId('navbar')).toBeInTheDocument();
    expect(screen.getByTestId('navbar-title')).toHaveTextContent('工单状态监测');
  });

  it('should show default title for unknown path', () => {
    renderLayout('/admin/unknown-page');
    expect(screen.getByTestId('navbar-title')).toHaveTextContent('后台管理');
  });

  it('should render navbar with back button', () => {
    renderLayout();
    expect(screen.getByTestId('navbar-back')).toBeInTheDocument();
  });

  it('should navigate back on back button click', () => {
    renderLayout();
    fireEvent.click(screen.getByTestId('navbar-back'));
    expect(mockNavigate).toHaveBeenCalledWith(-1);
  });

  // 原先这里 5 条用例钉的是「Navbar 右侧汉堡按钮 + 抽屉菜单」那套 UI：
  // AdminLayout.tsx 已改为 right={<UserAvatarMenu />}（头像面板），
  // 汉堡按钮与抽屉都不再渲染，旧用例全部失效（找不到 btn-text-small）。
  // 按「用例不测已下线的 UI」删除；管理入口导航如今不在本组件里，
  // 需要覆盖时应在承载它的组件上另写用例，不要恢复这里的旧断言。

  it('should render Outlet for child routes', () => {
    renderLayout();
    expect(screen.getByTestId('outlet')).toBeInTheDocument();
  });
});
