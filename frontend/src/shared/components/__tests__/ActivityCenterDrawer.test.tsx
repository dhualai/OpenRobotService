// 活动中心抽屉：公告全文 + 精品工单 TOP 榜（已归档工单按点赞数倒序），行内可点赞/取消。
// tdesign 用轻量替身（仓库既有约定），只替 Toast；数据层两个 api 模块整体 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
}));

vi.mock('@/api/announcement', () => ({
  fetchAnnouncements: vi.fn(),
}));

vi.mock('@/api/taskLike', () => ({
  fetchFeaturedTickets: vi.fn(),
  toggleTaskLike: vi.fn(),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

import { Toast } from 'tdesign-mobile-react';
import ActivityCenterDrawer from '../ActivityCenterDrawer';
import { fetchAnnouncements } from '@/api/announcement';
import { fetchFeaturedTickets, toggleTaskLike } from '@/api/taskLike';
import type { FeaturedTicket } from '@/api/taskLike';

const mockFetchAnnouncements = vi.mocked(fetchAnnouncements);
const mockFetchFeatured = vi.mocked(fetchFeaturedTickets);
const mockToggle = vi.mocked(toggleTaskLike);
const mockToast = vi.mocked(Toast);

const ANNOUNCEMENT = {
  id: 1,
  title: '双十一活动开启',
  content: '点赞精品工单即可参与抽奖。\n活动时间：10.15 - 10.31',
  is_pinned: true,
  status: 'published' as const,
  published_at: '2026-10-15T10:00:00',
  created_by: 'admin',
};

const TICKET: FeaturedTicket = {
  id: 5,
  title: '登录接口偶发 500',
  status: 'archived',
  like_count: 186,
  is_liked: false,
  project_name: '摇人吧',
  created_by: 'u1',
  created_at: null,
  archived_at: null,
};

const renderDrawer = (visible = true) => render(
  <ActivityCenterDrawer visible={visible} onClose={vi.fn()} />,
);

describe('ActivityCenterDrawer（活动中心抽屉）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchAnnouncements.mockResolvedValue({ total: 1, items: [ANNOUNCEMENT] });
    mockFetchFeatured.mockResolvedValue([TICKET]);
    mockToggle.mockResolvedValue({ liked: true, like_count: 187 });
  });

  it('visible=false 时不拉数据，抽屉对辅助技术隐藏', () => {
    const { container } = renderDrawer(false);

    expect(mockFetchAnnouncements).not.toHaveBeenCalled();
    expect(mockFetchFeatured).not.toHaveBeenCalled();
    expect(container.querySelector('.activity-drawer')).toHaveAttribute('aria-hidden', 'true');
  });

  it('visible=true 时展示公告全文与精品榜（名次 + 项目名）', async () => {
    renderDrawer();

    expect(await screen.findByText('双十一活动开启')).toBeInTheDocument();
    expect(screen.getByText(/活动时间：10\.15 - 10\.31/)).toBeInTheDocument();
    expect(screen.getByText('置顶')).toBeInTheDocument();
    expect(screen.getByText('登录接口偶发 500')).toBeInTheDocument();
    expect(screen.getByText('摇人吧')).toBeInTheDocument();
    expect(screen.getByLabelText('第 1 名')).toBeInTheDocument();
    expect(screen.getByTestId('like-count-5')).toHaveTextContent('186');
  });

  it('一段失败不影响另一段：公告挂了榜单照常展示', async () => {
    mockFetchAnnouncements.mockRejectedValue(new Error('boom'));
    renderDrawer();

    expect(await screen.findByText('登录接口偶发 500')).toBeInTheDocument();
    expect(screen.getByText('暂无公告')).toBeInTheDocument();
  });

  it('点赞：乐观 +1 并以服务端返回为准', async () => {
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByRole('button', { name: '点赞' }));

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith(5, true));
    await waitFor(() => expect(screen.getByTestId('like-count-5')).toHaveTextContent('187'));
    expect(screen.getByRole('button', { name: '取消点赞' })).toBeInTheDocument();
  });

  it('取消点赞：计数 -1，且按取消方向调接口', async () => {
    mockFetchFeatured.mockResolvedValue([{ ...TICKET, is_liked: true, like_count: 200 }]);
    mockToggle.mockResolvedValue({ liked: false, like_count: 199 });
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByRole('button', { name: '取消点赞' }));

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith(5, false));
    await waitFor(() => expect(screen.getByTestId('like-count-5')).toHaveTextContent('199'));
  });

  it('点赞失败：回滚到原计数并提示', async () => {
    mockToggle.mockResolvedValue(null);
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByRole('button', { name: '点赞' }));

    await waitFor(() => expect(mockToast).toHaveBeenCalled());
    expect(screen.getByTestId('like-count-5')).toHaveTextContent('186');
    expect(screen.getByRole('button', { name: '点赞' })).toBeInTheDocument();
  });

  it('点工单条目跳详情并关闭抽屉', async () => {
    const onClose = vi.fn();
    render(<ActivityCenterDrawer visible onClose={onClose} />);
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByText('登录接口偶发 500'));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/tasks/5');
  });
});
