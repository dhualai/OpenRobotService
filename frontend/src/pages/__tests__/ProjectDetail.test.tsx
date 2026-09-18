import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const mockNavigate = vi.fn();
const mockCreateRequest = vi.fn();
const mockToast = vi.fn();
const mockAiGet = vi.fn();
// useParams 的 id 由用例控制：默认 'new'（新建模式），AI 摘要用例切到 'p1'
const routeParams = vi.hoisted(() => ({ id: 'new' }));

// 必须用 vi.hoisted：vi.mock 工厂会被提升到文件顶部，在普通顶层 class 之前执行，
// 直接引用会报 "Cannot access 'MockApiError' before initialization"
const MockApiError = vi.hoisted(() => class MockApiError extends Error {
  statusCode: number;
  constructor(message: string, statusCode: number) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
  }
});

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: routeParams.id }),
    useNavigate: () => mockNavigate,
  };
});

vi.mock('@/api/client', () => ({
  createRequest: () => mockCreateRequest,
  ApiError: MockApiError,
  clearCache: vi.fn(),
}));

vi.mock('@/api/ai', () => ({
  aiGet: () => mockAiGet(),
}));

// 项目信息管理卡 / 项目动态卡挂载后会异步拉取：这里保持挂起（不 resolve），
// 既不触发 act 警告，也不占用下面 mockCreateRequest 的请求桩与调用次数断言
vi.mock('@/api/infoNodes', () => ({
  fetchInfoTree: () => new Promise(() => {}),
  createInfoNodeApi: vi.fn(),
  updateInfoNodeApi: vi.fn(),
  moveInfoNodeApi: vi.fn(),
  deleteInfoNodeApi: vi.fn(),
  importInfoTreeApi: vi.fn(),
  importInfoTemplateApi: vi.fn(),
  fetchInfoNodeMarksApi: () => new Promise(() => {}),
  toggleInfoNodeMarkApi: vi.fn(),
  fetchProjectActivityApi: () => new Promise(() => {}),
  fetchInfoNodeChangeSummaryApi: () => new Promise(() => {}),
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: (selector: (s: { username: string }) => unknown) => selector({ username: 'admin' }),
}));

// 项目工单卡同样挂载即拉取：保持挂起，避免占用下方 mockCreateRequest 的请求桩
vi.mock('@/api/projectTickets', () => ({
  fetchProjectTicketsOverviewApi: () => new Promise(() => {}),
  configureBlockingWeightsApi: vi.fn(),
}));

vi.mock('@/config/api', () => ({
  default: { ADMIN: { BASE_URL: '/api/admin' }, TASKS: { BASE_URL: '/api/tasks' } },
}));

vi.mock('tdesign-mobile-react', () => {
  interface FieldProps {
    value?: string;
    onChange?: (v: string) => void;
    onBlur?: () => void;
    placeholder?: string;
    autofocus?: boolean;
  }
  const Navbar = ({ title, right }: { title?: ReactNode; right?: ReactNode }) => (
    <div data-testid="navbar">
      <span>{title}</span>
      <div>{right}</div>
    </div>
  );
  const Loading = () => <div data-testid="loading" />;
  const Popup = ({ children, visible }: { children?: ReactNode; visible?: boolean }) =>
    visible ? <div data-testid="popup">{children}</div> : null;
  const Upload = () => <div data-testid="upload" />;
  const Checkbox = () => <div data-testid="checkbox" />;
  const Input = (props: FieldProps) => (
    <input
      value={props.value ?? ''}
      onChange={(e) => props.onChange?.(e.target.value)}
      onBlur={props.onBlur}
      placeholder={props.placeholder}
      autoFocus={props.autofocus}
    />
  );
  const Textarea = (props: FieldProps) => (
    <textarea
      value={props.value ?? ''}
      onChange={(e) => props.onChange?.(e.target.value)}
      onBlur={props.onBlur}
      placeholder={props.placeholder}
    />
  );
  // 一键回到顶部按钮：无交互逻辑可测，渲染占位即可
  const BackTop = () => <div data-testid="backtop" />;
  return {
    Navbar,
    Loading,
    Toast: (opts: { message?: string; theme?: string }) => {
      mockToast(opts);
      return null;
    },
    Popup,
    Upload,
    Checkbox,
    Input,
    Textarea,
    BackTop,
  };
});

import ProjectDetail from '../admin/ProjectDetail';

const renderView = () =>
  render(
    <MemoryRouter>
      <ProjectDetail />
    </MemoryRouter>
  );

describe('ProjectDetail（USP 项目新建）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeParams.id = 'new';
    mockNavigate.mockClear();
    mockToast.mockClear();
    mockCreateRequest.mockClear();
    mockAiGet.mockClear();
  });

  it('新建模式展示必填标记（项目名称/项目编号/项目状态）', () => {
    renderView();
    expect(screen.getAllByText('*')).toHaveLength(3);
  });

  it('未填必填字段点「创建」→ 提示先填写项目名称，且不发请求', () => {
    renderView();
    fireEvent.click(screen.getByText('创建'));
    expect(mockToast).toHaveBeenCalledWith({ message: '请填写项目名称', theme: 'warning' });
    expect(mockCreateRequest).not.toHaveBeenCalled();
  });

  it('项目编号重复（后端 409）→ 提示用户项目已存在请重新输入', async () => {
    mockCreateRequest.mockRejectedValueOnce(
      new MockApiError('项目编号「CODE-1」已存在，请重新输入', 409)
    );
    renderView();

    // 填写项目名称
    fireEvent.click(screen.getByText('未命名项目'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '项目A' } });
    fireEvent.blur(screen.getByRole('textbox'));

    // 填写项目编号（项目概况卡「项目编号」行：点值进入编辑，标签自身不可点）
    const codeRow = Array.from(document.querySelectorAll('.mac-meta-row'))
      .find((row) => row.textContent?.startsWith('项目编号'));
    expect(codeRow).toBeTruthy();
    fireEvent.click(within(codeRow as HTMLElement).getByText('未填写'));
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'CODE-1' } });
    fireEvent.blur(screen.getByRole('textbox'));

    fireEvent.click(screen.getByText('创建'));
    // 409 由 createRequest 的 rejected promise 触发，catch 在微任务里跑，需等待
    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith({ message: '项目编号「CODE-1」已存在，请重新输入', theme: 'warning' });
    });
  });
});

describe('ProjectDetail（卡片裁剪）', () => {
  const baseProject = {
    id: 'p1',
    project_code: 'P-001',
    name: '测试项目A',
    status: '正在实施',
    category_basis: '重要紧急',
    issues: 0,
    risks: 0,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    routeParams.id = 'p1';
    mockCreateRequest.mockReset();
    mockCreateRequest.mockResolvedValue({ ...baseProject });
  });

  it('只保留项目概况 / 项目信息管理 / 项目工单 / 项目动态四张卡，其余卡片不再渲染', async () => {
    renderView();
    expect(await screen.findByText('项目概况')).toBeTruthy();
    expect(screen.getByText('项目信息管理')).toBeTruthy();
    expect(screen.getByText('项目工单')).toBeTruthy();
    expect(screen.getByText('项目动态')).toBeTruthy();
    expect(screen.getByText('关注节点变动')).toBeTruthy();

    ['项目基础画像', '项目生命周期', '风险管理', '责任体系'].forEach((title) => {
      expect(screen.queryByText(title)).toBeNull();
    });
    // 项目阶段编辑入口随「项目生命周期」卡挪进概况（由它决定进度），仍在
    expect(screen.getByText('项目阶段')).toBeTruthy();
  });

  it('新建模式不渲染「项目工单」与「项目动态」（项目尚未落库）', () => {
    routeParams.id = 'new';
    renderView();
    expect(screen.getByText('项目概况')).toBeTruthy();
    expect(screen.queryByText('项目工单')).toBeNull();
    expect(screen.queryByText('项目动态')).toBeNull();
  });
});

describe('ProjectDetail（AI 项目摘要）', () => {
  // 非新建模式的最小项目：只要渲染路径不崩即可，字段多走可选链/兜底
  const baseProject = {
    id: 'p1',
    project_code: 'P-001',
    name: '测试项目A',
    status: '正在实施',
    category_basis: '重要紧急',
    issues: 0,
    risks: 0,
  };
  const genCall = ['/projects/p1/ai-summary', { method: 'POST', timeout: 180000 }];

  beforeEach(() => {
    vi.clearAllMocks();
    routeParams.id = 'p1';
    mockToast.mockClear();
    mockCreateRequest.mockClear();
    mockAiGet.mockClear();
  });

  it('已有摘要：渲染 ext_info 里的 AI 摘要（纯文本旧数据也兼容），点「重新生成」用新摘要刷新', async () => {
    mockCreateRequest
      .mockResolvedValueOnce({ ...baseProject, ext_info: { overview: { ai_summary: '旧的摘要内容' } } })
      .mockResolvedValueOnce({ summary: '全新的摘要内容', ext_info: { overview: { ai_summary: '全新的摘要内容' } } });

    renderView();
    // 首次 GET 项目详情（含 include_risks 查询串），摘要来自 ext_info.overview.ai_summary
    expect(await screen.findByText('旧的摘要内容')).toBeTruthy();
    expect(mockCreateRequest).toHaveBeenCalledWith('/projects/p1?include_risks=true');

    fireEvent.click(screen.getByText('重新生成'));
    expect(await screen.findByText('全新的摘要内容')).toBeTruthy();
    expect(mockCreateRequest).toHaveBeenLastCalledWith(...genCall);
    expect(mockToast).toHaveBeenCalledWith({ message: '摘要已生成并保存', theme: 'success' });
  });

  it('Markdown 摘要渲染为标题 / 列表 / 加粗结构', async () => {
    const md = '## 项目概况\n\n- **项目类型**：试点项目\n- 车数：6 台';
    mockCreateRequest.mockResolvedValueOnce({
      ...baseProject,
      ext_info: { overview: { ai_summary: md } },
    });

    renderView();
    // 页面上「项目概况」标题、类型标签等文案在其他区块也有，断言一律限定在摘要卡内
    await waitFor(() => expect(document.querySelector('.mac-ai__md')).toBeTruthy());
    const mdBox = document.querySelector('.mac-ai__md') as HTMLElement;
    expect(within(mdBox).getByRole('heading', { name: '项目概况' })).toBeTruthy();
    expect(within(mdBox).getByText('项目类型')).toBeTruthy(); // **加粗**字段名
    expect(within(mdBox).getByText('车数：6 台')).toBeTruthy();
    expect(within(mdBox).getAllByRole('listitem')).toHaveLength(2);
  });

  it('无摘要：按钮为「点击生成」，生成中禁用并显示加载文案，完成后展示摘要', async () => {
    // 用受控 Promise 卡住生成请求，观察按钮的「生成中...」禁用态
    let resolvePost: (value: unknown) => void = () => {};
    mockCreateRequest
      .mockResolvedValueOnce({ ...baseProject, ext_info: null })
      .mockImplementationOnce(() => new Promise((resolve) => { resolvePost = resolve; }));

    renderView();
    const generateBtn = await screen.findByRole('button', { name: '点击生成' });
    expect(document.querySelector('.mac-ai__body')?.textContent).toBe('暂无数据');

    fireEvent.click(generateBtn);
    const pendingBtn = screen.getByRole('button', { name: '生成中...' }) as HTMLButtonElement;
    expect(pendingBtn.disabled).toBe(true);
    expect(mockCreateRequest).toHaveBeenLastCalledWith(...genCall);

    await act(async () => {
      resolvePost({ summary: '第一次生成的摘要', ext_info: { overview: { ai_summary: '第一次生成的摘要' } } });
    });
    expect(screen.getByText('第一次生成的摘要')).toBeTruthy();
    // 生成完成后按钮变为「重新生成」
    expect(screen.getByRole('button', { name: '重新生成' })).toBeTruthy();
    expect(mockToast).toHaveBeenCalledWith({ message: '摘要已生成并保存', theme: 'success' });
  });
});
