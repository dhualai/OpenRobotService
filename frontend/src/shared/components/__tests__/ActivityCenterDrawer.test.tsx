// 活动中心抽屉：公告全文 + 优质问题 TOP 榜。
// 榜单与后台「知识贡献榜」问题榜同源（/api/admin/knowledge/questions?sort=likes），行内点赞走知识点赞接口。
// tdesign 用轻量替身（仓库既有约定），只替 Toast；数据层两个 api 模块整体 mock。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
}));

vi.mock('@/api/announcement', () => ({
  fetchAnnouncements: vi.fn(),
}));

vi.mock('@/api/knowledge', () => ({
  fetchKnowledgeOverview: vi.fn(),
  fetchKnowledgeQuestions: vi.fn(),
  toggleKnowledgeLike: vi.fn(),
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

import { Toast } from 'tdesign-mobile-react';
import ActivityCenterDrawer from '../ActivityCenterDrawer';
import { fetchAnnouncements } from '@/api/announcement';
import { fetchKnowledgeOverview, fetchKnowledgeQuestions, toggleKnowledgeLike } from '@/api/knowledge';
import type { KnowledgeOverview, KnowledgeQuestion } from '@/api/knowledge';

const mockFetchAnnouncements = vi.mocked(fetchAnnouncements);
const mockFetchOverview = vi.mocked(fetchKnowledgeOverview);
const mockFetchQuestions = vi.mocked(fetchKnowledgeQuestions);
const mockToggle = vi.mocked(toggleKnowledgeLike);
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

const OVERVIEW: KnowledgeOverview = {
  month: '2026-10',
  months: ['2026-09', '2026-10'],
  starts_at: '2026-08-01T00:00:00',
  ends_at: '2026-12-31T23:59:59',
  campaign_status: 'active',
  settlement_status: 'live',
  top_three: [],
  can_review: false,
  rule_notice: '',
};

const QUESTION: KnowledgeQuestion = {
  task_id: 5,
  title: '登录接口偶发 500',
  problem: '偶发 500',
  content: '重启后恢复',
  review_comment: '',
  value_point: '',
  created_at: '2026-10-10T02:00:00',
  archived_at: '2026-10-12T02:00:00',
  valid_like_count: 186,
  liked_by_me: false,
  can_like: true,
  questioner: { user_id: 'u1', name: '罗昊', avatar_resource_id: null },
  answerer: { user_id: 'u2', name: '张伟', avatar_resource_id: null },
};

const renderDrawer = (visible = true) => render(
  <ActivityCenterDrawer visible={visible} onClose={vi.fn()} />,
);

describe('ActivityCenterDrawer（活动中心抽屉）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchAnnouncements.mockResolvedValue({ total: 1, items: [ANNOUNCEMENT] });
    mockFetchOverview.mockResolvedValue(OVERVIEW);
    mockFetchQuestions.mockResolvedValue({ items: [QUESTION], total: 1 });
    mockToggle.mockResolvedValue({ ...QUESTION, liked_by_me: true, valid_like_count: 187 });
  });

  it('visible=false 时不拉数据，抽屉对辅助技术隐藏', () => {
    const { container } = renderDrawer(false);

    expect(mockFetchAnnouncements).not.toHaveBeenCalled();
    expect(mockFetchQuestions).not.toHaveBeenCalled();
    expect(container.querySelector('.activity-drawer')).toHaveAttribute('aria-hidden', 'true');
  });

  it('visible=true 时展示公告全文与同源榜单（名次 + 提单接单 + 有效点赞数）', async () => {
    renderDrawer();

    expect(await screen.findByText('双十一活动开启')).toBeInTheDocument();
    expect(screen.getByText(/活动时间：10\.15 - 10\.31/)).toBeInTheDocument();
    expect(screen.getByText('置顶')).toBeInTheDocument();
    expect(await screen.findByText('登录接口偶发 500')).toBeInTheDocument();
    expect(screen.getByText('提单：罗昊 · 接单：张伟')).toBeInTheDocument();
    expect(screen.getByLabelText('第 1 名')).toBeInTheDocument();
    expect(screen.getByTestId('like-count-5')).toHaveTextContent('186');
    // 榜单必须与后台知识榜同源、同排序口径
    expect(mockFetchQuestions).toHaveBeenCalledWith('2026-10', 'likes', 1, 10);
  });

  it('切换月份后按新月份重新拉榜', async () => {
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.change(screen.getByLabelText('榜单月份'), { target: { value: '2026-09' } });

    await waitFor(() => expect(mockFetchQuestions).toHaveBeenCalledWith('2026-09', 'likes', 1, 10));
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

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith(QUESTION));
    await waitFor(() => expect(screen.getByTestId('like-count-5')).toHaveTextContent('187'));
    expect(screen.getByRole('button', { name: '取消点赞' })).toBeInTheDocument();
  });

  it('取消点赞：按取消方向调接口并以服务端返回为准', async () => {
    mockFetchQuestions.mockResolvedValue({
      items: [{ ...QUESTION, liked_by_me: true, valid_like_count: 200 }],
      total: 1,
    });
    mockToggle.mockResolvedValue({ ...QUESTION, liked_by_me: false, valid_like_count: 199 });
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByRole('button', { name: '取消点赞' }));

    await waitFor(() => expect(mockToggle).toHaveBeenCalledWith(
      expect.objectContaining({ task_id: 5, liked_by_me: true }),
    ));
    await waitFor(() => expect(screen.getByTestId('like-count-5')).toHaveTextContent('199'));
  });

  it('点赞失败：回滚到原计数并提示', async () => {
    mockToggle.mockRejectedValue(new Error('今日已点赞5个不同问题'));
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    fireEvent.click(screen.getByRole('button', { name: '点赞' }));

    await waitFor(() => expect(mockToast).toHaveBeenCalled());
    expect(screen.getByTestId('like-count-5')).toHaveTextContent('186');
    expect(screen.getByRole('button', { name: '点赞' })).toBeInTheDocument();
  });

  it('can_like=false 时按钮禁用', async () => {
    mockFetchQuestions.mockResolvedValue({
      items: [{ ...QUESTION, can_like: false }],
      total: 1,
    });
    renderDrawer();
    await screen.findByText('登录接口偶发 500');

    expect(screen.getByRole('button', { name: '当前不可点赞' })).toBeDisabled();
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
