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
  };
});

const VEHICLE_CTX = {
  scene: 'proj_abc123def456',
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
    // confirm 成功：带开场大方向引导题（点类目即走 send 主路径）
    mockModeConfirm.mockResolvedValue({
      code: 0,
      data: {
        confirmed: true,
        model: 'XQE',
        domain: 'company',
        manual_docs: [],
        opening: {
          question: '您遇到了什么问题？',
          choices: ['故障码', '界面报故障码', '自检失败 / 开机异常'],
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
    const registeredSid = (mockModeConfirm.mock.calls[0][0] as { session_id: string }).session_id;
    expect(registeredSid).toMatch(/^sess_/);
    // 不能用恢复出来的旧会话 sid（扫码 = 这次专用的空白会话）
    expect(registeredSid).not.toBe(OLD_SID);

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
      expect.objectContaining({ aiSessionId: registeredSid }),
    );
  });
});
