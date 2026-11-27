/**
 * ArchiveReportRichEditor variant 测试（ORS-1214）
 *
 * 编辑器本体（Tiptap/ProseMirror）桩掉，只验证工具栏形态与 ref 插入接口：
 *   - variant='full'（默认）：完整工具栏 → 归档报告用法不受影响
 *   - variant='simple'：无工具栏纯输入区（仅粘贴/拖拽插入，工单解决方式弹窗用）
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { createRef } from 'react';

vi.mock('@tiptap/react', () => ({
  EditorContent: () => <div data-testid="editor-content" />,
  useEditor: () => null,
}));
vi.mock('@tiptap/core', () => ({
  Extension: { create: () => ({}) },
  getRenderedAttributes: () => ({}),
  mergeAttributes: (...a: Record<string, unknown>[]) => Object.assign({}, ...a),
  ResizableNodeView: class {},
}));
vi.mock('@tiptap/starter-kit', () => ({ default: { configure: () => ({}) } }));
vi.mock('@tiptap/markdown', () => ({ Markdown: { configure: () => ({}) } }));
vi.mock('@tiptap/pm/state', () => ({ Plugin: class {}, TextSelection: {} }));
vi.mock('@tiptap/extension-image', () => ({
  default: {
    extend: () => ({ configure: () => ({}), parseHTML: () => [], renderHTML: () => ({}) }),
  },
}));
vi.mock('@/shared/components/ImageLightbox', () => ({ default: () => null }));
vi.mock('@/shared/utils/authImageFetch', () => ({
  loadAuthImage: vi.fn(),
  getAuthImgCache: () => ({}),
}));

import ArchiveReportRichEditor from '../ArchiveReportRichEditor';

describe('ArchiveReportRichEditor variant', () => {
  beforeEach(() => {
    cleanup();
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('默认 full：完整工具栏（H2/H3/斜体/编号）', () => {
    render(<ArchiveReportRichEditor value="" />);
    expect(screen.getByRole('toolbar', { name: '报告格式' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'H2 二级标题' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'H3 三级标题' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '斜体' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '编号' })).toBeTruthy();
  });

  it('simple：无工具栏（无任何格式按钮，仅粘贴/拖拽插入），但有全屏按钮', () => {
    render(<ArchiveReportRichEditor value="" variant="simple" onUploadImages={vi.fn()} />);
    expect(screen.queryByRole('toolbar')).toBeNull();
    expect(screen.queryByRole('button', { name: '加粗' })).toBeNull();
    expect(screen.getByTestId('editor-content')).toBeTruthy();
    expect(screen.getByRole('button', { name: '全屏' })).toBeTruthy();
  });

  it('simple 全屏切换：点击进入（wrapper 挂 is-fullscreen + 按钮变退出全屏），再点退出', () => {
    const { container } = render(<ArchiveReportRichEditor value="" variant="simple" />);
    const wrapper = container.querySelector('.archive-report-document') as HTMLElement;
    expect(wrapper.classList.contains('is-fullscreen')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '全屏' }));
    expect(wrapper.classList.contains('is-fullscreen')).toBe(true);
    expect(screen.getByRole('button', { name: '退出全屏' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '退出全屏' }));
    expect(wrapper.classList.contains('is-fullscreen')).toBe(false);
    expect(screen.getByRole('button', { name: '全屏' })).toBeTruthy();
  });

  it('full 模式不提供全屏按钮（归档报告页不受影响）', () => {
    render(<ArchiveReportRichEditor value="" />);
    expect(screen.queryByRole('button', { name: '全屏' })).toBeNull();
  });

  it('ref.insertImages：编辑器未初始化（桩场景）时安全不抛错', () => {
    const ref = createRef<{ insertImages: (items: { path: string; name: string }[]) => void }>();
    render(<ArchiveReportRichEditor ref={ref as never} value="" variant="simple" />);
    expect(() => ref.current?.insertImages([{ path: '/api/tasks/attachments/download?path=x', name: 'a.png' }])).not.toThrow();
  });

  it('ref.insertFileLinks：编辑器未初始化（桩场景）时安全不抛错', () => {
    const ref = createRef<{ insertFileLinks: (items: { path: string; name: string }[]) => void }>();
    render(<ArchiveReportRichEditor ref={ref as never} value="" variant="simple" />);
    expect(() => ref.current?.insertFileLinks([{ path: 'helpdesk/temp/x.log', name: 'runtime.log' }])).not.toThrow();
  });
});
