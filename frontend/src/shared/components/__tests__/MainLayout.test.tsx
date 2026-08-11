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

// Mock tdesign
vi.mock('tdesign-mobile-react', () => ({
  TabBar: ({ children, value, onChange }: { children?: ReactNode; value?: string; onChange?: (v: string) => void }) => (
    <div data-testid="tabbar" data-value={value}>
      <div data-testid="tabbar-click" onClick={() => onChange?.('tasks')}>switch</div>
      {children}
    </div>
  ),
  TabBarItem: ({ value, children, 'aria-label': ariaLabel }: { value?: string; children?: ReactNode; 'aria-label'?: string }) => (
    <div data-testid={`tabitem-${value}`} data-aria-label={ariaLabel}>{children}</div>
  ),
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

  it('should render TabBar with three tabs', () => {
    renderLayout();
    expect(screen.getByTestId('tabbar')).toBeInTheDocument();
    expect(screen.getByTestId('tabitem-call')).toBeInTheDocument();
    expect(screen.getByTestId('tabitem-tasks')).toBeInTheDocument();
    expect(screen.getByTestId('tabitem-admin')).toBeInTheDocument();
  });

  it('should display icon-only tabs with aria labels', () => {
    renderLayout();
    expect(screen.getByTestId('tabitem-call')).toHaveAttribute('data-aria-label', '我要摇人');
    expect(screen.getByTestId('tabitem-tasks')).toHaveAttribute('data-aria-label', '系统任务');
    expect(screen.getByTestId('tabitem-admin')).toHaveAttribute('data-aria-label', '后台管理');
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
    const trigger = screen.getByTestId('tabbar-click');
    fireEvent.click(trigger);

    expect(mockSetActiveTab).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith('/tasks');
  });
});
