import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import ProjectTicketsCard from '../admin/ProjectTicketsCard';
import {
  configureBlockingWeightsApi,
  fetchProjectTicketsOverviewApi,
  type ProjectTicketsOverview,
} from '@/api/projectTickets';

// tdesign 弹层/Toast 桩：Toast 记录调用，Popup 仅 visible 时渲染内容，Textarea 用原生元素
const mockToast = vi.fn();
vi.mock('tdesign-mobile-react', () => ({
  Toast: (opts: { message?: string; theme?: string }) => {
    mockToast(opts);
    return null;
  },
  Popup: ({ children, visible }: { children?: React.ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null,
  Textarea: (props: {
    value?: string;
    onChange?: (v: string) => void;
    placeholder?: string;
  }) => (
    <textarea
      value={props.value ?? ''}
      onChange={(e) => props.onChange?.(e.target.value)}
      placeholder={props.placeholder}
    />
  ),
}));

vi.mock('@/api/projectTickets', () => ({
  fetchProjectTicketsOverviewApi: vi.fn(),
  configureBlockingWeightsApi: vi.fn(),
}));

// jsdom 无 canvas：echarts 组件桩成占位 div，把 option 序列化出来供断言
vi.mock('@/shared/components/ReactECharts', () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="trend-chart" data-option={JSON.stringify(option)} />
  ),
}));

// 「配置阻滞权重」按登录用户权限显隐：权限列表由用例控制
const authState = vi.hoisted(() => ({ permissions: ['admin'] as string[] }));
vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { permissions: string[] }) => unknown) => selector({ permissions: authState.permissions }),
}));

// 工单条目点击跳转：断言 mock 的 useNavigate 收到的目标路由
// （jsdom 的 UA 不是微信，navigateInWechat 会直接调 navigate）
const mockNavigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => mockNavigate };
});

const TS = '2026-09-14 10:00:00';

const TICKET = {
  id: 12,
  title: '导航不识别货架',
  status: 'in_progress',
  priority: 'urgent',
  ticket_type: 'bug',
  created_by: 'zhang',
  creator_name: '张三',
  assigned_to: 'u1',
  assignee_name: '李四',
  created_at: TS,
  deadline_at: '2026-09-10T00:00:00',
  overdue: true,
  description: '现场多台车复现，影响上线',
};

const OVERVIEW: ProjectTicketsOverview = {
  total: 7,
  by_status: { new: 1, in_progress: 2, paused: 1, resolved: 1, closed: 1, cancelled: 1 },
  pending_count: 3,
  overdue_count: 1,
  resolved_rate: 0.5,
  weekly: [
    { week_start: '2026-07-27', count: 1 },
    { week_start: '2026-08-03', count: 0 },
    { week_start: '2026-08-10', count: 2 },
    { week_start: '2026-08-17', count: 0 },
    { week_start: '2026-08-24', count: 1 },
    { week_start: '2026-08-31', count: 0 },
    { week_start: '2026-09-07', count: 1 },
    { week_start: '2026-09-14', count: 2 },
  ],
  blocking: {
    mode: 'default',
    tickets: [TICKET],
    prompt: null,
    summary: '',
    reasons: {},
    updated_by_name: null,
    updated_at: null,
  },
};

const renderCard = () => render(<ProjectTicketsCard projectId="P-001" />);

describe('ProjectTicketsCard（项目工单卡）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.permissions = ['admin'];
    vi.mocked(fetchProjectTicketsOverviewApi).mockResolvedValue(OVERVIEW);
  });

  it('顶部三格：总工单数 / 正在处理（处理中+挂起）/ 已完成（已解决+已关闭）', async () => {
    renderCard();
    expect(fetchProjectTicketsOverviewApi).toHaveBeenCalledWith('P-001');
    // fixture 状态分布：new1 / in_progress2 / paused1 / resolved1 / closed1 / cancelled1
    // 正在处理 = 2+1 = 3；已完成 = 1+1 = 2（新建、已取消不计入这两格，逐格比对防口径回归）
    expect(await screen.findByText('总工单数')).toBeTruthy();
    const stats = Array.from(document.querySelectorAll('.mac-tix__stat')).map((cell) => [
      cell.querySelector('.mac-tix__label')?.textContent,
      cell.querySelector('.mac-tix__num')?.textContent,
    ]);
    expect(stats).toEqual([
      ['总工单数', '7'],
      ['正在处理', '3'],
      ['已完成', '2'],
    ]);
  });

  it('默认模式下展示阻滞工单条目（工单号/提单人/接单人/优先级/状态/超期/问题概况）', async () => {
    renderCard();
    expect(await screen.findByText('核心阻滞工单')).toBeTruthy();
    const card = document.querySelector('.mac-tix__ticket') as HTMLElement;
    expect(within(card).getByText('导航不识别货架')).toBeTruthy();
    expect(within(card).getByText('#12')).toBeTruthy();
    expect(within(card).getByText('提单人 张三')).toBeTruthy();
    expect(within(card).getByText('接单人 李四')).toBeTruthy();
    expect(within(card).getByText('紧急')).toBeTruthy();
    expect(within(card).getByText('处理中')).toBeTruthy(); // 状态标签（原始枚举 in_progress）
    expect(within(card).getByText('已超期')).toBeTruthy();
    expect(within(card).getByText('现场多台车复现，影响上线')).toBeTruthy();
    // 未配置时给默认排序口径说明，不显示 AI 判定
    expect(screen.getByText(/当前按优先级、截止时间与创建时间默认排序/)).toBeTruthy();
    expect(screen.queryByText('AI 判定')).toBeNull();
  });

  it('点击阻滞工单条目跳转到该工单详情页（/tasks/:id）', async () => {
    renderCard();
    fireEvent.click(await screen.findByText('导航不识别货架'));
    expect(mockNavigate).toHaveBeenCalledWith('/tasks/12');
  });

  it('AI 配置模式：显示 AI 判定徽标、总述与逐单阻滞理由', async () => {
    vi.mocked(fetchProjectTicketsOverviewApi).mockResolvedValue({
      ...OVERVIEW,
      blocking: {
        mode: 'ai',
        tickets: [TICKET],
        prompt: '优先验收相关',
        summary: '当前阻滞集中在导航识别',
        reasons: { '12': '阻塞现场验收' },
        updated_by_name: '管理员',
        updated_at: '2026-09-16 12:00:00',
      },
    });
    renderCard();
    expect(await screen.findByText('AI 判定')).toBeTruthy();
    expect(screen.getByText('当前阻滞集中在导航识别')).toBeTruthy();
    expect(screen.getByText('阻塞现场验收')).toBeTruthy();
    expect(screen.queryByText(/默认排序/)).toBeNull();
  });

  it('趋势图数据为近 8 周每周新建工单数', async () => {
    renderCard();
    const chart = await screen.findByTestId('trend-chart');
    const option = JSON.parse(chart.getAttribute('data-option') as string);
    expect(option.series[0].type).toBe('bar');
    expect(option.series[0].data).toEqual([1, 0, 2, 0, 1, 0, 1, 2]);
    expect(option.xAxis.data[0]).toBe('7/27');
    expect(option.xAxis.data[7]).toBe('9/14');
  });

  it('无阻滞工单时给出空态文案', async () => {
    vi.mocked(fetchProjectTicketsOverviewApi).mockResolvedValue({
      ...OVERVIEW,
      blocking: { ...OVERVIEW.blocking, tickets: [] },
    });
    renderCard();
    expect(await screen.findByText('暂无未完成的阻滞工单')).toBeTruthy();
  });

  it('加载失败显示重试，点击后重新拉取', async () => {
    vi.mocked(fetchProjectTicketsOverviewApi).mockRejectedValueOnce(new Error('boom'));
    renderCard();
    expect(await screen.findByText('项目工单加载失败')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    await waitFor(() => expect(fetchProjectTicketsOverviewApi).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('总工单数')).toBeTruthy();
  });

  // —— 「配置阻滞权重」入口：仅管理员/超级管理员可见 ——

  it('管理员可见「配置阻滞权重」按钮，非管理员不可见', async () => {
    const admin = renderCard();
    expect(await screen.findByRole('button', { name: /配置阻滞权重/ })).toBeTruthy();
    admin.unmount();

    authState.permissions = ['user'];
    render(<ProjectTicketsCard projectId="P-002" />);
    await waitFor(() => expect(fetchProjectTicketsOverviewApi).toHaveBeenCalledWith('P-002'));
    expect(screen.queryByRole('button', { name: /配置阻滞权重/ })).toBeNull();
    // 非管理员仍能看到默认排序口径说明（但没有「让 AI 判定」的引导语）
    expect(screen.getByText(/当前按优先级、截止时间与创建时间默认排序$/)).toBeTruthy();
  });

  it('点开弹窗预填已有提示词，提交则调 AI 配置接口并以返回结果替换展示', async () => {
    vi.mocked(fetchProjectTicketsOverviewApi).mockResolvedValue({
      ...OVERVIEW,
      blocking: { ...OVERVIEW.blocking, prompt: '旧要求' },
    });
    vi.mocked(configureBlockingWeightsApi).mockResolvedValue({
      mode: 'ai',
      tickets: [TICKET],
      prompt: '新要求',
      summary: 'AI 总述',
      reasons: { '12': '影响验收' },
      updated_by_name: '管理员',
      updated_at: '2026-09-16 12:00:00',
    });
    renderCard();

    fireEvent.click(await screen.findByRole('button', { name: /配置阻滞权重/ }));
    const textarea = screen.getByPlaceholderText(/优先挑选影响现场验收/) as HTMLTextAreaElement;
    expect(textarea.value).toBe('旧要求');

    fireEvent.change(textarea, { target: { value: ' 新要求 ' } });
    fireEvent.click(screen.getByRole('button', { name: '提交判定' }));

    await waitFor(() => expect(configureBlockingWeightsApi).toHaveBeenCalledWith('P-001', '新要求'));
    expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ theme: 'success' }));
    // 弹窗关闭，展示已切换为 AI 判定 + 新理由
    expect(screen.queryByTestId('popup')).toBeNull();
    expect(await screen.findByText('AI 判定')).toBeTruthy();
    expect(screen.getByText('AI 总述')).toBeTruthy();
    expect(screen.getByText('影响验收')).toBeTruthy();
  });

  it('空提示词提交：提示并保持弹窗不请求', async () => {
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /配置阻滞权重/ }));
    fireEvent.click(screen.getByRole('button', { name: '提交判定' }));
    expect(mockToast).toHaveBeenCalledWith({ message: '请先输入阻滞权重的判定要求', theme: 'warning' });
    expect(configureBlockingWeightsApi).not.toHaveBeenCalled();
    expect(screen.getByTestId('popup')).toBeTruthy();
  });

  it('AI 配置失败：错误提示，弹窗保留可重试，展示不变', async () => {
    vi.mocked(configureBlockingWeightsApi).mockRejectedValue(new Error('大模型调用失败：timeout'));
    renderCard();
    fireEvent.click(await screen.findByRole('button', { name: /配置阻滞权重/ }));
    fireEvent.change(screen.getByPlaceholderText(/优先挑选影响现场验收/), { target: { value: '要求' } });
    fireEvent.click(screen.getByRole('button', { name: '提交判定' }));

    await waitFor(() => expect(mockToast).toHaveBeenCalledWith(
      expect.objectContaining({ theme: 'error', message: expect.stringContaining('大模型调用失败') }),
    ));
    expect(screen.getByTestId('popup')).toBeTruthy();
    expect(screen.queryByText('AI 判定')).toBeNull();
  });
});
