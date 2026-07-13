import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import Pagination from '../Pagination';

describe('Pagination', () => {
  it('should render nothing when totalPages <= 1', () => {
    const { container } = render(
      <Pagination current={1} total={5} pageSize={10} onChange={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });

  it('should render page buttons when totalPages > 1', () => {
    render(
      <Pagination current={1} total={25} pageSize={10} onChange={() => {}} />
    );
    // 25 items / 10 pageSize = 3 pages
    expect(screen.getByText('上一页')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('下一页')).toBeInTheDocument();
  });

  it('should disable "上一页" on first page', () => {
    render(
      <Pagination current={1} total={50} pageSize={10} onChange={() => {}} />
    );
    const prevBtn = screen.getByText('上一页');
    expect(prevBtn).toBeDisabled();
  });

  it('should disable "下一页" on last page', () => {
    render(
      <Pagination current={5} total={50} pageSize={10} onChange={() => {}} />
    );
    const nextBtn = screen.getByText('下一页');
    expect(nextBtn).toBeDisabled();
  });

  it('should call onChange with correct page on click', () => {
    const onChange = vi.fn();
    render(
      <Pagination current={2} total={50} pageSize={10} onChange={onChange} />
    );

    fireEvent.click(screen.getByText('上一页'));
    expect(onChange).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByText('下一页'));
    expect(onChange).toHaveBeenCalledWith(3);

    fireEvent.click(screen.getByText('4'));
    expect(onChange).toHaveBeenCalledWith(4);
  });

  it('should highlight current page with different style', () => {
    render(
      <Pagination current={3} total={50} pageSize={10} onChange={() => {}} />
    );
    const currentBtn = screen.getByText('3');
    expect(currentBtn).toHaveStyle({ background: '#0052d9', color: '#fff' });
  });

  it('should not highlight non-current pages', () => {
    render(
      <Pagination current={1} total={50} pageSize={10} onChange={() => {}} />
    );
    const otherBtn = screen.getByText('2');
    expect(otherBtn).toHaveStyle({ color: '#333' });
  });
});
