import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock react-router-dom 的 useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// 仅用到 Toast
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
}));

// Mock API
vi.mock('@/api/client', () => ({
  createRequest: vi.fn(() =>
    vi.fn().mockResolvedValue({
      access_token: 'test-access',
      refresh_token: 'test-refresh',
      expires_in: 3600,
    }),
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

  it('渲染品牌标题与副标题', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    expect(screen.getByText('OpenRobotService')).toBeInTheDocument();
    expect(screen.getByText(/机器人开源平台/)).toBeInTheDocument();
  });

  it('包含账号与密码输入框', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    const inputs = document.querySelectorAll('input');
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it('密码默认掩码，点击眼睛可切换显隐', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    expect(document.querySelector('input[type="password"]')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '显示密码' }));
    expect(document.querySelector('input[type="text"]')).toBeTruthy();
    expect(screen.getByRole('button', { name: '隐藏密码' })).toBeInTheDocument();
  });

  it('空表单提交给出提示且不导航', () => {
    render(
      <MemoryRouter>
        <Login />
      </MemoryRouter>,
    );
    fireEvent.submit(document.querySelector('form') as HTMLFormElement);
    expect(mockNavigate).not.toHaveBeenCalled();
  });
});
