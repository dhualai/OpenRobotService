import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock react-router-dom
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
  Button: ({ children, onClick }: any) => (
    <button onClick={onClick as () => void} data-testid="btn">
      {children}
    </button>
  ),
  Navbar: () => null,
}));

import NoPermission from '../../pages/NoPermission';

describe('NoPermission', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockNavigate.mockClear();
  });

  it('should render no-permission message', () => {
    render(
      <MemoryRouter>
        <NoPermission />
      </MemoryRouter>
    );

    expect(screen.getByText('权限不足')).toBeInTheDocument();
    expect(
      screen.getByText('您没有访问此页面的权限，请联系管理员')
    ).toBeInTheDocument();
  });

  it('should render a return home button', () => {
    render(
      <MemoryRouter>
        <NoPermission />
      </MemoryRouter>
    );

    expect(screen.getByText('返回首页')).toBeInTheDocument();
  });

  it('should navigate to /call on button click', () => {
    render(
      <MemoryRouter>
        <NoPermission />
      </MemoryRouter>
    );

    fireEvent.click(screen.getByText('返回首页'));
    expect(mockNavigate).toHaveBeenCalledWith('/call', { replace: true });
  });

  it('should show lock emoji', () => {
    const { container } = render(
      <MemoryRouter>
        <NoPermission />
      </MemoryRouter>
    );

    expect(container.textContent).toContain('🔒');
  });
});
