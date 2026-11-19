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

function mockTitleSize(dimensions: { viewport: number; content: number }) {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('announcement-bar__title') ? dimensions.viewport : 0;
  });
  vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockImplementation(function (this: HTMLElement) {
    return this.classList.contains('announcement-bar__title-text') ? dimensions.content : 0;
  });
}

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
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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

  it('高价值问题长标题横向滚动到结尾后才切换，点击仍打开活动中心', async () => {
    vi.useFakeTimers();
    mockTitleSize({ viewport: 160, content: 760 });
    api.fetchAnnouncement.mockResolvedValue(ANNOUNCEMENT);
    const title = '机器人长距离运行后定位漂移、路径偏移以及恢复定位流程的完整排查问题';
    api.fetchChallenges.mockResolvedValue({ items: [{ title }], total: 1 });
    const onOpen = vi.fn();
    await act(async () => { render(<AnnouncementBar onOpen={onOpen} />); });
    act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS); });
    const content = screen.getByText(title);
    expect(content.parentElement).toHaveClass('announcement-bar__title--scrolling');
    expect(content.style.getPropertyValue('--announcement-scroll-distance')).toBe('-600px');
    expect(content.style.getPropertyValue('--announcement-scroll-duration')).toBe('15000ms');
    expect(content.style.getPropertyValue('--announcement-scroll-delay')).toBe('1000ms');
    expect(screen.getByRole('button')).toHaveAccessibleName(`高价值问题：${title}，点击查看活动中心`);
    act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS); });
    expect(screen.getByText(title)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button'));
    expect(onOpen).toHaveBeenCalledTimes(1);
    act(() => { vi.advanceTimersByTime(12999); });
    expect(screen.getByText(title)).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.getByRole('button')).toHaveAccessibleName(/公告：双十一活动开启/);
  });

  it('不溢出的高价值问题保持原来的四秒轮播', async () => {
    vi.useFakeTimers();
    mockTitleSize({ viewport: 160, content: 160 });
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '短问题甲' }, { title: '短问题乙' }], total: 2 });
    await act(async () => { render(<AnnouncementBar onOpen={vi.fn()} />); });
    expect(screen.getByText('短问题甲').parentElement).not.toHaveClass('announcement-bar__title--scrolling');
    act(() => { vi.advanceTimersByTime(ACTIVITY_ROTATION_MS); });
    expect(screen.getByRole('button')).toHaveAccessibleName('高价值问题：短问题乙，点击查看活动中心');
  });

  it('只有一个长问题时重复滚动，卸载时清理定时器和尺寸监听', async () => {
    vi.useFakeTimers();
    mockTitleSize({ viewport: 160, content: 760 });
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', vi.fn().mockImplementation(() => ({ observe, disconnect })));
    const title = '唯一一个需要完整滚动展示的高价值问题';
    api.fetchChallenges.mockResolvedValue({ items: [{ title }], total: 1 });
    const view = await act(async () => render(<AnnouncementBar onOpen={vi.fn()} />));
    const firstContent = screen.getByText(title);
    expect(firstContent.parentElement).toHaveClass('announcement-bar__title--scrolling');
    expect(observe).toHaveBeenCalledWith(firstContent);
    expect(observe).toHaveBeenCalledWith(firstContent.parentElement);
    act(() => { vi.advanceTimersByTime(17000); });
    expect(screen.getByText(title)).not.toBe(firstContent);
    expect(screen.getByText(title).parentElement).toHaveClass('announcement-bar__title--scrolling');
    expect(vi.getTimerCount()).toBe(1);
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
    expect(disconnect).toHaveBeenCalled();
  });

  it('窗口宽度变化时重新计算滚动，能完整容纳标题后停止滚动', async () => {
    vi.useFakeTimers();
    const dimensions = { viewport: 160, content: 760 };
    mockTitleSize(dimensions);
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '随屏幕尺寸重新测量的高价值问题' }], total: 1 });
    await act(async () => { render(<AnnouncementBar onOpen={vi.fn()} />); });
    const content = screen.getByText('随屏幕尺寸重新测量的高价值问题');
    expect(content.style.getPropertyValue('--announcement-scroll-distance')).toBe('-600px');
    dimensions.viewport = 360;
    fireEvent(window, new Event('resize'));
    expect(content.style.getPropertyValue('--announcement-scroll-distance')).toBe('-400px');
    expect(content.style.getPropertyValue('--announcement-scroll-duration')).toBe('10000ms');
    dimensions.viewport = 800;
    fireEvent(window, new Event('resize'));
    expect(content.parentElement).not.toHaveClass('announcement-bar__title--scrolling');
    expect(content.style.getPropertyValue('--announcement-scroll-distance')).toBe('');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('标题容器隐藏时不启动无效滚动，恢复显示后重新测量', async () => {
    vi.useFakeTimers();
    const dimensions = { viewport: 0, content: 760 };
    mockTitleSize(dimensions);
    api.fetchChallenges.mockResolvedValue({ items: [{ title: '恢复显示后滚动的高价值问题' }], total: 1 });
    await act(async () => { render(<AnnouncementBar onOpen={vi.fn()} />); });
    const content = screen.getByText('恢复显示后滚动的高价值问题');
    expect(content.parentElement).not.toHaveClass('announcement-bar__title--scrolling');
    expect(vi.getTimerCount()).toBe(0);
    dimensions.viewport = 160;
    fireEvent(window, new Event('resize'));
    expect(content.parentElement).toHaveClass('announcement-bar__title--scrolling');
    expect(vi.getTimerCount()).toBe(1);
  });
});
