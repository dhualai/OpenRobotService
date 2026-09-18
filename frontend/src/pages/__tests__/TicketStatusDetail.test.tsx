import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
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

vi.mock('@/stores/auth', () => ({
  PERMISSION_VIEW_ALL: 'frontend:admin:dashboard:view-all',
  useAuthStore: () => ({ projectIds: [], hasPermission: () => true }),
}));

vi.mock('@/shared/utils/wechatJsSdk', () => ({
  navigateInWechat: vi.fn(),
}));

vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title }: { title?: ReactNode }) => <nav>{title}</nav>,
  Loading: ({ text }: { text?: string }) => <div>{text}</div>,
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

const renderView = () =>
  render(
    <MemoryRouter initialEntries={['/admin/dashboard/tickets/overdue']}>
      <Routes>
        <Route path="/admin/dashboard/tickets/:status" element={<TicketStatusDetail />} />
      </Routes>
    </MemoryRouter>,
  );

describe('TicketStatusDetail · 超时工单列表', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
});
