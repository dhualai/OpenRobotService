// ChatPanel 车体扫码 SOP 问答轮：回答气泡下渲染「相关操作手册」入口（ORS-1082）。
//
// 需求背景（工单 ORS-1082）：扫码 SOP 的问答界面只给文字回答，不给操作手册
// 入口——用户看完回答不知道去哪看完整手册。服务端按本轮检索实际命中的手册
// 文件随 result 事件下发（vehicle_sop_docs），前端在气泡下渲染手册入口，
// 点击复用既有 SOP 抽屉在线看全文。
//
// 本用例覆盖三条链路：
//   ① result 带 vehicle_sop_docs → 气泡下出现手册入口，点击打开抽屉；
//   ② result 不带手册（普通会话/无命中）→ 不渲染手册行；
//   ③ 历史恢复：落库的 metadata_.vehicle_manuals → 切会话/刷新后入口仍在。
import { useSyncExternalStore } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, waitFor, fireEvent, cleanup } from '@testing-library/react';
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
const mockAskStream = vi.fn();
const mockGetConversation = vi.fn();
const mockCreateConversation = vi.fn();
const mockAppendMessage = vi.fn();
const mockModeConfirm = vi.fn();
const globalFetch = vi.fn();

vi.mock('@/api/ai', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/ai')>();
  return {
    ...actual,
    qaModeConfirm: (payload: unknown) => mockModeConfirm(payload),
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
    updateConversationMeta: vi.fn().mockResolvedValue(undefined),
  };
});

const MANUAL_URL = '/api/ai/media/kb/company/XQE/36_异常处理标准流程SOP.md';
const USP_MANUAL_URL = '/api/ai/media/kb/team/USP/manual/manual.md';

/** 造一条 SSE 流响应（ChatPanel 用 response.body.getReader() 逐行解析）
 *
 *  格式对齐真实后端 ai/api/router.py：
 *  - token：**只有 data 行**（无 event 行），载荷 {"token": "文本"}
 *  - result / done / title：event 行 + data 行
 */
function sseResponse(events: Array<[string, unknown]>) {
  const parts: string[] = [];
  for (const [ev, data] of events) {
    if (ev === 'token') {
      parts.push(`data: ${JSON.stringify({ token: data })}\n\n`);
    } else {
      parts.push(`event: ${ev}\ndata: ${JSON.stringify(data)}\n\n`);
    }
  }
  const bytes = new TextEncoder().encode(parts.join(''));
  let done = false;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (done) return { done: true, value: undefined };
          done = true;
          return { done: false, value: bytes };
        },
        releaseLock: () => {},
      }),
    },
  };
}

const renderPanel = () =>
  render(
    <MemoryRouter>
      <ChatPanel scene="call" />
    </MemoryRouter>,
  );

const flush = async () => { await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

/** 在输入框里发一条消息（走 send 主链路 → SSE → result） */
async function sendMessage(text: string) {
  const input = screen.getByTestId('chat-input');
  await act(async () => { fireEvent.change(input, { target: { value: text } }); });
  await act(async () => { fireEvent.click(screen.getByTestId('chat-send')); });
}

describe('ChatPanel 扫码 SOP 问答：相关操作手册入口（ORS-1082）', () => {
  beforeEach(() => {
    // RTL 的 auto-cleanup 在本项目未生效（globals 模式不触发）→ 显式清理：
    // 否则上一例的 DOM 与 loading 锁残留，下一例的发送会被拦（气泡停在思考中）
    cleanup();
    // cleanup 只 unmount 不清空容器 → 再粗暴清一次 body，杜绝跨用例 DOM 残留
    document.body.innerHTML = '';
    listeners.clear();
    mockAskStream.mockReset();
    mockGetConversation.mockReset();
    mockCreateConversation.mockReset();
    mockAppendMessage.mockReset();
    mockModeConfirm.mockReset();
    globalFetch.mockReset();
    vi.stubGlobal('fetch', globalFetch);
    globalFetch.mockResolvedValue({
      ok: true,
      text: async () => '# 异常处理标准流程\n\n1. 停机断电\n2. 检查货叉',
    });

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

    mockGetConversation.mockImplementation(async (id: number) =>
      id === 7
        ? { id: 7, title: '旧会话', messages: [], metadata_: null }
        : { id, title: '新会话', messages: [], metadata_: null },
    );
    mockCreateConversation.mockImplementation(async (p: { title?: string; aiSessionId?: string }) => ({
      id: 99,
      title: p.title ?? '',
      metadata_: JSON.stringify({ ai_session_id: p.aiSessionId || '' }),
      service_ticket_id: p.aiSessionId || '',
    }));
    mockAppendMessage.mockResolvedValue({ id: 1 });
  });

  it('result 带手册清单 → 回答气泡下渲染手册入口，点击打开全文抽屉', async () => {
    mockAskStream.mockResolvedValue(sseResponse([
      ['token', '先确认一下车端提示。'],
      ['result', {
        type: 'diagnosis',
        action: 'answer',
        message: '先确认一下车端提示。',
        vehicle_sop_docs: [
          { title: '36_异常处理标准流程SOP', url: MANUAL_URL },
          { title: 'manual', url: USP_MANUAL_URL },
        ],
      }],
      ['done', {}],
    ]));

    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();
    await flush();

    await sendMessage('车不动了怎么办');

    // 手册入口渲染在回答气泡下（标题 + 两个手册按钮）
    await waitFor(() => expect(screen.getByText('相关操作手册')).toBeTruthy());
    const chip = screen.getByRole('button', { name: '36_异常处理标准流程SOP' });
    expect(screen.getByRole('button', { name: 'manual' })).toBeTruthy();

    // 点击 → 拉该手册的 md 正文（URL 必须是服务端下发的那个）
    await act(async () => { fireEvent.click(chip); });
    await waitFor(() => expect(globalFetch).toHaveBeenCalled());
    expect(String(globalFetch.mock.calls[0][0])).toContain(MANUAL_URL);
    // 抽屉打开并渲染正文
    await waitFor(() => expect(screen.getByText(/停机断电/)).toBeTruthy());
  });

  it('没有手册数据 → 不渲染手册行（普通会话/无命中不空挂一行）', async () => {
    // 历史消息不带 vehicle_manuals → 恢复后不得出现手册行
    mockGetConversation.mockImplementation(async (id: number) =>
      id === 7
        ? {
            id: 7,
            title: '普通会话',
            metadata_: null,
            messages: [
              { id: 201, role: 'assistant', content: '这个我没查到。', metadata_: null },
            ],
          }
        : { id, title: '新会话', messages: [], metadata_: null },
    );

    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();

    await waitFor(() => expect(screen.getByText('这个我没查到。')).toBeTruthy());
    expect(screen.queryAllByText('相关操作手册')).toHaveLength(0);
  });

  it('历史恢复：落库的 metadata_.vehicle_manuals → 手册入口重建', async () => {
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
                content: '先看这两篇手册。',
                metadata_: JSON.stringify({
                  vehicle_manuals: [{ title: '36_异常处理标准流程SOP', url: MANUAL_URL }],
                }),
              },
            ],
          }
        : { id, title: '新会话', messages: [], metadata_: null },
    );

    storeRef = useWorkbenchStore as unknown as typeof storeRef;
    renderPanel();

    await waitFor(() => expect(screen.getByText('相关操作手册')).toBeTruthy());
    expect(screen.getByRole('button', { name: '36_异常处理标准流程SOP' })).toBeTruthy();
  });
});
