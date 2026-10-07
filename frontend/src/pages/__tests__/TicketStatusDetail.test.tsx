import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const mockFetchTickets = vi.fn();
vi.mock('@/api/dashboard', () => ({
  fetchTicketsByStatus: (...args: unknown[]) => mockFetchTickets(...args),
}));

vi.mock('@/shared/utils/wechatJsSdk', () => ({
  navigateInWechat: vi.fn(),
}));

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <nav>{title}</nav>,
  Loading: ({ text }: { text?: string }) => <div>{text}</div>,
}));

import TicketStatusDetail from '../admin/TicketStatusDetail';
import { useAuthStore } from '@/stores/auth';

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
  screen.getAllByTestId('ticket-card').find((card) => within(card).queryByText(title)) as HTMLElement;

const renderView = () =>
  render(
    <MemoryRouter initialEntries={['/admin/dashboard/tickets/overdue']}>
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
    // 真 auth store：permissions 含 admin 即「能看全部」（与线上 hasPermission 同一套判定）
    useAuthStore.setState({ projectIds: [], permissions: ['admin'] });
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

  it('挂起工单：标记为挂起 + 状态标签，与普通工单区分', async () => {
    renderView();
    await screen.findByText('挂起-最久');

    // 挂起与否是语义（色条/色值属样式层，不在用例里断言）
    expect(cardOf('挂起-最久').getAttribute('data-suspended')).toBe('true');
    // 颜色之外还有文字标签：颜色不是唯一信号
    expect(screen.getAllByText('暂停/挂起')).toHaveLength(2); // 两条挂起工单
  });

  it('非挂起工单：不标记挂起、无挂起标签', async () => {
    renderView();
    await screen.findByText('处理中-最久');

    expect(cardOf('处理中-最久').getAttribute('data-suspended')).toBe('false');
    expect(cardOf('处理中-较久').getAttribute('data-suspended')).toBe('false');
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
    expect(mockFetchTickets).toHaveBeenCalledWith('overdue', ['P-001']);
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
    expect(mockFetchTickets).toHaveBeenCalledWith('overdue', undefined);
    expect(screen.getByText('超时工单 · 工单明细')).toBeInTheDocument();
  });
});
