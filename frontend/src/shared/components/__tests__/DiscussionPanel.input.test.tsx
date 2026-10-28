// 讨论区输入框「自验」用例：确认输入值在组件内不被任何转义/改写逻辑篡改。
//
// 背景（用户反馈：输入评论时先「闪一下」，随后冒出一长串反斜杠）：
//   怀疑点是 DiscussionPanel 的受控 textarea（value={commentText} + onChange）
//   在输入过程中对内容做了转义/拼接，或 IME 组合输入期间被重置。
// 本用例锁定「输入框不会自己造出反斜杠」这一事实，为定位到底是代码 bug
// 还是用户粘贴/输入法侧带入提供判据。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

// ---- auth store 替身：无权限（不拉 USP 环境列表），头像/名字为空 ----
vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    () => ({ username: 'tester', name: '测试', avatarResourceId: null, hasPermission: () => false }),
    { getState: () => ({ username: 'tester', name: '测试', avatarResourceId: null, hasPermission: () => false }) },
  ),
}));

// ---- 只读名单接口不触网 ----
vi.mock('@/api/taskRead', () => ({
  fetchCommentReadList: vi.fn().mockResolvedValue([]),
  reportCommentRead: vi.fn().mockResolvedValue(undefined),
}));

import DiscussionPanel from '../DiscussionPanel';

const getInput = () => screen.getByRole('textbox') as HTMLTextAreaElement;

describe('DiscussionPanel 输入框：值不被改写', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('输入含反斜杠的文本：textarea 值与用户输入逐字一致（不增不减反斜杠）', () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<DiscussionPanel comments={[]} onSend={onSend} />);
    const input = getInput();

    // 典型场景：正文 + 一串反斜杠
    const typed = '会议结论：库位空调逻辑需要增加预设逻辑，对于库位状态需要加读写锁，\\\\\\\\\\\\';
    fireEvent.change(input, { target: { value: typed } });

    expect(input.value).toBe(typed);
    expect(input.value.match(/\\/g)?.length).toBe(typed.match(/\\/g)?.length);
  });

  it('连续多次输入（含 IME 组合事件）：不会累积出额外字符', () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<DiscussionPanel comments={[]} onSend={onSend} />);
    const input = getInput();

    fireEvent.compositionStart(input);
    fireEvent.change(input, { target: { value: '会议结论' } });
    fireEvent.compositionUpdate(input, { data: '会议结论' });
    fireEvent.compositionEnd(input, { data: '会议结论' });
    expect(input.value).toBe('会议结论');

    fireEvent.change(input, { target: { value: '会议结论：库位空调逻辑需要增加预设逻辑' } });
    expect(input.value).toBe('会议结论：库位空调逻辑需要增加预设逻辑');

    fireEvent.change(input, { target: { value: '会议结论：库位空调逻辑需要增加预设逻辑，\\\\' } });
    expect(input.value).toBe('会议结论：库位空调逻辑需要增加预设逻辑，\\\\');
    expect(input.value.match(/\\/g)?.length).toBe(2);
  });

  it('发送出去的正文与输入框内容逐字一致（后端 JSON 编码不体现在界面文本上）', async () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<DiscussionPanel comments={[]} onSend={onSend} />);
    const input = getInput();

    const typed = '路径 C:\\logs\\a.txt，反斜杠 \\\\'; 
    fireEvent.change(input, { target: { value: typed } });

    await act(async () => {
      // tdesign Button 不透传 aria-label，这里按发送按钮的类名定位
      const sendBtn = document.querySelector('.detail-chat-send') as HTMLButtonElement;
      fireEvent.click(sendBtn);
    });

    expect(onSend).toHaveBeenCalledTimes(1);
    const sentText = onSend.mock.calls[0][0] as string;
    expect(sentText).toBe(typed);
    expect(sentText.match(/\\/g)?.length).toBe(4);
  });

  it('「闪一下」的代码机制：超 500 字时输入框内容被整段替换为 #clipboard(N行) 记号', () => {
    const onSend = vi.fn().mockResolvedValue(true);
    render(<DiscussionPanel comments={[]} onSend={onSend} enableAttach />);
    const input = getInput();

    // 600 字、无换行 → 记号按「行数」命名，故为 1 行
    const long = '库'.repeat(600);
    fireEvent.change(input, { target: { value: long } });

    // 输入框被换成记号（= 用户肉眼看到的「闪一下、内容突变」）
    expect(input.value).toBe('#clipboard(1行)');
    // 原文进了待发送附件，不丢内容（输入框 + 附件芯片各一处同名记号）
    expect(screen.getAllByText('#clipboard(1行)').length).toBe(2);
    // 超限提示可见
    expect(screen.getByText(/超出 500 字限制/)).toBeInTheDocument();
  });
});
