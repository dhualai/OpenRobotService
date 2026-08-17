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
  });

  it('should render navbar with title', () => {
    renderView();
    expect(screen.getByTestId('navbar')).toHaveTextContent('其他');
  });

  it('should show the three admin tool entries', () => {
    renderView();
    expect(screen.getByText('角色管理')).toBeInTheDocument();
    expect(screen.getByText('权限管理')).toBeInTheDocument();
    expect(screen.getByText('操作记录')).toBeInTheDocument();
  });

  it('should navigate on entry card click', () => {
    renderView();
    fireEvent.click(screen.getByText('角色管理'));
    expect(mockNavigate).toHaveBeenCalledWith('/admin/roles');
  });

  it('should render line icons for each entry card', () => {
    renderView();
    const cards = screen.getAllByRole('button');
    expect(cards.length).toBeGreaterThanOrEqual(3);
    cards.forEach((card) => {
      expect(card.querySelector('svg')).not.toBeNull();
    });
  });
});
