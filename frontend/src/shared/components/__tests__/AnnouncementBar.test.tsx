// 首页公告条：只常驻一条生效公告，点击打开活动中心；无公告/拉取失败都要「不渲染」。
// 拉取失败静默降级是刻意设计（公告条是增强项，不能因它打断首页对话），故单列一条用例钉住。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('@/api/announcement', () => ({
  fetchActiveAnnouncement: vi.fn(),
}));

import AnnouncementBar from '../AnnouncementBar';
import { fetchActiveAnnouncement } from '@/api/announcement';

const mockFetch = vi.mocked(fetchActiveAnnouncement);

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
    vi.clearAllMocks();
  });

  it('有生效公告时展示标题，点击打开活动中心', async () => {
    mockFetch.mockResolvedValue(ANNOUNCEMENT);
    const onOpen = vi.fn();
    render(<AnnouncementBar onOpen={onOpen} />);

    expect(await screen.findByText('双十一活动开启，点赞冲榜赢好礼')).toBeInTheDocument();
    expect(screen.getByText('活动中心')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /双十一活动开启/ }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it('没有生效公告（接口返回 null）时不渲染任何内容', async () => {
    mockFetch.mockResolvedValue(null);
    const { container } = render(<AnnouncementBar onOpen={vi.fn()} />);

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
  });

  it('接口失败时静默降级：不渲染、不抛错', async () => {
    mockFetch.mockRejectedValue(new Error('network down'));
    const { container } = render(<AnnouncementBar onOpen={vi.fn()} />);

    await waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1));
    expect(container).toBeEmptyDOMElement();
  });
});
