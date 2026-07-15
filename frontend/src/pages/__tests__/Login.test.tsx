import type { ReactNode, FormEvent } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock react-router-dom's useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock tdesign-mobile-react
vi.mock('tdesign-mobile-react', () => ({
  Button: ({ children, onClick, loading }: { children?: ReactNode; onClick?: () => void; loading?: boolean }) => (
    <button onClick={onClick} disabled={loading} data-testid="btn">
      {children}
    </button>
  ),
  Navbar: ({ title }: { title?: ReactNode }) => (
    <nav data-testid="navbar">{title}</nav>
  ),
  Form: ({ children, onSubmit }: { children?: ReactNode; onSubmit?: (e: FormEvent) => void }) => (
    <form
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        onSubmit?.(e);
      }}
    >
      {children}
    </form>
  ),
  FormItem: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  Input: ({ placeholder, defaultValue }: { placeholder?: string; defaultValue?: string }) => (
    <input placeholder={placeholder} defaultValue={defaultValue} />
  ),
  Toast: vi.fn(),
}));

// Mock API
vi.mock('@/api/client', () => ({
  createRequest: vi.fn(() =>
    vi.fn().mockImplementation(() => {
      // Will be overridden per-test
      return Promise.resolve({
        access_token: 'test-access',
        refresh_token: 'test-refresh',
        expires_in: 3600,
      });
    })
  ),
}));

// Mock auth store
const mockLogin = vi.fn();
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector?: (s: Record<string, unknown>) => unknown) => {
    const state = { login: mockLogin };
    if (selector) return selector(state);
    return state;
  },
}));

import Login from '../../pages/Login';

describe('Login', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
    mockLogin.mockClear();
  });

  it('should render login form', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    expect(screen.getByText('登录 OpenRobotService')).toBeInTheDocument();
    expect(screen.getByText('登录')).toBeInTheDocument();
    expect(screen.getByText('摇人吧')).toBeInTheDocument();
  });

  it('should show login hint text', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    expect(
      screen.getByText('请使用您的系统账号登录')
    ).toBeInTheDocument();
  });

  it('should have username and password inputs', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    const inputs = document.querySelectorAll('input');
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it('should have Navbar with title', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    expect(screen.getByTestId('navbar')).toHaveTextContent('摇人吧');
  });
});
