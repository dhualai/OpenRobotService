// 项目进度管理页的列表改动：长按置顶、排序（核算期 / 工单数）、筛选（5 个维度）。
// 用例都从界面出发断言（渲染后的卡片顺序与文案），不直接测内部函数。
import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockRequest = vi.fn();
const mockAiGet = vi.fn();

vi.mock('@/api/client', () => ({
  createRequest: () => mockRequest,
}));

vi.mock('@/api/ai', () => ({
  aiGet: () => mockAiGet(),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ hasPermission: () => true }),
  PERMISSION_VIEW_ALL: 'backend:project:view-all',
}));

vi.mock('@/config/api', () => ({
  default: { ADMIN: { BASE_URL: '/api/admin' } },
}));

// Popup 只在 visible 时渲染子树（与 tdesign 一致），Dialog 同理
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Loading: ({ text }: { text?: ReactNode }) => <div data-testid="loading">{text}</div>,
  Popup: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null,
  Dialog: ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="dialog">{children}</div> : null,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

import ProjectProgress from '../admin/ProjectProgress';

interface ProjectRow {
  id: string;
  project_code: string;
  name: string;
  status: string;
  contact_person: string;
  project_manager?: string | null;
  project_region?: string | null;
  total_vehicle_count?: number | null;
  risks?: number;
  ticket_count?: number | null;
  settlement_period?: string | null;
  system_id?: string | null;
}

const project = (over: Partial<ProjectRow> & { id: string; name: string }): ProjectRow => ({
  project_code: over.id,
  status: '正在实施',
  contact_person: '',
  risks: 0,
  ...over,
});

// 后端返回顺序里的项目名，用于断言渲染顺序（列表按 DOM 顺序取卡片标题）
const renderedNames = () =>
  screen.getAllByText(/^项目[ABC]$/).map((el) => el.textContent ?? '');

const setup = (
  projects: ProjectRow[],
  pins: string[] = [],
  wecomRecords: unknown[] = [],
) => {
  mockRequest.mockImplementation((url: string, init?: { method?: string }) => {
    if (url === '/projects/pins') return Promise.resolve({ project_ids: pins });
    if (url.startsWith('/projects/') && url.endsWith('/pin')) {
      return Promise.resolve({ pinned: init?.method !== 'DELETE' });
    }
    if (url.startsWith('/projects/')) return Promise.resolve(projects);
    return Promise.resolve(null);
  });
  mockAiGet.mockResolvedValue({ code: 0, data: { records: wecomRecords } });
  return render(
    <MemoryRouter>
      <ProjectProgress />
    </MemoryRouter>
  );
};

// 筛选下拉框：按钮名就是维度名（选中后变成「维度名: 值」），选项行是「值 + 命中项目数」，
// 所以两处都用前缀匹配（并转义括号等正则元字符）
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const byPrefix = (label: string) => new RegExp(`^${escapeRe(label)}`);

const openDropdown = (label: string) =>
  fireEvent.click(screen.getByRole('button', { name: byPrefix(label) }));

const pickOption = (label: string) =>
  fireEvent.click(screen.getByRole('button', { name: byPrefix(label) }));

const closeDropdown = () =>
  fireEvent.click(screen.getByRole('button', { name: '完成' }));

const searchOptions = (placeholder: string, value: string) =>
  fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value } });

describe('ProjectProgress 置顶', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it('置顶的项目排在最前（按置顶时间，最近置顶的在前）', async () => {
    // /projects/pins 按最近置顶在前返回：c 是刚置顶的，应排在 b 之前
    setup([
      project({ id: 'a', name: '项目A' }),
      project({ id: 'b', name: '项目B' }),
      project({ id: 'c', name: '项目C' }),
    ], ['c', 'b']);

    await waitFor(() => expect(renderedNames()).toEqual(['项目C', '项目B', '项目A']));
  });

  it('置顶接口不可用时静默降级，列表仍按后端顺序展示', async () => {
    mockRequest.mockImplementation((url: string) => {
      if (url === '/projects/pins') return Promise.reject(new Error('500'));
      if (url.startsWith('/projects/')) {
        return Promise.resolve([
          project({ id: 'a', name: '项目A' }),
          project({ id: 'b', name: '项目B' }),
        ]);
      }
      return Promise.resolve(null);
    });
    mockAiGet.mockResolvedValue({ code: 0, data: { records: [] } });
    render(<MemoryRouter><ProjectProgress /></MemoryRouter>);

    await waitFor(() => expect(renderedNames()).toEqual(['项目A', '项目B']));
  });

  it('长按卡片弹出操作卡：已置顶显示「取消置顶」并调 DELETE', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup([project({ id: 'a', name: '项目A' })], ['a']);
    await waitFor(() => expect(screen.getByText('项目A')).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByText('项目A'));
    await act(async () => { vi.advanceTimersByTime(700); });

    // 卡片上的置顶角标 + 操作卡里的「取消置顶」：两处都含「置顶」二字
    const cancelBtn = screen.getByRole('button', { name: /取消置顶/ });
    fireEvent.click(cancelBtn);

    await waitFor(() => expect(mockRequest).toHaveBeenCalledWith(
      '/projects/a/pin', { method: 'DELETE' },
    ));
  });

  it('长按未置顶卡片：操作卡显示「置顶」并调 POST', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    setup([project({ id: 'a', name: '项目A' })], []);
    await waitFor(() => expect(screen.getByText('项目A')).toBeInTheDocument());

    fireEvent.mouseDown(screen.getByText('项目A'));
    await act(async () => { vi.advanceTimersByTime(700); });

    fireEvent.click(screen.getByRole('button', { name: /^置顶$/ }));
    await waitFor(() => expect(mockRequest).toHaveBeenCalledWith(
      '/projects/a/pin', { method: 'POST' },
    ));
  });
});

describe('ProjectProgress 排序', () => {
  beforeEach(() => vi.clearAllMocks());

  const rows = [
    project({ id: 'a', name: '项目A', settlement_period: '202601', ticket_count: 3 }),
    project({ id: 'b', name: '项目B', settlement_period: '202608', ticket_count: 9 }),
    project({ id: 'c', name: '项目C', ticket_count: 7 }), // 无核算期
  ];

  it('按时间：核算期新的在前，没填核算期的排最后', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toEqual(['项目A', '项目B', '项目C']));

    fireEvent.click(screen.getByRole('button', { name: '按时间' }));
    expect(renderedNames()).toEqual(['项目B', '项目A', '项目C']);
  });

  it('按工单数：数量多的在前，无工单数（null）排最后', async () => {
    setup([
      project({ id: 'a', name: '项目A', ticket_count: 3 }),
      project({ id: 'b', name: '项目B', ticket_count: 9 }),
      project({ id: 'c', name: '项目C', ticket_count: null }),
    ]);
    await waitFor(() => expect(renderedNames()).toEqual(['项目A', '项目B', '项目C']));

    fireEvent.click(screen.getByRole('button', { name: '按工单数' }));
    expect(renderedNames()).toEqual(['项目B', '项目A', '项目C']);
  });

  it('排序不改变置顶优先：置顶项目始终在最前', async () => {
    setup(rows, ['a']);
    await waitFor(() => expect(renderedNames()).toEqual(['项目A', '项目B', '项目C']));

    fireEvent.click(screen.getByRole('button', { name: '按工单数' }));
    // 工单数最高的是 B(9)，但 A 被置顶，仍排最前
    expect(renderedNames()).toEqual(['项目A', '项目B', '项目C']);
  });
});

describe('ProjectProgress 筛选', () => {
  beforeEach(() => vi.clearAllMocks());

  // 每个字段都填上值（项目经理也一样）：否则「其他」会在多个维度里同时出现，
  // 弹层里的同名选项就不唯一了——单个维度的用例（AGV 那一档）才测得出「其他」
  const rows = [
    project({ id: 'a', name: '项目A', contact_person: '白永奇', project_region: '欧洲 Europe', total_vehicle_count: 3, status: '正在实施', project_manager: '张三' }),
    project({ id: 'b', name: '项目B', contact_person: '田树政', project_region: '大陆(China Mainland)', total_vehicle_count: 20, status: '项目结束', project_manager: '李四' }),
    project({ id: 'c', name: '项目C', contact_person: '白永奇', project_region: '大陆(China Mainland)', total_vehicle_count: null, status: '试运行中', project_manager: '王五' }),
  ];

  it('按项目阶段筛选，同维度多选取并集', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('项目阶段');
    pickOption('项目结束');
    expect(renderedNames()).toEqual(['项目B']);

    // 再选一个阶段：两个阶段取并集（A 与 B 留下），没选中的「试运行中」（C）仍被排除
    pickOption('正在实施');
    expect(renderedNames()).toEqual(['项目A', '项目B']);
  });

  it('维度之间取交集：对接人 + 地区同时满足', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('对接人');
    pickOption('白永奇');
    expect(renderedNames()).toEqual(['项目A', '项目C']);
    closeDropdown();

    openDropdown('地区');
    pickOption('欧洲 Europe');
    expect(renderedNames()).toEqual(['项目A']);
  });

  it('AGV 数量按区间分档筛选，没填车数的归到「其他」单独一档', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('AGV 数量');
    pickOption('11-30 台');
    expect(renderedNames()).toEqual(['项目B']);

    pickOption('其他');
    expect(renderedNames()).toEqual(['项目B', '项目C']);
  });

  it('没填值的项目归到「其他」，且永远排在选项最后', async () => {
    // 两个项目没填对接人（比「白永奇」还多一个），按命中数本该排最前
    setup([
      project({ id: 'a', name: '项目A', contact_person: '' }),
      project({ id: 'b', name: '项目B', contact_person: '' }),
      project({ id: 'c', name: '项目C', contact_person: '白永奇' }),
    ]);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('对接人');
    expect(screen.getAllByText(/^(白永奇|其他)$/).map((el) => el.textContent))
      .toEqual(['白永奇', '其他']);

    pickOption('其他');
    expect(renderedNames()).toEqual(['项目A', '项目B']);
  });

  it('下拉框「清空此项」只清当前维度，「清空筛选」清掉全部维度', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('项目阶段');
    pickOption('项目结束');
    closeDropdown();
    openDropdown('对接人');
    pickOption('田树政');
    expect(renderedNames()).toEqual(['项目B']);

    // 只清对接人：项目阶段那条还在（B 是「项目结束」，仍留在结果里）
    fireEvent.click(screen.getByRole('button', { name: '清空此项' }));
    expect(renderedNames()).toEqual(['项目B']);
    closeDropdown();

    // 一键清空全部维度：回到完整列表
    fireEvent.click(screen.getByRole('button', { name: /清空筛选/ }));
    expect(renderedNames()).toEqual(['项目A', '项目B', '项目C']);
  });

  it('对接人下拉框可模糊搜索：输「永奇」筛出「白永奇」', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('对接人');
    searchOptions('输入关键词模糊查找对接人', '永奇');
    expect(screen.queryByRole('button', { name: byPrefix('田树政') })).toBeNull();

    pickOption('白永奇');
    expect(renderedNames()).toEqual(['项目A', '项目C']);
  });

  it('下拉框搜索支持顺序命中（不要求连续）', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('对接人');
    searchOptions('输入关键词模糊查找对接人', '白奇'); // 「白…奇」按顺序命中「白永奇」
    expect(screen.getByRole('button', { name: byPrefix('白永奇') })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: byPrefix('田树政') })).toBeNull();
  });

  it('下拉框搜索无结果时给出空状态', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    openDropdown('项目经理');
    searchOptions('输入关键词模糊查找项目经理', 'zzz不存在');
    expect(screen.getByText('未找到匹配的选项')).toBeInTheDocument();
  });

  it('地区 / 项目阶段 / AGV 不给搜索框，选项原样列出直接选', async () => {
    setup(rows);
    await waitFor(() => expect(renderedNames()).toHaveLength(3));

    for (const dim of ['地区', '项目阶段', 'AGV 数量']) {
      openDropdown(dim);
      expect(screen.queryByPlaceholderText(`输入关键词模糊查找${dim}`)).toBeNull();
      closeDropdown();
    }

    // 没有搜索框也不影响选：全部选项都在，点了就生效
    openDropdown('地区');
    pickOption('欧洲 Europe');
    expect(renderedNames()).toEqual(['项目A']);
  });

  it('项目经理筛选用卡片实际显示的值（台账值优先）', async () => {
    setup(
      [
        project({ id: 'a', name: '项目A', project_manager: '本地李四' }),
        project({ id: 'b', name: '项目B', project_manager: '本地王五' }),
      ],
      [],
      [{ record_id: 'r1', values: { 项目编号: 'a', 项目经理: '台账张三' } }],
    );
    await waitFor(() => expect(renderedNames()).toHaveLength(2));
    // 台账值是异步到的：等它渲染到卡片上再筛
    await waitFor(() => expect(screen.getByText(/项目经理: 台账张三/)).toBeInTheDocument());

    openDropdown('项目经理');
    pickOption('台账张三');
    expect(renderedNames()).toEqual(['项目A']);
  });
});
