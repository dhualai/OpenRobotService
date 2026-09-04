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

// Mock workbench store
let mockActiveTab = 'call';
const mockSetActiveTab = vi.fn();

vi.mock('@/stores/workbench', () => ({
  useWorkbenchStore: (selector?: (s: Record<string, unknown>) => unknown) => {
    const state = {
      activeTab: mockActiveTab,
      setActiveTab: mockSetActiveTab,
    };
    if (selector) return selector(state);
    return state;
  },
}));

// Mock tdesign（MainLayout 仅使用 Loading）
vi.mock('tdesign-mobile-react', () => ({
  Loading: ({ text }: { text?: string }) => <div data-testid="loading">{text}</div>,
}));

import MainLayout from '../MainLayout';

const renderLayout = (route = '/call') => {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <MainLayout />
    </MemoryRouter>
  );
};

describe('MainLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    mockSetActiveTab.mockClear();
    mockActiveTab = 'call';
  });

  it('should render bottom nav with three tabs', () => {
    renderLayout();
    expect(screen.getByTestId('app-bottom-nav')).toBeInTheDocument();
    expect(screen.getByTestId('nav-item-call')).toBeInTheDocument();
    expect(screen.getByTestId('nav-item-tasks')).toBeInTheDocument();
    expect(screen.getByTestId('nav-item-admin')).toBeInTheDocument();
  });

  it('should display tabs with icon and text labels', () => {
    renderLayout();
    expect(screen.getByText('我要摇人')).toBeInTheDocument();
    expect(screen.getByText('系统任务')).toBeInTheDocument();
    expect(screen.getByText('后台管理')).toBeInTheDocument();
  });

  it('should highlight active tab from store', () => {
    renderLayout();
    expect(screen.getByTestId('nav-item-call').className).toContain('is-active');
    expect(screen.getByTestId('nav-item-tasks').className).not.toContain('is-active');
  });

  it('should render Outlet for child routes', () => {
    renderLayout();
    expect(screen.getByTestId('outlet')).toBeInTheDocument();
  });

  it('should set activeTab from route on mount', () => {
    renderLayout('/tasks');
    expect(mockSetActiveTab).toHaveBeenCalledWith('tasks');
  });

  it('should set activeTab to call for unknown paths', () => {
    renderLayout('/unknown');
    expect(mockSetActiveTab).toHaveBeenCalledWith('call');
  });

  it('should navigate on tab change', () => {
    renderLayout();
    fireEvent.click(screen.getByTestId('nav-item-tasks'));

    expect(mockSetActiveTab).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith('/tasks');
  });
});
