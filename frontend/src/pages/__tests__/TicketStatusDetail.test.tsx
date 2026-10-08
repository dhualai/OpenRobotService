import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const mockFetchTickets = vi.fn();
vi.mock('@/api/dashboard', () => ({
  fetchTicketsByStatus: (...args: unknown[]) => mockFetchTickets(...args),
}));

const authState = vi.hoisted(() => ({ projectIds: [] as string[], canViewAll: true }));
vi.mock('@/stores/auth', () => ({
  PERMISSION_VIEW_ALL: 'frontend:admin:dashboard:view-all',
  useAuthStore: () => ({ projectIds: authState.projectIds, hasPermission: () => authState.canViewAll }),
}));

vi.mock('@/shared/utils/wechatJsSdk', () => ({
  navigateInWechat: vi.fn(),
}));

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <nav>{title}</nav>,
  Loading: ({ text }: { text?: string }) => <div>{text}</div>,
  Button: ({ children, disabled, onClick }: ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button disabled={disabled} onClick={onClick}>{children}</button>
  ),
}));

import TicketStatusDetail from '../admin/TicketStatusDetail';

const HOUR = 3600_000;
const DAY = 24 * HOUR;

/** 后端 naive UTC 口径（无时区后缀的 ISO），与 parseBackendDayjs 的解析约定一致 */
const agoNaive = (ms: number) => new Date(Date.now() - ms).toISOString().replace('Z', '');

// 接口已按「挂起置顶 → 其余超时最久在前」排好序（见后端 task_dashboard_service），
// 前端只按序渲染、不再重排，故这里的数组顺序即期望的渲染顺序。
const ITEMS = [
  { id: '1', title: '挂起-最久', status: 'pending', priority: 'high', created_at: agoNaive(5 * DAY), deadline_at: agoNaive(4 * DAY) },
  { id: '2', title: '挂起-次久', status: 'pending', priority: 'high', created_at: agoNaive(4 * DAY), deadline_at: agoNaive(2 * DAY) },
  { id: '3', title: '处理中-最久', status: 'in_progress', priority: 'high', created_at: agoNaive(3 * DAY), deadline_at: agoNaive(3 * DAY) },
  { id: '4', title: '处理中-较久', status: 'in_progress', priority: 'high', created_at: agoNaive(2 * DAY), deadline_at: agoNaive(2 * HOUR) },
];

/** 由标题文字取整张卡片：span → 标题行 → 卡片 */
const cardOf = (title: string): HTMLElement =>
  screen.getByText(title).parentElement!.parentElement as HTMLElement;

const renderView = (status = 'overdue') =>
  render(
    <MemoryRouter initialEntries={[`/admin/dashboard/tickets/${status}`]}>
      <Routes>
        <Route path="/admin/dashboard/tickets/:status" element={<TicketStatusDetail />} />
        <Route path="/admin/project-detail/:id/tickets/:status" element={<TicketStatusDetail />} />
      </Routes>
    </MemoryRouter>,
  );

/** 项目工单卡三格的下钻入口：路径上带项目 id */
const renderProjectView = (status = 'overdue') =>
  render(
    <MemoryRouter initialEntries={[`/admin/project-detail/P-001/tickets/${status}`]}>
      <Routes>
        <Route path="/admin/project-detail/:id/tickets/:status" element={<TicketStatusDetail />} />
      </Routes>
    </MemoryRouter>,
  );

describe('TicketStatusDetail · 超时工单列表', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.projectIds = [];
    authState.canViewAll = true;
    mockFetchTickets.mockResolvedValue({ items: ITEMS, total: ITEMS.length });
  });

  it('按接口顺序渲染：挂起置顶，其余超时最久在前', async () => {
    renderView();
    await screen.findByText('挂起-最久');

    const els = ['挂起-最久', '挂起-次久', '处理中-最久', '处理中-较久'].map((t) => screen.getByText(t));
    els.slice(1).forEach((el, i) => {
      expect(els[i].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
  });

  it('挂起工单：左侧色条 + 状态标签，与普通工单区分', async () => {
    renderView();
    await screen.findByText('挂起-最久');

    const card = cardOf('挂起-最久');
    // 色值含 var()，只有简写属性能原样读回，长写属性（borderLeftWidth 等）在 jsdom 下为空
    expect(card.style.borderLeft).toBe('4px solid var(--mac-status-3)');
    // 色条/底纹取设计系统的马卡龙蓝阶（暂停/挂起 = status-3），不是旧的橘黄 #e37318
    expect(card.style.background).toContain('var(--mac-status-3)');
    expect(card.getAttribute('style')).not.toContain('e37318');
    // 颜色之外还有文字标签：颜色不是唯一信号
    expect(screen.getAllByText('暂停/挂起')).toHaveLength(2); // 两条挂起工单
  });

  it('非挂起工单：无左侧色条、无挂起标签', async () => {
    renderView();
    await screen.findByText('处理中-最久');

    expect(cardOf('处理中-最久').style.borderLeft).toBe('');
    expect(cardOf('处理中-较久').style.borderLeft).toBe('');
  });

  it('标注已超时时长（超时最久的排在前面）', async () => {
    renderView();
    await screen.findByText('挂起-最久');

    // 文案两边不留空格：RTL 默认对元素文本做 trim + 空白折叠
    expect(screen.getByText('· 已超时 4天')).toBeInTheDocument();
    expect(screen.getByText('· 已超时 2天')).toBeInTheDocument();
    expect(screen.getByText('· 已超时 3天')).toBeInTheDocument();
    expect(screen.getByText('· 已超时 2小时')).toBeInTheDocument();
  });

  // —— 项目工单卡三格的下钻（同一个页面的第二条入口） ——

  it('从项目工单卡进来：请求把范围收窄成这一个项目', async () => {
    renderProjectView('overdue');
    await screen.findByText('挂起-最久');
    // 即便这个用户能看全部（canViewAll=true），项目卡下钻也只列这一个项目
    expect(mockFetchTickets).toHaveBeenCalledWith('overdue', ['P-001'], { skip: 0, limit: 20 });
  });

  it('标题用项目卡自己的词（总工单/正在处理/超期工单），与点进来的格子对得上', async () => {
    const { unmount } = renderProjectView('pending');
    expect(await screen.findByText('正在处理 · 工单明细')).toBeInTheDocument();
    unmount();

    renderProjectView('all');
    expect(await screen.findByText('总工单 · 工单明细')).toBeInTheDocument();
  });

  it('仪表盘入口不受影响：仍按「能看全部 = 不过滤」的原口径请求', async () => {
    renderView();
    await screen.findByText('挂起-最久');
    expect(mockFetchTickets).toHaveBeenCalledWith('overdue', undefined, { skip: 0, limit: 20 });
    expect(screen.getByText('超时工单 · 工单明细')).toBeInTheDocument();
  });

  it.each(['all', 'pending', 'overdue'])('%s 超过20条时可以继续加载到统计总数', async (status) => {
    const tickets = Array.from({ length: 25 }, (_, index) => ({
      ...ITEMS[0], id: String(index + 1), title: `工单-${index + 1}`,
    }));
    mockFetchTickets
      .mockResolvedValueOnce({ items: tickets.slice(0, 20), total: 25 })
      .mockResolvedValueOnce({ items: tickets.slice(20), total: 25 });

    renderView(status);
    expect(await screen.findByText('共 25 条，已加载 20 条')).toBeInTheDocument();
    expect(screen.queryByText('工单-21')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));

    expect(await screen.findByText('工单-25')).toBeInTheDocument();
    expect(screen.getByText('工单-1')).toBeInTheDocument();
    expect(mockFetchTickets).toHaveBeenLastCalledWith(status, undefined, { skip: 20, limit: 20 });
    expect(screen.getByText('共 25 条')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
  });

  it('项目范围在加载下一页时保持不变', async () => {
    mockFetchTickets
      .mockResolvedValueOnce({ items: ITEMS, total: 5 })
      .mockResolvedValueOnce({ items: [{ ...ITEMS[0], id: '5', title: '项目最后一条' }], total: 5 });
    renderProjectView('all');
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    await waitFor(() => expect(mockFetchTickets).toHaveBeenLastCalledWith(
      'all', ['P-001'], { skip: 4, limit: 20 },
    ));
    expect(await screen.findByText('项目最后一条')).toBeInTheDocument();
  });

  it('不能看全部的用户仅加载关联项目工单', async () => {
    authState.canViewAll = false;
    authState.projectIds = ['P-001', 'P-002'];
    renderView('all');
    await screen.findByText('挂起-最久');
    expect(mockFetchTickets).toHaveBeenCalledWith('all', authState.projectIds, { skip: 0, limit: 20 });
  });

  it('加载更多失败时保留已有工单和总数，重试同一页', async () => {
    mockFetchTickets
      .mockResolvedValueOnce({ items: ITEMS, total: 5 })
      .mockRejectedValueOnce(new Error('network error'))
      .mockResolvedValueOnce({ items: [{ ...ITEMS[0], id: '5', title: '最后一条' }], total: 5 });

    renderView();
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('工单列表加载失败，请重试');
    expect(screen.getByText('共 5 条，已加载 4 条')).toBeInTheDocument();
    expect(screen.getByText('挂起-最久')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('最后一条')).toBeInTheDocument();
    expect(mockFetchTickets).toHaveBeenLastCalledWith('overdue', undefined, { skip: 4, limit: 20 });
  });

  it('首次加载失败提示重试，而不是显示暂无数据', async () => {
    mockFetchTickets.mockRejectedValueOnce(new Error('network error'));
    renderView();
    expect(await screen.findByText('工单列表加载失败，请重试')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('挂起-最久')).toBeInTheDocument();
  });

  it.each(['all', 'pending', 'overdue'])('%s 后端重复返回第一页时不追加重复工单', async (status) => {
    const tickets = Array.from({ length: 25 }, (_, index) => ({
      ...ITEMS[0], id: String(index + 1), title: `工单-${index + 1}`,
    }));
    mockFetchTickets
      .mockResolvedValueOnce({ items: tickets.slice(0, 20), total: 25 })
      .mockResolvedValueOnce({ items: tickets.slice(0, 20), total: 25 })
      .mockResolvedValueOnce({ items: tickets.slice(20), total: 25 });

    renderView(status);
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请确认后端已更新并重启');
    expect(screen.getAllByText('工单-1')).toHaveLength(1);
    expect(screen.getByText('共 25 条，已加载 20 条')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(await screen.findByText('工单-25')).toBeInTheDocument();
    expect(mockFetchTickets).toHaveBeenLastCalledWith(status, undefined, { skip: 20, limit: 20 });
    expect(screen.getByText('共 25 条')).toBeInTheDocument();
  });

  it('分页部分重叠时按工单ID去重，下一页偏移量仍按原始返回条数推进', async () => {
    const tickets = Array.from({ length: 24 }, (_, index) => ({
      ...ITEMS[0], id: String(index + 1), title: `工单-${index + 1}`,
    }));
    mockFetchTickets
      .mockResolvedValueOnce({ items: tickets.slice(0, 20), total: 24 })
      .mockResolvedValueOnce({ items: tickets.slice(19, 22), total: 24 })
      .mockResolvedValueOnce({ items: tickets.slice(22), total: 24 });

    renderView();
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByText('共 24 条，已加载 22 条')).toBeInTheDocument();
    expect(screen.getAllByText('工单-20')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(await screen.findByText('工单-24')).toBeInTheDocument();
    expect(mockFetchTickets).toHaveBeenLastCalledWith('overdue', undefined, { skip: 23, limit: 20 });
  });

  it('后端返回空页但仍有未加载工单时提示异常并允许重试原页', async () => {
    mockFetchTickets
      .mockResolvedValueOnce({ items: ITEMS, total: 5 })
      .mockResolvedValueOnce({ items: [], total: 5 });
    renderView();
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('未获取到新的工单');
    expect(screen.getByText('共 5 条，已加载 4 条')).toBeInTheDocument();
    expect(mockFetchTickets).toHaveBeenLastCalledWith('overdue', undefined, { skip: 4, limit: 20 });
  });
});
