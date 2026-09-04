import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import AvatarImg from '../AvatarImg';

describe('AvatarImg', () => {
  it('should render fallback when src is empty/null', () => {
    const { rerender } = render(<AvatarImg src={null} alt="张三" fallback={<span>占位</span>} />);
    expect(screen.getByText('占位')).toBeInTheDocument();
    rerender(<AvatarImg src="" alt="张三" fallback={<span>占位</span>} />);
    expect(screen.getByText('占位')).toBeInTheDocument();
  });

  it('should render <img> when src exists', () => {
    render(<AvatarImg src="/avatar/1.png" alt="张三" />);
    const img = screen.getByRole('img', { name: '张三' });
    expect(img).toHaveAttribute('src', '/avatar/1.png');
  });

  it('should switch to fallback when image fails to load', () => {
    render(<AvatarImg src="/avatar/bad.png" alt="张三" fallback={<span>占位</span>} />);
    const img = screen.getByRole('img', { name: '张三' });
    fireEvent.error(img);
    expect(screen.queryByRole('img', { name: '张三' })).toBeNull();
    expect(screen.getByText('占位')).toBeInTheDocument();
  });

  it('should retry and render <img> again after src changes', () => {
    const { rerender } = render(<AvatarImg src="/avatar/a.png" alt="张三" fallback={<span>占位</span>} />);
    fireEvent.error(screen.getByRole('img', { name: '张三' }));
    expect(screen.getByText('占位')).toBeInTheDocument();
    rerender(<AvatarImg src="/avatar/b.png" alt="张三" fallback={<span>占位</span>} />);
    const img = screen.getByRole('img', { name: '张三' });
    expect(img).toHaveAttribute('src', '/avatar/b.png');
  });
});
