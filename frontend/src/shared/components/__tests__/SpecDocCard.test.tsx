import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
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

/** 打开弹窗：入口按钮常驻在「问题描述」标题行右侧 */
const openSheet = async () => {
  fireEvent.click(await screen.findByRole('button', { name: '详细问题文档' }));
};

describe('详细问题文档入口（工单详情，标题行按钮 + 弹窗，AI 汇总讨论内容）', () => {
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

  it('有文档且可编辑：入口按钮常驻，未点击不渲染弹窗内容，打开后看到正文与两个入口', async () => {
    render(<SpecDocCard taskId={9527} canEdit />);

    expect(await screen.findByRole('button', { name: '详细问题文档' })).toBeTruthy();
    // 懒挂载：未打开弹窗时不渲染内容子树
    expect(screen.queryByTestId('md-render')).toBeNull();

    await openSheet();
    expect(await screen.findByRole('button', { name: 'AI 汇总讨论内容' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '编辑补充' })).toBeTruthy();
    expect(screen.getByTestId('md-render')).toBeTruthy();
  });

  it('有文档但不可编辑：弹窗内只有正文，看不到 AI 汇总与编辑补充', async () => {
    render(<SpecDocCard taskId={9527} canEdit={false} />);

    await openSheet();
    expect(await screen.findByTestId('md-render')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'AI 汇总讨论内容' })).toBeNull();
    expect(screen.queryByRole('button', { name: '编辑补充' })).toBeNull();
  });

  it('无文档但可编辑：入口仍在，弹窗内是「编写」与空态文案', async () => {
    vi.mocked(getSpecDoc).mockResolvedValue({ ...DOC, exists: false, content: '', revision: 0 });
    render(<SpecDocCard taskId={9527} canEdit />);

    await openSheet();
    expect(screen.getByRole('button', { name: '编写' })).toBeTruthy();
    expect(screen.getByText(/尚未编写问题文档/)).toBeTruthy();
  });

  it('无文档且无编辑权限：入口按钮整体不渲染', async () => {
    vi.mocked(getSpecDoc).mockResolvedValue({ ...DOC, exists: false, content: '', revision: 0 });
    render(<SpecDocCard taskId={9527} canEdit={false} />);

    await waitFor(() => expect(vi.mocked(getSpecDoc)).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('button', { name: '详细问题文档' })).toBeNull();
  });

  it('评论按正序交给接口，确认后只替换补充段并乐观锁写入', async () => {
    render(<SpecDocCard taskId={9527} canEdit />);
    await openSheet();
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

    await openSheet();
    fireEvent.click(await screen.findByRole('button', { name: 'AI 汇总讨论内容' }));
    await waitFor(() => expect(vi.mocked(generateProblemDoc)).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole('button', { name: '写入文档' }));

    await waitFor(() => expect(vi.mocked(saveSpecDoc)).toHaveBeenCalledTimes(1));
    const payload = vi.mocked(saveSpecDoc).mock.calls[0][1];
    expect(payload.content).toContain('AI 汇总：车启动不了，报故障码 P0701');
  });

  it('弹窗顶部只读展示系统段带入的一级标签；无系统段（手写文档）不渲染这一行', async () => {
    const first = render(<SpecDocCard taskId={9527} canEdit />);
    await openSheet();

    const tags = await screen.findByTestId('spec-sheet-tags');
    // 系统段里 `### 车端软件`（`## 项目背景信息` 是章节标题，不算标签）
    expect(within(tags).getByText('车端软件')).toBeTruthy();
    expect(within(tags).queryByText('项目背景信息')).toBeNull();
    first.unmount();

    // 提单后没写补充内容 → 文档没有分隔线（整篇都是系统段），标签同样要展示
    vi.mocked(getSpecDoc).mockResolvedValue({
      ...DOC,
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
      ].join('\n'),
    });
    const second = render(<SpecDocCard taskId={9527} canEdit />);
    await openSheet();
    expect(within(await screen.findByTestId('spec-sheet-tags')).getByText('车端软件')).toBeTruthy();
    second.unmount();

    // 无系统段（上传 / 手写文档）→ 整块不渲染
    vi.mocked(getSpecDoc).mockResolvedValue({
      ...DOC,
      content: '## 问题描述\n\n（手写的补充内容）',
    });
    render(<SpecDocCard taskId={9527} canEdit />);
    await openSheet();
    expect(await screen.findByTestId('md-render')).toBeTruthy();
    expect(screen.queryByTestId('spec-sheet-tags')).toBeNull();
  });
});
