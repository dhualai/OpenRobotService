// ChatPanel 草稿确认弹窗·自定义新项目（ORS-1023）联动回归：
//   ① 项目下拉输入不存在的项目名 → 选「作为新项目」→ 自动勾选兜底双工单 + 弹出
//      选填的新项目基本信息表单（编号/客户/区域）；
//   ② 提交时申请工单（工单2）透传 metadata_info.new_project_info + 描述富化文本；
//   ③ 手动取消勾选 = 清空自定义名与表单，回到普通选项目。
// 只断言用户可见行为（testid / role / 文本），不认实现细节。
import { useSyncExternalStore } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ---- workbench store mock：zustand 语义（setState 合并+通知，订阅组件重渲染）----
const listeners = new Set<() => void>();
const authState = { token: 'tok', name: '测试', username: 'tester' };

vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    (sel?: (s: typeof authState) => unknown) => (typeof sel === 'function' ? sel(authState) : authState),
    { getState: () => authState },
  ),
}));

interface WbState {
  chatContext: unknown;
  consumeChatContext: () => unknown;
  refreshTasks: () => void;
  tasksRefreshKey: number;
  conversationId: number | null;
  setConversationId: (id: number | null) => void;
  setConversationTitle: (t: string) => void;
  renameConversation: (id: number, t: string) => void;
  refreshConversations: () => Promise<void>;
  requestNewConversation: () => void;
  vehicleContext: unknown;
  consumeVehicleContext: () => unknown;
  conversations: unknown[];
  pendingNewConversation: boolean;
}

const RESTORED_SID = 'sess_draft_test_001';
let wbState: WbState;
let storeRef: { getState: () => WbState; setState: (patch: Partial<WbState>) => void };

vi.mock('@/stores/workbench', () => ({
  useWorkbenchStore: Object.assign(
    (sel?: (s: WbState) => unknown) =>
      useSyncExternalStore(
        (l) => { listeners.add(l); return () => { listeners.delete(l); }; },
        () => (typeof sel === 'function' ? sel(wbState) : wbState),
      ),
    {
      getState: () => wbState,
      setState: (patch: Partial<WbState>) => {
        wbState = { ...wbState, ...patch };
        listeners.forEach((l) => l());
      },
    },
  ),
}));

// ---- 网络/组件出口 mock ----
const mockPrepare = vi.fn();
const mockConfirm = vi.fn();
const mockSteps = vi.fn();
const mockProjectMatch = vi.fn();
const mockAskStream = vi.fn();
const mockCreateTicket = vi.fn();

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>();
  return {
    ...actual,
    qaPrepareTicket: () => mockPrepare(),
    qaConfirmTicket: (sid: string, ov: unknown) => mockConfirm(sid, ov),
    qaGetTicketSteps: (t: string) => mockSteps(t),
    qaProjectMatch: (sid: string) => mockProjectMatch(sid),
    fetchWithAuth: (path: string, init: RequestInit) => mockAskStream(path, init),
  };
});

vi.mock('@/api/ticket', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ticket')>();
  return {
    ...actual,
    createTicket: (p: unknown) => mockCreateTicket(p),
    uploadCommentAttachment: vi.fn(async () => '/tmp/shot.png'),
    fetchRedispatch: vi.fn(async () => ({ candidates: [] })),
    getOnBehalfCandidates: vi.fn(async () => []),
  };
});

vi.mock('@/api/conversation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/conversation')>();
  return {
    ...actual,
    getConversation: (id: number) => mockGetConversation(id),
    createConversation: (p: { title: string; scene: string; aiSessionId?: string }) => mockCreateConversation(p),
    appendMessage: (cid: number, role: string, content: string, opts?: unknown) => mockAppendMessage(cid, role, content, opts),
    updateMessageContent: vi.fn().mockResolvedValue(undefined),
    updateConversationMeta: vi.fn().mockResolvedValue(undefined),
  };
});
const mockGetConversation = vi.fn();
const mockCreateConversation = vi.fn();
const mockAppendMessage = vi.fn();

// 项目下拉数据源：空列表 = 任何搜索词都无匹配（必然走「作为新项目」路径）
vi.mock('@/api/projects', () => ({
  getProjects: vi.fn(async () => []),
  getMyProjectRelevance: vi.fn(async () => ({ ticketed: [], owned: [] })),
}));

// 项目负责人选择器：mock 成一次点击即选中固定负责人（按钮文本带 placeholder，
// 与弹窗内共享文档设置的 UserSelect「选择要请谁补充」区分开）
vi.mock('@/shared/components/UserSelect', () => ({
  default: ({
    onChange,
    placeholder,
  }: {
    onChange?: (u: { id: string; name: string; username: string }) => void;
    placeholder?: string;
  }) => (
    <button
      type="button"
      data-testid="mock-user-select"
      onClick={() => onChange?.({ id: 'u1', name: '负责人甲', username: 'owner1' })}
    >
      {placeholder ?? 'mock 选择用户'}
    </button>
  ),
}));

// 被代理人不参与本用例，mock 成空渲染
vi.mock('@/shared/components/OnBehalfSelect', () => ({
  default: () => <div data-testid="mock-onbehalf" />,
}));

import ChatPanel from '../ChatPanel';
import { useWorkbenchStore } from '@/stores/workbench';

const renderPanel = () =>
  render(
    <MemoryRouter>
      <ChatPanel scene="call" />
    </MemoryRouter>,
  );

const flush = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

// Popup（tdesign + react-transition-group）的开/关动画靠 setTimeout 驱动：
// RTL auto-cleanup 卸载组件后这些 timer 仍在队列里，测试环境拆除后才触发，
// 访问 window 报 ReferenceError（vitest 记 unhandled error → exit 1）。
// 每个用例结束前等动画 timers 落定（tdesign 退场动画 ~300ms，给 700ms 余量）。
afterEach(async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 700)); });
});

/** 弹窗就绪前提：会话恢复（sid + 一条用户消息）→ 点转工单 → draft_ready 弹窗打开 */
const openDraftModal = async () => {
  storeRef = useWorkbenchStore as unknown as typeof storeRef;
  renderPanel();
  await waitFor(() => expect(mockGetConversation).toHaveBeenCalledWith(7));
  await flush();

  await act(async () => {
    fireEvent.click(screen.getByTestId('chat-transfer-ticket'));
  });
  await waitFor(() => expect(screen.getByTestId('chat-ticket-draft-modal')).toBeTruthy());
  await flush(); // 等 loadTicketSteps 回填默认阶段与截止时间
};

/** 在项目下拉里搜索并选中「作为新项目」条目（ORS-1023 入口） */
const pickCustomProject = async (name: string) => {
  await act(async () => {
    fireEvent.click(screen.getByText('请选择绑定项目'));
  });
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText('搜索项目名称 / 编码…'), { target: { value: name } });
  });
  const createItem = await screen.findByTestId('project-select-create');
  await act(async () => {
    fireEvent.click(createItem);
  });
};

describe('ChatPanel 草稿弹窗：输入不存在的新项目（ORS-1023）', () => {
  beforeEach(() => {
    listeners.clear();
    mockPrepare.mockReset();
    mockConfirm.mockReset();
    mockSteps.mockReset();
    mockProjectMatch.mockReset();
    mockAskStream.mockReset();
    mockCreateTicket.mockReset();
    mockGetConversation.mockReset();
    mockCreateConversation.mockReset();
    mockAppendMessage.mockReset();

    wbState = {
      chatContext: null,
      consumeChatContext: vi.fn(() => null),
      refreshTasks: vi.fn(),
      tasksRefreshKey: 0,
      conversationId: 7,
      setConversationId: (id) => storeRef.setState({ conversationId: id }),
      setConversationTitle: vi.fn(),
      renameConversation: vi.fn(),
      refreshConversations: vi.fn().mockResolvedValue(undefined),
      requestNewConversation: vi.fn(),
      vehicleContext: null,
      consumeVehicleContext: vi.fn(() => null),
      conversations: [],
      pendingNewConversation: false,
    };

    // 恢复出带 sid 的会话 + 一条用户消息（转工单前置校验：messages 非空）
    mockGetConversation.mockResolvedValue({
      id: 7,
      title: '草稿测试会话',
      metadata_: JSON.stringify({ ai_session_id: RESTORED_SID }),
      messages: [
        { id: 1, role: 'user', content: '机器人原地转圈', created_at: '2026-10-20T01:00:00Z', metadata_: null },
      ],
    });
    mockAskStream.mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => ({ done: true, value: undefined }),
          releaseLock: () => {},
        }),
      },
    });
    mockCreateConversation.mockResolvedValue({ id: 99, title: '', metadata_: null });
    mockAppendMessage.mockResolvedValue({ id: 1 });
    mockProjectMatch.mockResolvedValue([]);
    mockPrepare.mockResolvedValue({
      code: 0,
      data: {
        stage: 'draft_ready',
        draft: {
          ticket_id: 'TK-1',
          type: 'problem',
          title: '机器人异常',
          description: '机器人原地转圈',
          priority: '中',
          project: '',
          project_id: '',
        },
      },
    });
    // steps.id 必须是数字型字符串：loadTicketSteps 里 Number(steps[0].id) 回填
    // curr_step_id，非数字 id 会变 NaN（falsy）被「请选择处理阶段」校验拦下
    mockSteps.mockResolvedValue({
      code: 0,
      data: { steps: [{ id: 101, step_name: '远程排查' }] },
    });
    // db_id/id 置 0（falsy）：提交后不启动派单轮询——5s 定时器会在测试环境拆除后
    // 触发 fetch（window is not defined），本用例断言不依赖轮询结果
    mockConfirm.mockResolvedValue({
      code: 0,
      data: { ticket: { ticket_id: 'TK-1', title: '机器人异常', type: 'problem', priority: '中', project: '摇人吧服务号提单' }, db_id: 0, notice: '' },
    });
    mockCreateTicket.mockResolvedValue({ id: 0 });
  });

  it('选新项目 → 自动勾选兜底 + 出现选填表单 → 提交随申请工单透传 new_project_info', async () => {
    await openDraftModal();

    // ① 输入不存在的项目名 → 选中「作为新项目」
    await pickCustomProject('全新测试项目');

    // ② 兜底双工单复选框被自动勾选
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    // 触发框回显自定义项目名
    expect(screen.getByText('全新测试项目')).toBeTruthy();

    // ③ 新项目基本信息表单出现（选填三件套）
    expect(screen.getByTestId('new-project-info')).toBeTruthy();
    await act(async () => {
      fireEvent.change(screen.getByTestId('new-project-code'), { target: { value: 'X-test-001' } });
      fireEvent.change(screen.getByTestId('new-project-customer'), { target: { value: '客户乙' } });
      fireEvent.change(screen.getByTestId('new-project-region'), { target: { value: '杭州市滨江区' } });
    });

    // ④ 双工单必填：选项目负责人（按钮文本 = 弹窗内该选择器的 placeholder）
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '请选择项目负责人' }));
    });

    // ⑤ 提交
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-ticket-confirm'));
    });

    // 工单1（confirm_submit）：项目强制兜底，project_id 清空
    await waitFor(() => expect(mockConfirm).toHaveBeenCalledTimes(1));
    const confirmOverrides = mockConfirm.mock.calls[0][1] as Record<string, string>;
    expect(confirmOverrides.project).toBe('摇人吧服务号提单');
    expect(confirmOverrides.project_id).toBe('');

    // 工单2（申请单）：结构化 new_project_info + 描述富化 + 指派项目负责人
    await waitFor(() => expect(mockCreateTicket).toHaveBeenCalledTimes(1));
    const payload = mockCreateTicket.mock.calls[0][0] as {
      title: string;
      description: string;
      assigned_to: string;
      metadata_info?: { new_project_info?: { name: string; code: string; customer: string; region: string } };
    };
    expect(payload.title).toBe('【项目申请】请求新建项目/添加用户');
    expect(payload.assigned_to).toBe('u1');
    expect(payload.metadata_info?.new_project_info).toEqual({
      name: '全新测试项目',
      code: 'X-test-001',
      customer: '客户乙',
      region: '杭州市滨江区',
    });
    expect(payload.description).toContain('【新项目基本信息】');
    expect(payload.description).toContain('项目名称：全新测试项目');
    expect(payload.description).toContain('项目编号：X-test-001');
    expect(payload.description).toContain('客户信息：客户乙');
    expect(payload.description).toContain('项目区域/地点：杭州市滨江区');
    expect(payload.description).toContain('机器人原地转圈');
  });

  it('取消勾选复选框：清空自定义名与表单，回到普通选项目', async () => {
    await openDraftModal();
    await pickCustomProject('全新测试项目');

    // 自动勾选 + 表单出现的既有态
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByTestId('new-project-info')).toBeTruthy();

    // 取消勾选
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox'));
    });

    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
    // 表单与自定义名都消失，触发框回到占位文案
    expect(screen.queryByTestId('new-project-info')).toBeNull();
    expect(screen.queryByText('全新测试项目')).toBeNull();
    expect(screen.getByText('请选择绑定项目')).toBeTruthy();
  });

  it('普通兜底勾选（未输入新项目名）：不出现新项目信息表单', async () => {
    await openDraftModal();

    // 用户直接手动勾选兜底（项目在系统里、只是不在自己项目集的老场景）
    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox'));
    });

    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByTestId('new-project-info')).toBeNull();
  });
});
