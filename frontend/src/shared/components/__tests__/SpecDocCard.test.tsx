import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SpecDocCard from '../SpecDocCard';
import { getSpecDoc, saveSpecDoc, generateProblemDoc } from '@/api/specDoc';

// 弹层与 Toast 桩掉：只验证逻辑，不渲染真实浮层
vi.mock('tdesign-mobile-react', () => ({
  Toast: vi.fn(),
  Popup: ({ visible, children }: { visible?: boolean; children?: React.ReactNode }) =>
    (visible ? <div>{children}</div> : null),
}));

// markdown 渲染 / 编辑器弹层：桩掉，避免拖进重依赖
vi.mock('@/shared/components/MarkdownRenderer', () => ({ default: () => <div data-testid="md-render" /> }));
vi.mock('@/shared/components/SpecDocEditor', () => ({ default: () => null }));

// 讨论区评论（GET /{task_id}/comments；接口按时间倒序返回，组件内转正序）
vi.mock('@/api/client', () => ({
  createRequest: () =>
    vi.fn().mockResolvedValue([
      { id: 2, content: '补充：仪表盘报故障码 P0701', created_by_name: '李四', created_at: '2026-09-29 11:00:00' },
      { id: 1, content: '第一次评论：车启动不了', created_by_name: '张三', created_at: '2026-09-29 10:00:00' },
    ]),
}));

vi.mock('@/api/specDoc', () => ({
  getSpecDoc: vi.fn(),
  saveSpecDoc: vi.fn(),
  generateProblemDoc: vi.fn(),
}));

const DOC = {
  exists: true,
  task_id: 9527,
  content: [
    '# 问题共享文档',
    '',
    '> 项目：江苏常州多摩川混场项目',
    '',
    '## 项目背景信息',
    '',
    '### 车端软件',
    '',
    'v2.3.1',
    '',
    '---',
    '',
    '## 问题描述',
    '',
    '（旧的人工补充）',
  ].join('\n'),
  content_type: 'markdown',
  source: 'inline',
  source_files: [],
  revision: 3,
  updated_by_name: '张三',
};

describe('问题文档卡片（工单详情，AI 汇总讨论内容）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSpecDoc).mockResolvedValue(DOC);
    vi.mocked(saveSpecDoc).mockResolvedValue({ ...DOC, revision: 4 });
    vi.mocked(generateProblemDoc).mockResolvedValue({
      markdown: '## 问题描述\n\nAI 汇总：车启动不了，报故障码 P0701',
      used: 2,
      dropped: 0,
      truncated: false,
    });
  });

  it('可编辑者看到「AI 汇总讨论内容」与「编辑补充」入口', async () => {
    render(<SpecDocCard taskId={9527} canEdit />);
    expect(await screen.findByRole('button', { name: 'AI 汇总讨论内容' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '编辑补充' })).toBeTruthy();
    expect(screen.getByTestId('md-render')).toBeTruthy();
  });

  it('不可编辑者看不到 AI 汇总入口', async () => {
    render(<SpecDocCard taskId={9527} canEdit={false} />);
    expect(await screen.findByTestId('md-render')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'AI 汇总讨论内容' })).toBeNull();
  });

  it('评论按正序交给接口，确认后只替换补充段并乐观锁写入', async () => {
    render(<SpecDocCard taskId={9527} canEdit />);
    fireEvent.click(await screen.findByRole('button', { name: 'AI 汇总讨论内容' }));

    await waitFor(() => expect(vi.mocked(generateProblemDoc)).toHaveBeenCalledTimes(1));
    const params = vi.mocked(generateProblemDoc).mock.calls[0][0];
    // 接口倒序 → 前端反转成正序
    expect(params.items.map((i) => i.content)).toEqual([
      '第一次评论：车启动不了',
      '补充：仪表盘报故障码 P0701',
    ]);
    expect(params.items[0].author).toBe('张三');
    expect(params.scene).toBe('discussion');

    // 预览弹层（可编辑）→ 确认写入
    fireEvent.click(await screen.findByRole('button', { name: '写入文档' }));

    await waitFor(() => expect(vi.mocked(saveSpecDoc)).toHaveBeenCalledTimes(1));
    const [taskIdArg, payload] = vi.mocked(saveSpecDoc).mock.calls[0];
    expect(taskIdArg).toBe(9527);
    expect(payload.revision).toBe(3);
    expect(payload.source).toBe('ai_summary');
    // 系统段（项目背景信息）原样保留，分隔线以下的补充段整体替换
    expect(payload.content).toContain('### 车端软件');
    expect(payload.content).toContain('v2.3.1');
    expect(payload.content).not.toContain('（旧的人工补充）');
    expect(payload.content).toContain('AI 汇总：车启动不了，报故障码 P0701');
  });

  it('无文档时也能从评论生成：写入后形成只有补充段的新文档', async () => {
    vi.mocked(getSpecDoc).mockResolvedValue({ ...DOC, exists: false, content: '', revision: 0 });
    render(<SpecDocCard taskId={9527} canEdit />);

    fireEvent.click(await screen.findByRole('button', { name: 'AI 汇总讨论内容' }));
    await waitFor(() => expect(vi.mocked(generateProblemDoc)).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole('button', { name: '写入文档' }));

    await waitFor(() => expect(vi.mocked(saveSpecDoc)).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(saveSpecDoc).mock.calls[0][1];
    expect(payload.content).toContain('AI 汇总：车启动不了，报故障码 P0701');
  });
});
