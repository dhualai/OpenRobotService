// 历史工单列表的「收藏（关注）」能力（与系统任务列表页同源：task_followers 表 + /api/tasks/{id}/follow）：
//   ① 每行星标 → toggleTaskFollow，乐观更新、失败回滚
//   ② 状态 Tab 末尾的「我关注的」开关 → 与状态筛选**叠加**（followed: true），不是互斥单选
//   ③ viewer 恒传当前登录用户（关注归属），与「按创建者过滤」的 username 解耦（admin 也传）
// 打桩边界：网络层（@/api/ai、@/api/taskFollow）打桩，不触网；tdesign 只留最小替身
//（真实 tdesign 会让本文件收集阶段卡住）；auth 用真 store，断言跨组件契约。
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

const mockQaListTickets = vi.fn();
const mockToggleTaskFollow = vi.fn();
const mockNavigateInWechat = vi.fn();

vi.mock('@/api/ai', () => ({
  qaListTickets: (...args: unknown[]) => mockQaListTickets(...args),
}));

vi.mock('@/api/taskFollow', () => ({
  toggleTaskFollow: (...args: unknown[]) => mockToggleTaskFollow(...args),
}));

// 进详情的跳转方式打桩：PC 微信的分享卡片取「最近一次整页加载时的 URL」且不认 JS-SDK
// 设的 link，用 SPA 进详情会让转发出去仍是上一张工单；故此处断言「必须走 navigateInWechat」
// 这一契约（该函数在非 PC 端等价于 navigate，故打桩不影响其余用例语义）。
vi.mock('@/shared/utils/wechatJsSdk', () => ({
  navigateInWechat: (...args: unknown[]) => mockNavigateInWechat(...args),
}));

// tdesign 只留列内用到的符号的最小替身：真实 tdesign 会让本文件**收集阶段**卡住
//（与 CallView.vehicleScan 测试同因同解，见该文件注释）
vi.mock('tdesign-mobile-react', () => ({
  Loading: ({ text }: { text?: string }) => <div>{text}</div>,
  Toast: vi.fn(),
  Button: ({ children }: { children?: ReactNode }) => <button type="button">{children}</button>,
  Popup: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
  DialogPlugin: { confirm: vi.fn() },
}));

import HistoryTickets from '../call/HistoryTickets';
import type { AiTicketBrief } from '@/api/ai';
import { useAuthStore } from '@/stores/auth';

const FOLLOWED_KEY = 'call.history.followedOnly';

const makeTicket = (over: Partial<AiTicketBrief> = {}): AiTicketBrief => ({
  id: 101,
  // session_id 是 AiTicketBrief 的必填字段（后端 metadata_info.session_id，无会话时为空串）：
  // 缺了它、再 spread Partial<AiTicketBrief>，会让推导出的对象类型把 session_id 放宽成可选，
  // 于是 tsc -b 在打包阶段报 TS2322（vitest 走 esbuild 不做类型检查，拦不住）
  session_id: 'sess-101',
  title: '机器人不动了',
  status: 'new',
  type: 'problem',
  created_at: '2026-10-01 08:00:00',
  is_followed: false,
  ...over,
});

const listResponse = (items: AiTicketBrief[]) => ({
  data: {
    items,
    total: items.length,
    by_status: { new: items.length },
    active_total: items.length,
  },
});

const renderPage = () => render(
  <MemoryRouter>
    <HistoryTickets />
  </MemoryRouter>,
);

/** 第 n 次列表请求带的筛选参数 */
const filtersOfCall = (n: number) => mockQaListTickets.mock.calls[n]?.[2] as Record<string, unknown>;

beforeEach(() => {
  sessionStorage.clear();
  useAuthStore.setState({ username: 'zhangsan', isAdmin: false, isLoggedIn: true });
  mockQaListTickets.mockReset();
  mockToggleTaskFollow.mockReset();
});

describe('历史工单列表 · 收藏（关注）', () => {
  it('首屏按当前登录用户拉取，恒传 viewer 且默认不带 followed', async () => {
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket()]));

    renderPage();

    await screen.findByText('机器人不动了');
    expect(filtersOfCall(0)).toMatchObject({ viewer: 'zhangsan', username: 'zhangsan' });
    expect(filtersOfCall(0)).not.toHaveProperty('followed');
  });

  it('admin 不按创建者过滤，但仍传 viewer 回填自己的星标态', async () => {
    useAuthStore.setState({ username: 'boss', isAdmin: true });
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket()]));

    renderPage();

    await screen.findByText('机器人不动了');
    expect(filtersOfCall(0)).toMatchObject({ viewer: 'boss' });
    expect(filtersOfCall(0)).not.toHaveProperty('username');
  });

  it('已关注 / 未关注的工单分别渲染「取消关注」与「添加关注」星标', async () => {
    mockQaListTickets.mockResolvedValue(listResponse([
      makeTicket({ id: 1, title: 'A', is_followed: true }),
      makeTicket({ id: 2, title: 'B', is_followed: false }),
    ]));

    renderPage();

    expect(await screen.findByRole('button', { name: '取消关注' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '添加关注' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('点星标即关注：调用 toggleTaskFollow 并乐观更新星标', async () => {
    const user = userEvent.setup();
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket({ id: 7, is_followed: false })]));
    mockToggleTaskFollow.mockResolvedValue(true);

    renderPage();

    await user.click(await screen.findByRole('button', { name: '添加关注' }));

    expect(mockToggleTaskFollow).toHaveBeenCalledWith(7, true);
    await waitFor(() => expect(screen.getByRole('button', { name: '取消关注' })).toHaveAttribute('aria-pressed', 'true'));
  });

  it('关注失败时回滚星标状态', async () => {
    const user = userEvent.setup();
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket({ id: 7, is_followed: false })]));
    mockToggleTaskFollow.mockResolvedValue(false);

    renderPage();

    await user.click(await screen.findByRole('button', { name: '添加关注' }));

    await waitFor(() =>
      expect(screen.getByRole('button', { name: '添加关注' })).toHaveAttribute('aria-pressed', 'false'),
    );
  });

  it('「我关注的」开关与状态筛选叠加（不是互斥替换）', async () => {
    const user = userEvent.setup();
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket()]));

    renderPage();
    await screen.findByText('机器人不动了');

    await user.click(screen.getByRole('button', { name: '处理中' }));
    await waitFor(() => expect(filtersOfCall(mockQaListTickets.mock.calls.length - 1)).toMatchObject({ status: 'in_progress' }));

    await user.click(screen.getByRole('button', { name: '我关注的' }));
    await waitFor(() =>
      expect(filtersOfCall(mockQaListTickets.mock.calls.length - 1)).toMatchObject({
        status: 'in_progress',
        followed: true,
      }),
    );
    expect(screen.getByRole('button', { name: '我关注的' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('会话内恢复「我关注的」开关，并在该视图取消关注后重新拉取列表', async () => {
    const user = userEvent.setup();
    sessionStorage.setItem(FOLLOWED_KEY, '1');
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket({ id: 9, is_followed: true })]));
    mockToggleTaskFollow.mockResolvedValue(true);

    renderPage();

    await screen.findByRole('button', { name: '取消关注' });
    expect(filtersOfCall(0)).toMatchObject({ followed: true });

    const before = mockQaListTickets.mock.calls.length;
    await user.click(screen.getByRole('button', { name: '取消关注' }));

    expect(mockToggleTaskFollow).toHaveBeenCalledWith(9, false);
    await waitFor(() => expect(mockQaListTickets.mock.calls.length).toBeGreaterThan(before));
  });

  it('「我关注的」无数据时给出对应空态文案', async () => {
    sessionStorage.setItem(FOLLOWED_KEY, '1');
    mockQaListTickets.mockResolvedValue(listResponse([]));

    renderPage();

    expect(await screen.findByText('暂无关注的工单')).toBeInTheDocument();
  });
});

// 回归用例：历史工单列表点卡片进入详情必须走 navigateInWechat。
// 背景（用户实测）：PC 微信不认 JS-SDK 设的分享 link，只认「最近一次整页加载时的 URL」；
// 原先用 navigate 做 SPA 跳转时地址栏虽已变成目标工单，微信仍按旧 URL 生成转发卡片，
// 导致转发出去的是上一张工单。navigateInWechat 在 PC 微信下改走整页跳转来刷新该 URL，
// 其他端等价于 navigate，行为与体验不变。
describe('历史工单列表 · 进入详情的跳转方式（PC 微信转发链接修复）', () => {
  beforeEach(() => {
    mockNavigateInWechat.mockReset();
  });

  it('点击工单卡片走 navigateInWechat，目标是当前这条工单的详情路由', async () => {
    const user = userEvent.setup();
    mockQaListTickets.mockResolvedValue(listResponse([makeTicket({ id: 928 })]));

    renderPage();

    await user.click(await screen.findByText('机器人不动了'));

    expect(mockNavigateInWechat).toHaveBeenCalledWith(expect.any(Function), '/call/ticket/db_928');
  });
});
