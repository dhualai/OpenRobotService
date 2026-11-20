// 后台「公告管理」：权限门禁、列表加载、新建（校验 + 提交）、发布/下线、删除确认。
// tdesign 用轻量替身（仓库既有约定）；数据层整体 mock，断言落在「调了哪个接口、传了什么」。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <nav data-testid="navbar">{title}</nav>,
  Loading: ({ text }: { text?: ReactNode }) => <div data-testid="loading">{text}</div>,
  Button: ({ children, onClick, disabled, loading }: {
    children?: ReactNode; onClick?: () => void; disabled?: boolean; loading?: boolean;
  }) => (
    <button disabled={disabled} onClick={() => { if (!disabled) onClick?.(); }}>
      {loading ? 'loading' : children}
    </button>
  ),
  Tag: ({ children }: { children?: ReactNode }) => <span>{children}</span>,
  Switch: ({ value, onChange }: { value?: boolean; onChange?: (v: boolean) => void }) => (
    <button data-testid="pin-switch" onClick={() => onChange?.(!value)}>{value ? 'on' : 'off'}</button>
  ),
  Textarea: ({ value, onChange, placeholder }: {
    value?: string; onChange?: (v: unknown) => void; placeholder?: string;
  }) => (
    <textarea
      value={value ?? ''}
      placeholder={placeholder}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
  Popup: ({ visible, children }: { visible?: boolean; children?: ReactNode }) => (
    visible ? <div data-testid="editor-popup">{children}</div> : null
  ),
  Dialog: ({ visible, children, onConfirm, confirmBtn }: {
    visible?: boolean; children?: ReactNode; onConfirm?: () => void; confirmBtn?: { content?: ReactNode };
  }) => (
    visible ? (
      <div data-testid="delete-dialog">
        <div>{children}</div>
        <button onClick={onConfirm}>{confirmBtn?.content ?? '确认'}</button>
      </div>
    ) : null
  ),
  Toast: vi.fn(),
}));

vi.mock('@/api/announcement', () => ({
  PERM_ANNOUNCEMENT: 'frontend:admin:announcement:manage',
  ANNOUNCEMENT_STATUS_LABELS: { draft: '草稿', published: '已发布', offline: '已下线' },
  adminListAnnouncements: vi.fn(),
  adminCreateAnnouncement: vi.fn(),
  adminUpdateAnnouncement: vi.fn(),
  adminPublishAnnouncement: vi.fn(),
  adminOfflineAnnouncement: vi.fn(),
  adminDeleteAnnouncement: vi.fn(),
}));

import { Toast } from 'tdesign-mobile-react';
import AnnouncementManage from '../admin/AnnouncementManage';
import {
  adminCreateAnnouncement,
  adminDeleteAnnouncement,
  adminListAnnouncements,
  adminOfflineAnnouncement,
  adminPublishAnnouncement,
  type Announcement,
} from '@/api/announcement';
import { useAuthStore } from '@/stores/auth';

const mockList = vi.mocked(adminListAnnouncements);
const mockCreate = vi.mocked(adminCreateAnnouncement);
const mockOffline = vi.mocked(adminOfflineAnnouncement);
const mockPublish = vi.mocked(adminPublishAnnouncement);
const mockDelete = vi.mocked(adminDeleteAnnouncement);
const mockToast = vi.mocked(Toast);

const PUBLISHED: Announcement = {
  id: 1,
  title: '双十一活动开启',
  content: '点赞精品工单即可参与抽奖。',
  is_pinned: true,
  status: 'published',
  published_at: '2026-10-15T10:00:00',
  created_by: 'admin',
};

const DRAFT: Announcement = {
  id: 2,
  title: '草稿：内测公告',
  content: '仅内部可见',
  is_pinned: false,
  status: 'draft',
  published_at: null,
  created_by: 'admin',
};

const renderPage = () => render(
  <MemoryRouter initialEntries={['/admin/announcements']}>
    <AnnouncementManage />
  </MemoryRouter>,
);

describe('AnnouncementManage（后台公告管理）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.setState({ username: 'admin', permissions: ['admin'] });
    mockList.mockResolvedValue({ total: 2, items: [PUBLISHED, DRAFT] });
    mockCreate.mockResolvedValue({ ...PUBLISHED, id: 3, title: '新公告', status: 'draft' });
    mockPublish.mockResolvedValue({ ...DRAFT, status: 'published' });
    mockOffline.mockResolvedValue({ ...PUBLISHED, status: 'offline' });
    mockDelete.mockResolvedValue(true);
  });

  it('无权限时给出提示且不请求列表', () => {
    useAuthStore.setState({ permissions: [] });
    renderPage();

    expect(screen.getByText('您没有权限访问此页面')).toBeInTheDocument();
    expect(mockList).not.toHaveBeenCalled();
  });

  it('有权限时加载列表并展示标题与状态', async () => {
    renderPage();

    expect(await screen.findByText('双十一活动开启')).toBeInTheDocument();
    expect(screen.getByText('草稿：内测公告')).toBeInTheDocument();
    // 状态标签与筛选下拉里的同名文案要区分：断言收进各自卡片（section 上的 testid）
    expect(within(screen.getByTestId('ann-card-1')).getByText('已发布')).toBeInTheDocument();
    expect(within(screen.getByTestId('ann-card-1')).getByText('置顶')).toBeInTheDocument();
    expect(within(screen.getByTestId('ann-card-2')).getByText('草稿')).toBeInTheDocument();
  });

  it('新建：填好标题正文 + 置顶后保存，调创建接口并刷新列表', async () => {
    renderPage();
    await screen.findByText('双十一活动开启');
    const listCallsBefore = mockList.mock.calls.length;

    fireEvent.click(screen.getByText('新建'));
    expect(screen.getByTestId('editor-popup')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('最多 200 字'), { target: { value: '新公告' } });
    fireEvent.change(screen.getByPlaceholderText('支持换行'), { target: { value: '正文内容' } });
    fireEvent.click(screen.getByTestId('pin-switch'));
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockCreate).toHaveBeenCalledWith({
      title: '新公告',
      content: '正文内容',
      is_pinned: true,
      status: 'draft',
    }));
    await waitFor(() => expect(mockList.mock.calls.length).toBeGreaterThan(listCallsBefore));
    // 保存成功后弹层收起
    expect(screen.queryByTestId('editor-popup')).not.toBeInTheDocument();
  });

  it('新建时标题为空：提示且不提交', async () => {
    renderPage();
    await screen.findByText('双十一活动开启');

    fireEvent.click(screen.getByText('新建'));
    fireEvent.change(screen.getByPlaceholderText('支持换行'), { target: { value: '只有正文' } });
    fireEvent.click(screen.getByText('保存'));

    await waitFor(() => expect(mockToast).toHaveBeenCalled());
    expect(mockCreate).not.toHaveBeenCalled();
  });

  it('已发布走「下线」、草稿走「发布」，各调对应接口', async () => {
    renderPage();
    await screen.findByText('双十一活动开启');

    fireEvent.click(screen.getByText('下线'));
    await waitFor(() => expect(mockOffline).toHaveBeenCalledWith(PUBLISHED.id));

    fireEvent.click(screen.getByText('发布'));
    await waitFor(() => expect(mockPublish).toHaveBeenCalledWith(DRAFT.id));
  });

  it('删除：确认后调删除接口', async () => {
    renderPage();
    await screen.findByText('双十一活动开启');

    const deleteButtons = screen.getAllByText('删除');
    fireEvent.click(deleteButtons[0]);
    expect(screen.getByTestId('delete-dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByText('删除', { selector: '[data-testid="delete-dialog"] button' }));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith(PUBLISHED.id));
  });
});
