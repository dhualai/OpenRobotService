import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// 登录态与工作台 store mock（ChatPanel 挂载只依赖字段与回调存在）
const authState = { token: 'tok', name: '测试', username: 'tester' };
vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    (sel?: (s: typeof authState) => unknown) =>
      typeof sel === 'function' ? sel(authState) : authState,
    { getState: () => authState },
  ),
}));
const wbState = {
  chatContext: null, consumeChatContext: vi.fn(), refreshTasks: vi.fn(),
  tasksRefreshKey: 0, conversationId: null, setConversationId: vi.fn(),
  setConversationTitle: vi.fn(), renameConversation: vi.fn(),
  refreshConversations: vi.fn(), requestNewConversation: vi.fn(),
  // getState() 解构字段（挂载 effect 直接读）
  conversations: [], pendingNewConversation: false,
};
vi.mock('@/stores/workbench', () => ({
  // zustand store 兼 getState（ChatPanel 挂载 effect 直接调用）
  useWorkbenchStore: Object.assign(
    (sel?: (s: typeof wbState) => unknown) =>
      typeof sel === 'function' ? sel(wbState) : wbState,
    { getState: () => wbState },
  ),
}));
// 挂载期的会话列表等请求不落地：永不 resolve，组件停在空态+输入栏
vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));

import ChatPanel from '../ChatPanel';
import { AI_INPUT_PLACEHOLDER_TIPS } from '../ChatPanel';

const renderPanel = () =>
  render(
    <MemoryRouter>
      <ChatPanel scene="call" />
    </MemoryRouter>,
  );

describe('ChatPanel 输入框轮播提示（仅 call 场景）', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('placeholder 随 3s 定时轮换，输入有内容时轮播仍在（placeholder 不显示）', () => {
    renderPanel();
    expect(screen.getByPlaceholderText(AI_INPUT_PLACEHOLDER_TIPS[0]))
      .toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.getByPlaceholderText(AI_INPUT_PLACEHOLDER_TIPS[1]))
      .toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(3000 * (AI_INPUT_PLACEHOLDER_TIPS.length - 1)); });
    // 转一整圈回到第一条
    expect(screen.getByPlaceholderText(AI_INPUT_PLACEHOLDER_TIPS[0]))
      .toBeInTheDocument();
  });

  it('非 call 场景保持静态 placeholder，不轮播', () => {
    render(
      <MemoryRouter>
        <ChatPanel scene="tasks" />
      </MemoryRouter>,
    );
    expect(screen.getByPlaceholderText('发消息…')).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(9000); });
    expect(screen.getByPlaceholderText('发消息…')).toBeInTheDocument();
  });
});
