import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock react-router-dom useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock url utils
vi.mock('@/shared/utils/url', () => ({
  getUrlParams: vi.fn(() => null),
  buildWechatAuthUrl: vi.fn(() => 'https://open.weixin.qq.com/connect/oauth2/authorize?appid=test'),
  checkUrlTokens: vi.fn(),
}));

// Mock auth store with mutable refs
let mockIsLoggedIn = false;
let mockIsLoading = false;
let mockIsAdmin = false;

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector?: (s: Record<string, unknown>) => unknown) => {
    const state = {
      isLoggedIn: mockIsLoggedIn,
      isLoading: mockIsLoading,
      isAdmin: mockIsAdmin,
      username: 'testuser',
      token: 'test-token',
    };
    if (selector) return selector(state);
    return state;
  },
}));

import { AuthGuard } from '../authGuard';

const renderAuthGuard = (props: { requireAdmin?: boolean; children?: ReactNode } = {}) => {
  return render(
    <MemoryRouter initialEntries={['/app/call']}>
      <AuthGuard requireAdmin={props.requireAdmin ?? false}>
        {props.children ?? <div data-testid="protected-content">Protected</div>}
      </AuthGuard>
    </MemoryRouter>
  );
};

describe('AuthGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.unstubAllEnvs();
    mockNavigate.mockClear();
    mockIsLoggedIn = false;
    mockIsLoading = false;
    mockIsAdmin = false;
  });

  describe('loading state', () => {
    it('should show loading text when isLoading is true', () => {
      mockIsLoading = true;
      renderAuthGuard();
      expect(screen.getByText('加载中...')).toBeInTheDocument();
    });
  });

  describe('not logged in', () => {
    it('should show auth redirect message when not logged in', () => {
      mockIsLoggedIn = false;
      mockIsLoading = false;
      renderAuthGuard();
      expect(screen.getByText('正在跳转登录...')).toBeInTheDocument();
    });
  });

  describe('logged in', () => {
    it('should render children when logged in', () => {
      mockIsLoggedIn = true;
      mockIsLoading = false;
      renderAuthGuard();
      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
    });

    it('should render children when logged in without admin requirement', () => {
      mockIsLoggedIn = true;
      mockIsAdmin = false;
      renderAuthGuard({ requireAdmin: false });
      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
    });
  });

  describe('admin requirement', () => {
    it('should redirect non-admin to no-permission', async () => {
      mockIsLoggedIn = true;
      mockIsLoading = false;
      mockIsAdmin = false;
      renderAuthGuard({ requireAdmin: true });

      // useEffect runs asynchronously in jsdom
      await waitFor(() => {
        expect(mockNavigate).toHaveBeenCalledWith('/no-permission', { replace: true });
      });
    });

    it('should allow admin to access', () => {
      mockIsLoggedIn = true;
      mockIsLoading = false;
      mockIsAdmin = true;
      renderAuthGuard({ requireAdmin: true });

      expect(screen.getByTestId('protected-content')).toBeInTheDocument();
    });
  });
});
