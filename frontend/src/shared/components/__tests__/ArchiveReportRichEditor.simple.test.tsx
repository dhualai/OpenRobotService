/**
 * ArchiveReportRichEditor variant 测试（ORS-1214）
 *
 * 编辑器本体（Tiptap/ProseMirror）桩掉，只验证工具栏形态与图片上传回调：
 *   - variant='full'（默认）：完整工具栏，无图片按钮 → 归档报告用法不受影响
 *   - variant='simple'：精简工具栏（加粗/列表/撤销/重做/图片），供解决方式弹窗使用
 *   - simple 的图片 input change → onUploadImages 收到文件
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
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

  it('默认 full：完整工具栏（H2/H3/斜体/编号），无图片按钮', () => {
    render(<ArchiveReportRichEditor value="" />);
    expect(screen.getByRole('button', { name: 'H2 二级标题' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'H3 三级标题' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '斜体' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '编号' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: '📷 图片' })).toBeNull();
  });

  it('simple：精简工具栏（加粗/列表/撤销/重做/图片），无 H2，toolbar 语义为解决方式格式', () => {
    render(
      <ArchiveReportRichEditor
        value=""
        variant="simple"
        onUploadImages={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '加粗' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '列表' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '撤销' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '重做' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '📷 图片' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'H2 二级标题' })).toBeNull();
    expect(screen.getByRole('toolbar', { name: '解决方式格式' })).toBeTruthy();
  });

  it('simple 未传 onUploadImages：不渲染图片按钮与 file input', () => {
    render(<ArchiveReportRichEditor value="" variant="simple" />);
    expect(screen.queryByRole('button', { name: '📷 图片' })).toBeNull();
    expect(document.querySelector('input[type="file"]')).toBeNull();
  });

  it('simple 图片 input change → onUploadImages 收到文件并清空 value（可重复选同一文件）', () => {
    const onUploadImages = vi.fn();
    render(
      <ArchiveReportRichEditor
        value=""
        variant="simple"
        onUploadImages={onUploadImages}
      />,
    );
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input?.accept).toBe('image/*');
    const png = new File(['x'], 'a.png', { type: 'image/png' });
    Object.defineProperty(input, 'files', { value: [png], configurable: true });
    fireEvent.change(input);
    expect(onUploadImages).toHaveBeenCalledTimes(1);
    expect(onUploadImages).toHaveBeenCalledWith([png]);
    expect(input.value).toBe('');
  });

  it('ref.insertImages：编辑器未初始化（桩场景）时安全不抛错', () => {
    const ref = createRef<{ insertImages: (items: { path: string; name: string }[]) => void }>();
    render(
      <ArchiveReportRichEditor
        ref={ref as never}
        value=""
        variant="simple"
      />,
    );
    expect(() => ref.current?.insertImages([{ path: '/api/tasks/attachments/download?path=x', name: 'a.png' }])).not.toThrow();
  });
});
