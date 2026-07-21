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
}));

// Mock auth store
let mockIsAdmin = false;
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector?: (s: Record<string, unknown>) => unknown) => {
    const state = { isAdmin: mockIsAdmin };
    if (selector) return selector(state);
    return state;
  },
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
    mockIsAdmin = false;
  });

  it('should render navbar with title', () => {
    renderView();
    expect(screen.getByTestId('navbar')).toHaveTextContent('更多功能');
  });

  it('should show public entries for all users', () => {
    renderView();
    expect(screen.getByText('工单状态监测')).toBeInTheDocument();
    expect(screen.getByText('项目进度管理')).toBeInTheDocument();
    expect(screen.getByText('日报 / 周报')).toBeInTheDocument();
  });

  it('should hide admin-only entries for non-admin', () => {
    mockIsAdmin = false;
    renderView();
    expect(screen.queryByText('用户管理')).not.toBeInTheDocument();
    expect(screen.queryByText('角色管理')).not.toBeInTheDocument();
    expect(screen.queryByText('微信管理')).not.toBeInTheDocument();
  });

  it('should show admin-only entries for admin', () => {
    mockIsAdmin = true;
    renderView();
    expect(screen.getByText('用户管理')).toBeInTheDocument();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
    expect(screen.getByText('微信管理')).toBeInTheDocument();
  });

  it('should show tip for non-admin users', () => {
    mockIsAdmin = false;
    renderView();
    expect(screen.getByText('部分管理功能仅对管理员可见')).toBeInTheDocument();
  });

  it('should hide tip for admin users', () => {
    mockIsAdmin = true;
    renderView();
    expect(screen.queryByText('部分管理功能仅对管理员可见')).not.toBeInTheDocument();
  });

  it('should navigate on entry card click', () => {
    renderView();
    fireEvent.click(screen.getByText('工单状态监测'));
    expect(mockNavigate).toHaveBeenCalledWith('/admin/ticket-monitor');
  });

  it('should render correct emoji icons', () => {
    renderView();
    expect(screen.getByText('🎫')).toBeInTheDocument();
    expect(screen.getByText('📊')).toBeInTheDocument();
    expect(screen.getByText('📋')).toBeInTheDocument();
  });
});
