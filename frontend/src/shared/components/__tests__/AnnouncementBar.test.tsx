import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  fetchAnnouncement: vi.fn(), fetchOverview: vi.fn(), fetchQuestions: vi.fn(), fetchChallenges: vi.fn(),
}));

vi.mock('@/api/announcement', () => ({
  fetchActiveAnnouncement: api.fetchAnnouncement,
}));
vi.mock('@/api/knowledge', () => ({ fetchKnowledgeOverview: api.fetchOverview, fetchKnowledgeQuestions: api.fetchQuestions }));
vi.mock('@/api/knowledgeChallenges', () => ({ fetchChallenges: api.fetchChallenges }));

import AnnouncementBar, { ACTIVITY_ROTATION_MS } from '../AnnouncementBar';

const ANNOUNCEMENT = {
  id: 1,
  title: '双十一活动开启，点赞冲榜赢好礼',
  content: '正文第一行\n正文第二行',
  is_pinned: true,
  status: 'published' as const,
  published_at: '2026-10-15T10:00:00',
  created_by: 'admin',
};

describe('AnnouncementBar（首页公告条）', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.fetchAnnouncement.mockResolvedValue(null);
    api.fetchOverview.mockResolvedValue({ month: '2026-11' });
    api.fetchQuestions.mockResolvedValue({ items: [], total: 0 });
    api.fetchChallenges.mockResolvedValue({ items: [], total: 0 });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('有生效公告时展示标题，点击打开活动中心', async () => {
    api.fetchAnnouncement.mockResolvedValue(ANNOUNCEMENT);
    const onOpen = vi.fn();
    render(<AnnouncementBar onOpen={onOpen} />);

    expect(await screen.findByText('双十一活动开启，点赞冲榜赢好礼')).toBeInTheDocument();
    expect(screen.getByText('活动中心')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /双十一活动开启/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('公告、优质问题和高价值问题都为空时不占位', async () => {
    const { container } = render(<AnnouncementBar onOpen={vi.fn()} />);

    await waitFor(() => expect(api.fetchQuestions).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
  });

  it('所有接口失败时静默降级，不渲染、不抛错', async () => {
    api.fetchAnnouncement.mockRejectedValue(new Error('network down'));
    api.fetchOverview.mockRejectedValue(new Error('network down'));
    api.fetchQuestions.mockRejectedValue(new Error('network down'));
    api.fetchChallenges.mockRejectedValue(new Error('network down'));
    const { container } = render(<AnnouncementBar onOpen={vi.fn()} />);

    await waitFor(() => expect(api.fetchQuestions).toHaveBeenCalledWith('', 'likes', 1, 10));
    expect(container).toBeEmptyDOMElement();
  });

  it('公告、优质问题、高价值问题按具体标题交替轮播并循环，点击仍打开活动中心', async () => {
    vi.useFakeTimers();
    api.fetchAnnouncement.mockResolvedValue(ANNOUNCEMENT);
    api.fetchQuestions.mockResolvedValue({ items: [{ title: '优质问题甲' }, { title: '优质问题乙' }], total: 2 });
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '高价值问题甲' }, { title: '高价值问题乙' }], total: 2 });
    const onOpen = vi.fn();
    await act(async () => { render(<AnnouncementBar onOpen={onOpen} />); });
    expect(screen.getByRole('button')).toHaveAccessibleName(/公告：双十一活动开启/);
    for (const title of ['优质问题：优质问题甲', '高价值问题：高价值问题甲', '优质问题：优质问题乙', '高价值问题：高价值问题乙', '公告：双十一活动开启']) {
      act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS); });
      expect(screen.getByRole('button').getAttribute('aria-label')).toContain(title);
    }
    fireEvent.click(screen.getByRole('button'));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(api.fetchQuestions).toHaveBeenCalledWith('2026-11', 'likes', 1, 10);
    expect(api.fetchChallenges).toHaveBeenCalledWith('active', 1);
  });

  it('没有公告或高价值问题时只展示优质问题，不显示空类别', async () => {
    vi.useFakeTimers();
    api.fetchQuestions.mockResolvedValue({ items: [{ title: '只有优质问题' }], total: 1 });
    await act(async () => { render(<AnnouncementBar onOpen={vi.fn()} />); });
    expect(screen.getByRole('button')).toHaveAccessibleName('优质问题：只有优质问题，点击查看活动中心');
    act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS * 4); });
    expect(screen.getByRole('button')).toHaveAccessibleName('优质问题：只有优质问题，点击查看活动中心');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('知识榜加载挂起不阻塞其他类别，空白标题不加入轮播', async () => {
    api.fetchOverview.mockReturnValue(new Promise(() => {}));
    api.fetchAnnouncement.mockResolvedValue({ ...ANNOUNCEMENT, title: '  ' });
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '' }, { title: '等待解答的高价值问题' }], total: 2 });
    render(<AnnouncementBar onOpen={vi.fn()} />);
    expect(await screen.findByRole('button')).toHaveAccessibleName('高价值问题：等待解答的高价值问题，点击查看活动中心');
    expect(screen.queryByText('公告')).toBeNull();
  });

  it('某个类别失败仍轮播其他类别，卸载时清理轮播定时器', async () => {
    vi.useFakeTimers();
    api.fetchAnnouncement.mockResolvedValue(ANNOUNCEMENT);
    api.fetchQuestions.mockRejectedValue(new Error('榜单暂不可用'));
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '高价值问题' }], total: 1 });
    const view = await act(async () => render(<AnnouncementBar onOpen={vi.fn()} />));
    expect(vi.getTimerCount()).toBe(1);
    act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS); });
    expect(screen.getByRole('button')).toHaveAccessibleName('高价值问题：高价值问题，点击查看活动中心');
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
