import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import ParticipantStack, { MAX_VISIBLE_PARTICIPANTS, type ParticipantItem } from '../ParticipantStack';

const mk = (n: number): ParticipantItem[] =>
  Array.from({ length: n }, (_, i) => ({
    username: `user${i + 1}`,
    name: `张俊磊${i + 1}号`,
    comment_count: n - i, // 后端已按「评论数降序 → 时间降序」排好，第 1 个是最热参与人
    has_unread: i === 1,
  }));

describe('ParticipantStack', () => {
  it('should render nothing when participants is empty', () => {
    const { container } = render(<ParticipantStack participants={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('should filter out entries without username', () => {
    const { container } = render(
      <ParticipantStack participants={[{ username: '' }, { username: 'a', name: 'A' }]} />,
    );
    expect(container.querySelectorAll('.participant-stack__item')).toHaveLength(1);
  });

  it('should show at most MAX_VISIBLE_PARTICIPANTS avatars', () => {
    const { container } = render(<ParticipantStack participants={mk(5)} />);
    expect(container.querySelectorAll('.participant-stack__item')).toHaveLength(MAX_VISIBLE_PARTICIPANTS);
  });

  it('should collapse overflow into a round +N badge', () => {
    const { container } = render(<ParticipantStack participants={mk(5)} />);
    const badge = container.querySelector('.task-card2__participant--overflow');
    expect(badge?.textContent).toBe('+2');
  });

  it('should not render +N when participants fit within limit', () => {
    const { container } = render(<ParticipantStack participants={mk(3)} />);
    expect(container.querySelector('.task-card2__participant--overflow')).toBeNull();
  });

  it('should render all avatars when exactly at the limit', () => {
    const { container } = render(<ParticipantStack participants={mk(3)} />);
    expect(container.querySelectorAll('.participant-stack__item')).toHaveLength(3);
  });

  it('should render the stack as a single clickable button', () => {
    const { container } = render(<ParticipantStack participants={mk(2)} />);
    const btn = container.querySelector('button.participant-stack');
    expect(btn).not.toBeNull();
    expect(container.querySelectorAll('button.participant-stack')).toHaveLength(1);
  });

  it('should navigate on click without opening any popup', () => {
    const onLocate = vi.fn();
    const { container } = render(<ParticipantStack participants={mk(5)} onLocate={onLocate} />);

    fireEvent.click(container.querySelector('.participant-stack')!);

    // 直跳：以排序最靠前的参与人（最新/最热发言者）为定位目标
    expect(onLocate).toHaveBeenCalledTimes(1);
    expect(onLocate).toHaveBeenCalledWith(expect.objectContaining({ username: 'user1' }));
    // 不再有名单浮层
    expect(container.querySelector('.participant-stack__pop')).toBeNull();
    expect(container.querySelector('.participant-stack__pop-list')).toBeNull();
  });

  it('should not crash when onLocate is not provided', () => {
    const { container } = render(<ParticipantStack participants={mk(2)} />);
    expect(() => fireEvent.click(container.querySelector('.participant-stack')!)).not.toThrow();
  });

  it('should stop propagation so card click is not triggered', () => {
    const onCardClick = vi.fn();
    const { container } = render(
      <div onClick={onCardClick}>
        <ParticipantStack participants={mk(2)} onLocate={() => {}} />
      </div>,
    );
    fireEvent.click(container.querySelector('.participant-stack')!);
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('should mark unread participant with red dot on its avatar', () => {
    const { container } = render(<ParticipantStack participants={mk(3)} />);
    expect(container.querySelectorAll('.participant-stack__item.has-unread')).toHaveLength(1);
    expect(container.querySelectorAll('.participant-stack__dot')).toHaveLength(1);
  });

  it('should expose participant names in title for accessibility', () => {
    const { container } = render(<ParticipantStack participants={mk(3)} />);
    const btn = container.querySelector('.participant-stack')!;
    expect(btn.getAttribute('title')).toContain('参与人：张俊磊1号、张俊磊2号、张俊磊3号');
    expect(btn.getAttribute('aria-label')).toContain('张俊磊1号');
  });

  it('should append unread hint in title when there are unread comments', () => {
    const { container } = render(<ParticipantStack participants={mk(3)} />);
    // mk(3) 中第 2 位 has_unread
    expect(container.querySelector('.participant-stack')!.getAttribute('title')).toContain('（1 人有未读评论）');
  });
});
