// ChatPanel 车体扫码链路回归：车型模式注册与首问必须共用同一个 session_id。
//
// 回归背景（0930 线上实锤）：扫码确认时 requestNewConversation()（conversationId→null）
// 与 setVehicleContext() 被 React 批到同一次 render，同一 commit 内 effect 按声明顺序
// 执行——[conversationId] effect 先跑并排队 setSessionId('')（下一轮渲染才生效），
// [vehicleContext] effect 后跑但 ensureSessionId() 读到的是本轮闭包里「页面进入时
// 自动恢复出的旧会话 sid」。结果：注册落在旧 sid 上，而用户点开场题首问时
// sessionId 已被清空、ensureSessionId 改生成新 sid——注册与提问两个 session，
// 车型模式对首问不可见（落默认三域检索、team 老内容串味）。
//
// 本用例复刻该时序（恢复旧会话 → 触发扫码确认 → 点开场题类目），断言：
//   ① qaModeConfirm 注册用的 sid === 首问 /qa/ask/stream 用的 sid；
//   ② 该 sid 不是被恢复的旧 sid（扫码专用空白会话，不污染历史会话）。
import { useSyncExternalStore } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
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

const OLD_SID = 'sess_restored_old_001';
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

import ChatPanel from '../ChatPanel';
import { useWorkbenchStore } from '@/stores/workbench';

// ---- api mock：保留真实的 generateSessionId/trackSession，只桩掉网络出口 ----
const mockModeConfirm = vi.fn();
const mockAskStream = vi.fn();
const mockGetConversation = vi.fn();
const mockCreateConversation = vi.fn();
const mockAppendMessage = vi.fn();
const mockUpdateConversationMeta = vi.fn();

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>();
  return {
    ...actual,
    qaModeConfirm: (payload: unknown) => mockModeConfirm(payload),
    // 捕获首问请求体（含 session_id），返回即刻结束的空 SSE 流
    fetchWithAuth: (path: string, init: RequestInit) => mockAskStream(path, init),
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
    updateConversationMeta: (...args) => mockUpdateConversationMeta(...args),
  };
});

const VEHICLE_CTX = {
  scene: '101',
  projectName: '项目A',
  customerName: '客户A',
  vehicleModel: 'XQE',
};

const renderPanel = () =>
  render(
    <MemoryRouter>
      <ChatPanel scene="call" />
    </MemoryRouter>,
  );

/** 冲掉微任务+宏任务队列，等异步 effect 链（会话恢复/注册响应）落定 */
const flush = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

describe('ChatPanel 车体扫码：车型模式注册与首问同 session_id', () => {
  beforeEach(() => {
    listeners.clear();
    mockModeConfirm.mockReset();
    mockAskStream.mockReset();
    mockGetConversation.mockReset();
    mockCreateConversation.mockReset();
    mockAppendMessage.mockReset();
    mockUpdateConversationMeta.mockReset();

    // 初始态：页面进入时已自动选中最近会话（conversationId=7），
    // 其 ai_session_id 会被 Effect A 恢复进 sessionId（旧 sid 来源）。
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
      consumeVehicleContext: vi.fn(() => {
        const c = wbState.vehicleContext;
        if (c) storeRef.setState({ vehicleContext: null });
        return c;
      }),
      conversations: [],
      pendingNewConversation: false,
    };

    // 旧会话（自动选中的那条）：恢复出历史 ai_session_id
    mockGetConversation.mockImplementation(async (id: number) =>
      id === 7
        ? { id: 7, title: '旧会话', messages: [], metadata_: JSON.stringify({ ai_session_id: OLD_SID }) }
        : { id, title: '新会话', messages: [], metadata_: null },
    );
    // confirm 成功：带开场大方向引导题（点类目即走 send 主路径）+ 1012 新增的
    // 车型资料引导条与折叠类目
    mockModeConfirm.mockResolvedValue({
      code: 0,
      data: {
        confirmed: true,
        model: 'XQE',
        domain: 'company',
        manual_docs: [],
        vehicle_guide: {
          question: '想了解 XQE 车型信息？点击查看型号介绍',
          choices: ['XQE122 车型介绍', 'XQE151 车型介绍'],
        },
        opening: {
          question: '您遇到了什么问题？',
          choices: ['界面报故障码', '多车调度', '卸货', '取货'],
          collapsed_choices: ['建图与定位', '充电异常', '避障异常', '自检失败 / 开机异常', '远程接管'],
          hint: '若是其他情况，请在下方输入框描述',
        },
      },
    });
    // 首问流式：空 SSE（即刻 done），只关心请求体里的 session_id
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
    // 发送前落库：建会话（回写 aiSessionId，模拟真实 createConversation）+ 追加用户消息
    mockCreateConversation.mockImplementation(async (p: { title?: string; aiSessionId?: string }) => ({
      id: 99,
      title: p.title ?? '',
      metadata_: JSON.stringify({ ai_session_id: p.aiSessionId || '' }),
      service_ticket_id: p.aiSessionId || '',
    }));
    mockAppendMessage.mockResolvedValue({ id: 1 });
  });

  it('注册 sid 与首问 sid 相同，且不使用恢复出的旧会话 sid', async () => {
    // storeRef 指向 mock store 的 getState/setState（工厂惰性执行，此时已就绪）
    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();

    // 1. 等旧会话恢复完成（Effect A：getConversation → setSessionId(旧sid)）
    await waitFor(() => expect(mockGetConversation).toHaveBeenCalledWith(7));
    await flush();

    // 2. 模拟扫码确认：requestNewConversation() + setVehicleContext() 同批落地
    await act(async () => {
      storeRef.setState({ conversationId: null, vehicleContext: VEHICLE_CTX });
    });

    // 3. 确认即注册：qaModeConfirm 被调用，捕获注册 sid
    await waitFor(() => expect(mockModeConfirm).toHaveBeenCalledTimes(1));
    const confirmPayload = mockModeConfirm.mock.calls[0][0] as {
      session_id: string;
      scene: string;
      model: string;
    };
    const registeredSid = confirmPayload.session_id;
    expect(registeredSid).toMatch(/^sess_/);
    // 不能用恢复出来的旧会话 sid（扫码 = 这次专用的空白会话）
    expect(registeredSid).not.toBe(OLD_SID);
    // 必须透传 scene（= 行 id）：后端按它精确取「扫的那一行」录入信息，缺了注册就失败
    expect(confirmPayload.scene).toBe('101');
    expect(confirmPayload.model).toBe('XQE');

    // 4. 开场题气泡渲染后点类目（= 用户真实路径：点选项即发送全文）
    const chip = await waitFor(() => screen.getByRole('button', { name: '界面报故障码' }));
    await act(async () => { fireEvent.click(chip); });

    // 5. 首问 /qa/ask/stream 发出，捕获请求体里的 session_id
    await waitFor(() => expect(mockAskStream).toHaveBeenCalledTimes(1));
    const askBody = JSON.parse((mockAskStream.mock.calls[0][1] as RequestInit).body as string);

    // 6. 核心断言：注册与首问同一个 session（旧实现这里会是两个不同 sid）
    expect(askBody.session_id).toBe(registeredSid);
    // 建会话时落库的 aiSessionId 也与该 sid 一致（会话↔session 映射不错位）
    expect(mockCreateConversation).toHaveBeenCalledWith(
      expect.objectContaining({ aiSessionId: registeredSid, vehicle: 'XQE' }),
    );
  });

  it('开场引导：车型介绍并入确认气泡，开场题常驻 4、展开后九类齐全、两条气泡落库', async () => {
    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();

    await waitFor(() => expect(mockGetConversation).toHaveBeenCalledWith(7));
    await flush();
    await act(async () => {
      storeRef.setState({ conversationId: null, vehicleContext: VEHICLE_CTX });
    });
    await waitFor(() => expect(mockModeConfirm).toHaveBeenCalledTimes(1));
    await flush();

    // ① 确认气泡：车型介绍按钮并入这条气泡（1014 合并），不再有独立的引导题面
    expect(await screen.findByText(/车辆信息已确认/)).toBeTruthy();
    expect(screen.queryByText('想了解 XQE 车型信息？点击查看型号介绍')).toBeNull();
    const intro122 = screen.getByRole('button', { name: /XQE122 车型介绍/ });
    const intro151 = screen.getByRole('button', { name: /XQE151 车型介绍/ });
    // 视觉区分（1014）：车型介绍按钮用 intro 变体（区别于开场题的蓝色胶囊）
    expect(intro122.className).toContain('chat-veh-chip--intro');
    expect(intro151.className).toContain('chat-veh-chip--intro');

    // ② 开场题常驻 4 全渲染，折叠 5 类默认不出现；开场题按钮不带 intro 变体
    for (const c of ['界面报故障码', '多车调度', '卸货', '取货']) {
      expect(screen.getByRole('button', { name: c })).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: '界面报故障码' }).className).not.toContain('chat-veh-chip--intro');
    for (const c of ['建图与定位', '充电异常', '远程接管']) {
      expect(screen.queryByRole('button', { name: c })).toBeNull();
    }

    // ③ 「其他情况」提示语只属于大方向题：确认气泡下面不挂
    expect(screen.getAllByText('若是其他情况，请在下方输入框描述')).toHaveLength(1);

    // ④ 落库（1014 持久化）：建会话 + 两条 assistant 消息带 metadata
    await waitFor(() => expect(mockAppendMessage).toHaveBeenCalledTimes(2));
    const first = mockAppendMessage.mock.calls[0] as [number, string, string, { metadata?: string }];
    const second = mockAppendMessage.mock.calls[1] as [number, string, string, { metadata?: string }];
    expect(first[1]).toBe('assistant');
    expect(first[2]).toContain('**车辆信息已确认**');
    // 尾句「接下来按该车型的引导流程…」已删（用户反馈多余）：确认气泡只有
    // 标题 + 三行 key-value
    expect(first[2]).not.toContain('接下来按该车型的引导流程');
    // key-value 行尾双空格 = markdown 硬换行（renderer 无 remark-breaks，
    // 单 \n 会被合并成一段——实测「项目一行、客户车型挤一行」）
    expect(first[2]).toContain('**项目**：项目A  \n**客户**：客户A  \n**车型**：XQE');
    expect(JSON.parse(first[3].metadata || '{}')).toMatchObject({
      vehicle_confirm: true,
      vehicle_guide: true,
      vehicle_choices: ['XQE122 车型介绍', 'XQE151 车型介绍'],
    });
    expect(second[2]).toBe('您遇到了什么问题？');
    expect(JSON.parse(second[3].metadata || '{}')).toMatchObject({
      vehicle_opening: true,
      vehicle_choices: ['界面报故障码', '多车调度', '卸货', '取货'],
      vehicle_collapsed: ['建图与定位', '充电异常', '避障异常', '自检失败 / 开机异常', '远程接管'],
    });

    // ⑤ 点「展开全部」：折叠 5 类补齐（九大类全部可达），开关文案切「收起」
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /展开全部/ }));
    });
    for (const c of ['建图与定位', '充电异常', '避障异常', '自检失败 / 开机异常', '远程接管']) {
      expect(screen.getByRole('button', { name: c })).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: '收起' })).toBeTruthy();

    // ⑥ 点折叠里的类目 = 按选项原文发送（走正常检索链路）
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '建图与定位' }));
    });
    await waitFor(() => expect(mockAskStream).toHaveBeenCalledTimes(1));
    const askBody = JSON.parse((mockAskStream.mock.calls[0][1] as RequestInit).body as string);
    expect(askBody.query).toBe('建图与定位');
  });

  it('历史恢复：落库的引导气泡按 metadata 重建按钮（切会话/刷新后引导不丢）', async () => {
    // 旧会话 7 带两条落库的引导消息（1014 持久化的形态）
    mockGetConversation.mockImplementation(async (id: number) =>
      id === 7
        ? {
            id: 7,
            title: '扫码会话',
            metadata_: null,
            messages: [
              {
                id: 101,
                role: 'assistant',
                content: '**车辆信息已确认**\n\n**项目**：项目A\n**客户**：客户A\n**车型**：XQE\n\n接下来按该车型的引导流程协助你，直接描述现象或点下面的选项即可。',
                created_at: '2026-10-14T01:00:00Z',
                metadata_: JSON.stringify({
                  vehicle_confirm: true,
                  vehicle_guide: true,
                  vehicle_choices: ['XQE122 车型介绍', 'XQE151 车型介绍'],
                }),
              },
              {
                id: 102,
                role: 'assistant',
                content: '您遇到了什么问题？',
                created_at: '2026-10-14T01:00:01Z',
                metadata_: JSON.stringify({
                  vehicle_opening: true,
                  vehicle_choices: ['界面报故障码', '多车调度'],
                  vehicle_collapsed: ['充电异常'],
                }),
              },
            ],
          }
        : { id, title: '新会话', messages: [], metadata_: null },
    );

    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();

    // 恢复完成：两条气泡的按钮组都按 metadata 重建（intro 变体 + 折叠开关）
    expect(await screen.findByRole('button', { name: /XQE122 车型介绍/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /XQE151 车型介绍/ }).className).toContain('chat-veh-chip--intro');
    expect(screen.getByRole('button', { name: '界面报故障码' }).className).not.toContain('chat-veh-chip--intro');
    expect(screen.getByRole('button', { name: /展开全部（还有 1 类）/ })).toBeTruthy();

    // 开场题大字题面（vehicleOpening 标记恢复）
    expect(screen.getByText('您遇到了什么问题？')).toBeTruthy();
    // 提示语只挂在开场题下（确认气泡 vehicleGuide 恢复后不挂）
    expect(screen.getAllByText('若是其他情况，请在下方输入框描述')).toHaveLength(1);

    // 存量自愈：会话有 vehicle_confirm 引导消息但 metadata 缺 vehicle 标记
    // （角标功能上线前的老扫码会话）→ 恢复时自动从确认气泡提取车型回填，
    // 下次拉列表即有角标（fire-and-forget，无需 SQL 补数据）
    await waitFor(() => expect(mockUpdateConversationMeta).toHaveBeenCalledTimes(1));
    expect(mockUpdateConversationMeta).toHaveBeenCalledWith(7, { vehicle: 'XQE' });
  });
});
