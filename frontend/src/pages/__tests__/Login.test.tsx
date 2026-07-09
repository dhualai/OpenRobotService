import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
  Button: ({ children, onClick, loading, ...props }: Record<string, unknown>) => (
    <button onClick={onClick as () => void} disabled={loading as boolean} data-testid="btn" {...props}>
      {children}
    </button>
  ),
  NavBar: ({ title }: Record<string, unknown>) => (
    <nav data-testid="navbar">{title as string}</nav>
  ),
  Form: ({ children, onSubmit }: Record<string, unknown>) => (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        (onSubmit as (e: React.FormEvent) => void)?.(e);
      }}
    >
      {children}
    </form>
  ),
  FormItem: ({ children }: Record<string, unknown>) => <div>{children}</div>,
  Input: (props: Record<string, unknown>) => (
    <input placeholder={props.placeholder as string} defaultValue={props.defaultValue as string} />
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

  it('should show debug hint text', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    expect(
      screen.getByText('Debug模式：用户名和密码均为 debug')
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

  it('should have NavBar with title', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>
    );

    expect(screen.getByTestId('navbar')).toHaveTextContent('摇人吧');
  });
});
