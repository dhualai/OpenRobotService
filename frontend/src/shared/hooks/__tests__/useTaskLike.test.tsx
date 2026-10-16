// useTaskLike：详情页点赞按钮的数据层。
// 钉住四件事：首屏拉一次状态 / 拉取失败静默 / toggle 乐观更新并回滚 / pending 期间不重复提交。
// 仓库没有 renderHook 的使用先例，这里用宿主组件把 state 打到 DOM 上断言。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useState } from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/api/taskLike', () => ({
  fetchTaskLike: vi.fn(),
  toggleTaskLike: vi.fn(),
}));

import { fetchTaskLike, toggleTaskLike, type TaskLikeState } from '@/api/taskLike';
import { useTaskLike } from '../useTaskLike';

const mockFetch = vi.mocked(fetchTaskLike);
const mockToggle = vi.mocked(toggleTaskLike);

/** 宿主：state → DOM；toggle 返回值也打到 DOM（详情页据此决定是否弹错） */
function Harness({ taskId }: { taskId: string | number | null }) {
  const { state, pending, toggle } = useTaskLike(taskId);
  const [lastResult, setLastResult] = useState('none');

  return (
    <div>
      <span data-testid="state">
        {state ? `${state.liked ? 'liked' : 'unliked'}:${state.like_count}` : 'none'}
      </span>
      <span data-testid="pending">{pending ? 'pending' : 'idle'}</span>
      <span data-testid="result">{lastResult}</span>
      <button
        data-testid="toggle"
        onClick={() => { void toggle().then((r) => setLastResult(r === null ? 'null' : String(r))); }}
      >
        toggle
      </button>
    </div>
  );
}

describe('useTaskLike（工单点赞状态）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockResolvedValue({ liked: false, like_count: 186 });
  });

  it('挂载时拉一次状态并展示', async () => {
    render(<Harness taskId={5} />);

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:186'));
    expect(mockFetch).toHaveBeenCalledWith(5);
  });

  it('taskId 为空时不请求，state 保持 none', () => {
    render(<Harness taskId={null} />);

    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.getByTestId('state')).toHaveTextContent('none');
  });

  it('拉取失败静默降级：state 保持 none（不渲染点赞按钮）', async () => {
    mockFetch.mockRejectedValue(new Error('boom'));
    render(<Harness taskId={5} />);

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    expect(screen.getByTestId('state')).toHaveTextContent('none');
  });

  it('点赞先乐观 +1，成功后以服务端返回的计数为准', async () => {
    let resolveToggle!: (v: TaskLikeState | null) => void;
    mockToggle.mockImplementation(() => new Promise((resolve) => { resolveToggle = resolve; }));

    render(<Harness taskId={5} />);
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:186'));

    fireEvent.click(screen.getByTestId('toggle'));
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('liked:187'));
    expect(screen.getByTestId('pending')).toHaveTextContent('pending');

    resolveToggle({ liked: true, like_count: 190 });
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('liked:190'));
    await waitFor(() => expect(screen.getByTestId('result')).toHaveTextContent('true'));
  });

  it('点赞失败：回滚原状态，toggle 返回 null', async () => {
    mockToggle.mockResolvedValue(null);
    render(<Harness taskId={5} />);
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:186'));

    fireEvent.click(screen.getByTestId('toggle'));

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:186'));
    expect(screen.getByTestId('result')).toHaveTextContent('null');
  });

  it('pending 期间重复点击只提交一次', async () => {
    let resolveToggle!: (v: TaskLikeState | null) => void;
    mockToggle.mockImplementation(() => new Promise((resolve) => { resolveToggle = resolve; }));

    render(<Harness taskId={5} />);
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:186'));

    fireEvent.click(screen.getByTestId('toggle'));
    fireEvent.click(screen.getByTestId('toggle'));
    fireEvent.click(screen.getByTestId('toggle'));

    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('liked:187'));
    expect(mockToggle).toHaveBeenCalledTimes(1);

    resolveToggle({ liked: true, like_count: 187 });
    await waitFor(() => expect(screen.getByTestId('pending')).toHaveTextContent('idle'));
  });

  it('取消点赞：计数 -1，方向为取消', async () => {
    mockFetch.mockResolvedValue({ liked: true, like_count: 200 });
    mockToggle.mockResolvedValue({ liked: false, like_count: 199 });

    render(<Harness taskId={5} />);
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('liked:200'));

    fireEvent.click(screen.getByTestId('toggle'));

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith(5, false));
    await waitFor(() => expect(screen.getByTestId('state')).toHaveTextContent('unliked:199'));
    expect(screen.getByTestId('result')).toHaveTextContent('false');
  });
});
