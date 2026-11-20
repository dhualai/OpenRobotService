// 用户管理列表排序：默认按微信关注时间倒序（最新在前），可切「用户名」A→Z。
// 规则持久化到本机 —— 后台改用户信息（列表重拉）、切走再回来顺序都不变，直到手动改。
// 断言都从渲染结果出发（卡片标题的 DOM 顺序、localStorage），不直接测内部函数。
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// 必须 hoisted：@/api/profile 在 import 期就会调一次 createRequest()，而 vi.mock 工厂
// 会在那个时刻求值，普通顶层 const 还处在 TDZ
const mockRequest = vi.hoisted(() => vi.fn());

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  createRequest: () => mockRequest,
}));

// 编辑弹层才用到的选项接口：用例不打开弹层，给最小桩（头像 URL 等真实实现照用）
vi.mock('@/api/profile', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/profile')>()),
  getProfileOptions: () => Promise.resolve({}),
}));

// Popup/Dialog 只在 visible 时渲染子树（与 tdesign 一致）
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Loading: ({ text }: { text?: ReactNode }) => <div data-testid="loading">{text}</div>,
  Popup: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null,
  Dialog: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="dialog">{children}</div> : null,
  BackTop: () => null,
}));

import UserManage from '../admin/UserManage';
import { useAuthStore } from '@/stores/auth';

interface UserRow {
  id: string;
  username: string;
  name?: string | null;
  status?: string;
  department?: string | null;
  job_level?: number;
  subscribe_time?: number | null;
}

const mkUser = (over: Partial<UserRow> & { id: string; username: string }): UserRow => ({
  status: 'active',
  job_level: 1,
  ...over,
});

// 三人：李四关注最晚、张三次之、王五无关注时间（手工账号/未关注/无快照）→ 默认应为 李四 > 张三 > 王五
const USERS: UserRow[] = [
  mkUser({ id: 'u1', username: 'zhang_san', name: '张三', subscribe_time: 111 }),
  mkUser({ id: 'u2', username: 'li_si', name: '李四', subscribe_time: 333 }),
  mkUser({ id: 'u3', username: 'wang_wu', name: '王五', subscribe_time: null }),
];

const setup = (users: UserRow[] = USERS) => {
  mockRequest.mockImplementation((url: string) => {
    if (url.startsWith('/users/?')) return Promise.resolve(users);
    // 角色 / 项目 / 权限名称查找表：本用例不校验，返回空列表
    if (url === '/roles/' || url === '/permissions/') return Promise.resolve([]);
    if (url.startsWith('/projects/')) return Promise.resolve([]);
    return Promise.resolve(null);
  });
  return render(
    <MemoryRouter>
      <UserManage />
    </MemoryRouter>
  );
};

// 列表按 DOM 顺序取卡片标题（姓名）：按组件声明的 testid 取，不认样式类名
const renderedNames = () =>
  screen.getAllByTestId('user-card-title').map((el) => el.textContent ?? '');

describe('用户管理列表排序', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // 真 auth store：admin 权限码直通，判断逻辑与线上一致
    useAuthStore.setState({ username: 'admin', permissions: ['admin'] });
  });

  it('默认按关注时间倒序：最新关注在前，无关注时间的排最后', async () => {
    setup();
    await waitFor(() => expect(renderedNames()).toEqual(['李四', '张三', '王五']));

    // 默认选中「关注时间」；有数据的卡片上带日期 chip（无数据的王五没有）
    expect(screen.getByRole('button', { name: '关注时间' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getAllByText(/^关注 \d{4}\/\d{2}\/\d{2}$/)).toHaveLength(2);
  });

  it('切到「用户名」按账号名 A→Z，并持久化到本机', async () => {
    setup();
    await waitFor(() => expect(renderedNames()).toEqual(['李四', '张三', '王五']));

    fireEvent.click(screen.getByRole('button', { name: '用户名' }));
    // li_si < wang_wu < zhang_san
    expect(renderedNames()).toEqual(['李四', '王五', '张三']);
    expect(screen.getByRole('button', { name: '用户名' }).getAttribute('aria-pressed')).toBe('true');
    expect(localStorage.getItem('admin_user_manage_sort')).toBe('username');
  });

  it('切走再回来、期间后台改过用户资料：排序规则与顺序都不变', async () => {
    localStorage.setItem('admin_user_manage_sort', 'username');
    const { unmount } = setup();
    await waitFor(() => expect(renderedNames()).toEqual(['李四', '王五', '张三']));

    unmount(); // 离开页面

    // 回来时列表已重拉：王五改过姓名/部门（排序键 username 未变）→ 顺序不跳、仍按用户名排
    setup(USERS.map((u) => (u.id === 'u3' ? { ...u, name: '王小五', department: '交付部' } : u)));
    await waitFor(() => expect(renderedNames()).toEqual(['李四', '王小五', '张三']));
    expect(screen.getByRole('button', { name: '用户名' }).getAttribute('aria-pressed')).toBe('true');
  });
});
