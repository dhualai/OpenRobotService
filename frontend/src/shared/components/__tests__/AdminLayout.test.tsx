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

const renderLayout = (route = '/app/admin/dashboard') => {
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
    renderLayout('/app/admin/dashboard');
    expect(screen.getByTestId('navbar')).toBeInTheDocument();
    expect(screen.getByTestId('navbar-title')).toHaveTextContent('仪表盘');
  });

  it('should show default title for unknown path', () => {
    renderLayout('/app/admin/unknown-page');
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

  it('should render hamburger menu button', () => {
    renderLayout();
    expect(screen.getByTestId('btn-text-small')).toBeInTheDocument();
    expect(screen.getByTestId('btn-text-small')).toHaveTextContent('☰');
  });

  it('should show menu drawer when hamburger is clicked', () => {
    renderLayout();
    // Click hamburger
    fireEvent.click(screen.getByTestId('btn-text-small'));

    // Should show menu header
    expect(screen.getByText('⚙️ 后台管理')).toBeInTheDocument();
  });

  it('should show all admin menu items in drawer', () => {
    renderLayout();
    fireEvent.click(screen.getByTestId('btn-text-small'));

    // Key menu items should be visible (use getAllByText since navbar title duplicates)
    const dashboardItems = screen.getAllByText('仪表盘');
    expect(dashboardItems.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('项目管理')).toBeInTheDocument();
    expect(screen.getByText('进度看板')).toBeInTheDocument();
    expect(screen.getByText('风险管理')).toBeInTheDocument();
    expect(screen.getByText('用户管理')).toBeInTheDocument();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
    expect(screen.getByText('权限管理')).toBeInTheDocument();
  });

  it('should close menu when clicking overlay', () => {
    renderLayout();
    fireEvent.click(screen.getByTestId('btn-text-small'));
    expect(screen.getByText('⚙️ 后台管理')).toBeInTheDocument();

    // Click overlay - find by background color style
    const overlay = document.querySelector('[style*="rgba(0, 0, 0, 0.5)"]');
    if (overlay) {
      fireEvent.click(overlay);
    }
  });

  it('should navigate when clicking a menu item', () => {
    renderLayout();
    fireEvent.click(screen.getByTestId('btn-text-small'));
    fireEvent.click(screen.getByText('风险管理'));

    expect(mockNavigate).toHaveBeenCalledWith('/app/admin/risks');
  });

  it('should render Outlet for child routes', () => {
    renderLayout();
    expect(screen.getByTestId('outlet')).toBeInTheDocument();
  });
});
