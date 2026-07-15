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

import AdminView from '../admin/AdminView';

const renderView = () => {
  return render(
    <MemoryRouter>
      <AdminView />
    </MemoryRouter>
  );
};

describe('AdminView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    mockIsAdmin = false;
  });

  it('should render navbar with title', () => {
    renderView();
    expect(screen.getByTestId('navbar')).toHaveTextContent('后台管理');
  });

  it('should display slogan', () => {
    renderView();
    expect(screen.getByText('东厂管不了的事，我西厂管！')).toBeInTheDocument();
  });

  it('should show public entries for all users', () => {
    renderView();
    expect(screen.getByText('项目看板')).toBeInTheDocument();
    expect(screen.getByText('项目管理')).toBeInTheDocument();
    expect(screen.getByText('风险红黄灯')).toBeInTheDocument();
    expect(screen.getByText('进度看板')).toBeInTheDocument();
    expect(screen.getByText('报表分析')).toBeInTheDocument();
    expect(screen.getByText('机器人数据')).toBeInTheDocument();
  });

  it('should hide admin-only entries for non-admin', () => {
    mockIsAdmin = false;
    renderView();
    expect(screen.queryByText('用户管理')).not.toBeInTheDocument();
    expect(screen.queryByText('角色管理')).not.toBeInTheDocument();
    expect(screen.queryByText('权限管理')).not.toBeInTheDocument();
    expect(screen.queryByText('资源管理')).not.toBeInTheDocument();
  });

  it('should show admin-only entries for admin', () => {
    mockIsAdmin = true;
    renderView();
    expect(screen.getByText('用户管理')).toBeInTheDocument();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
    expect(screen.getByText('权限管理')).toBeInTheDocument();
    expect(screen.getByText('资源管理')).toBeInTheDocument();
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
    fireEvent.click(screen.getByText('项目看板'));
    expect(mockNavigate).toHaveBeenCalledWith('/app/admin/dashboard');
  });

  it('should render correct emoji icons', () => {
    renderView();
    expect(screen.getByText('📊')).toBeInTheDocument();
    expect(screen.getByText('📁')).toBeInTheDocument();
    expect(screen.getByText('⚠️')).toBeInTheDocument();
  });
});
