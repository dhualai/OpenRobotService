/**
 * ArchiveReportRichEditor —— 知识沉淀报告富文本编辑器
 *
 * 基于 Tiptap（@tiptap/react v3）实现 WYSIWYG 所见即所得编辑，底层持久化格式
 * 为 Markdown（通过 @tiptap/markdown extension），与 AI 生成、后端接口
 * 的 Markdown 文本格式完全对齐，读写链路无转换损耗。
 *
 * 参考实现：ChallengeAnswerRichEditor.tsx（admin 知识竞赛答案编辑器）。
 * 图片路径适配知识沉淀：/api/tasks/attachments/download?path=<object_path>
 *
 * 额外能力：
 *   - 支持 Markdown alt 里的尺寸语法 `![alt|50%](url)` / `![alt|300px](url)`
 *   - 粘贴/拖拽图片 → onUploadImages 回调 → 外部上传后 ref.insertImages 回插
 *   - readOnly 模式：审核人只读查看，替代 MarkdownRenderer 预览态
 */
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { EditorContent, useEditor } from '@tiptap/react';
import type { JSONContent, MarkdownParseResult } from '@tiptap/core';
import { Extension, getRenderedAttributes, mergeAttributes, ResizableNodeView } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import { Markdown } from '@tiptap/markdown';
import { Plugin, TextSelection } from '@tiptap/pm/state';
import type { MarkdownToken, MarkdownParseHelpers, MarkdownRendererHelpers, RenderContext } from '@tiptap/core';
import ImageLightbox from '@/shared/components/ImageLightbox';
import { toAppUrl } from '@/shared/utils/markdown';
import { loadAuthImage, getAuthImgCache } from '@/shared/utils/authImageFetch';

// ---------------------------------------------------------------------------
// 知识沉淀图片路径识别：
//   Markdown 里存的是完整 URL：/api/tasks/attachments/download?path=<object_path>
//   我们内部只存 object_path（MinIO key），渲染时再拼回完整 URL。
// ---------------------------------------------------------------------------
const REPORT_IMAGE_PATH = /^\/api\/tasks\/attachments\/download(\?path=(.+))?$/;
// 也接受带 ENV_PREFIX 前缀的完整路径
const REPORT_IMAGE_PATH_FULL = /\/api\/tasks\/attachments\/download\?path=(.+)$/;

/** 从 Markdown href / HTML src 里提取 MinIO object_path */
function extractObjectPath(raw: string): string {
  if (!raw) return '';
  // 完整 URL 带前缀
  const full = raw.match(REPORT_IMAGE_PATH_FULL);
  if (full) {
    try { return decodeURIComponent(full[1]); } catch { return full[1]; }
  }
  return raw; // 已经是 object_path 原样返回
}

/** 把 object_path 拼成渲染用的完整 URL（带 ENV_PREFIX：dev '' / prod '/p'） */
function buildReportImageUrl(path: string): string {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  // 相对路径：确保以 /api/tasks/attachments/download?path= 开头
  const apiPath = path.startsWith('/api/')
    ? path
    : `/api/tasks/attachments/download?path=${encodeURIComponent(path)}`;
  return toAppUrl(apiPath); // 补 ENV_PREFIX 前缀（生产环境 → /p/api/...）
}

/** 净化 alt 文本：保留尺寸语法（|50% / |300px），其余特殊字符替换 */
function imageAltClean(value: string): string {
  if (!value) return '';
  // 保留末尾的 |尺寸 语法；替换其余 markdown 特殊字符
  return value.replace(/[\[\]\r\n]/g, '_');
}

// ---------------------------------------------------------------------------
// parseAltSize —— 与 MarkdownRenderer.tsx 里同名函数保持一致
// 从 alt 里提取可选的尺寸语法：![alt|50%](url) / ![alt|300px](url)
// 返回 width：number（px，Tiptap ResizableNodeView 期望纯数值）或 string（%）
// ---------------------------------------------------------------------------
function parseAltSizeForEditor(alt: string): { alt: string; width?: number | string } {
  if (!alt) return { alt: '' };
  const m = alt.match(/^(.*?)\|\s*(\d+(?:px|%)?)\s*$/);
  if (!m) return { alt };
  const raw = m[2];
  if (raw.endsWith('%')) return { alt: m[1], width: raw };
  const num = parseInt(raw, 10);
  return { alt: m[1], width: isNaN(num) ? raw : num };
}

// ---------------------------------------------------------------------------
// ReportImage —— 自定义图片 node
//   attrs: src=object_path, alt, width (可选，来自 |尺寸 语法)
//
// Tiptap v3 参考：ChallengeAnswerRichEditor.tsx AnswerImage
// DOM 解析逻辑放在 addAttributes() 的 parseHTML 回调里，不用已废弃的 parseAttributes
// ---------------------------------------------------------------------------
const ReportImage = Image.extend({
  // 开启内置 resize 能力（拖角落手柄调整图片大小）
  addOptions() {
    return {
      ...this.parent?.(),
      inline: false,
      allowBase64: false,
      HTMLAttributes: {},
      resize: {
        enabled: true,
        directions: ['top-left', 'top-right', 'bottom-left', 'bottom-right', 'right', 'left'],
        minWidth: 60,
        alwaysPreserveAspectRatio: true,
      },
    };
  },

  // Override 父类 Image 的 addNodeView：
  // 只保留 ResizableNodeView 的 wrap + resize handles 能力，
  // 去掉 syncImageSource 对 src 的管理（src 完全由我们的 fetch + Bearer token 流程填充）。
  // 父类 L100 的 syncImageSource(newHTMLAttributes.src) 会每次 update 清掉 blob URL！
  addNodeView() {
    if (!this.options.resize || !this.options.resize.enabled || typeof document === 'undefined') return null;
    const { directions, minWidth, minHeight, alwaysPreserveAspectRatio } = this.options.resize;
    const resizeManagedAttributes = new Set(['src', 'width', 'height']);

    return ({ node, getPos, HTMLAttributes, editor }) => {
      const el = document.createElement('img');
      el.draggable = false;

      // 把 data-auth-src / alt / draggable 等设到 img 上（跳过 src/width/height）
      const mergedAttributes = mergeAttributes(this.options.HTMLAttributes, HTMLAttributes);
      Object.entries(mergedAttributes).forEach(([key, value]) => {
        if (value != null) {
          switch (key) {
            case 'src':
            case 'width':
            case 'height':
              break; // ResizableNodeView 自己管理这些
            default:
              el.setAttribute(key, value);
          }
        }
      });

      // ⚠️ 关键：不设 src！不调 syncImageSource！
      // 让外层 fetch + Bearer token 去填真正的 blob URL。
      // 父类 L77/L87/L100 会反复覆盖 src，这里全部跳过。

      let previousHTMLAttributes = { ...HTMLAttributes };

      const onUpdate = (updatedNode: any) => {
        if (updatedNode.type !== node.type) return false;
        const extensionAttributes = editor.extensionManager.attributes.filter(
          (a: any) => a.type === updatedNode.type.name,
        );
        const newHTMLAttributes = getRenderedAttributes(updatedNode, extensionAttributes);
        // 移除旧 attr
        Object.keys(previousHTMLAttributes).forEach((key) => {
          if (!resizeManagedAttributes.has(key) && !(key in newHTMLAttributes)) {
            el.removeAttribute(key);
          }
        });
        // 设置新 attr
        Object.entries(newHTMLAttributes).forEach(([key, value]) => {
          if (resizeManagedAttributes.has(key)) return;
          if (value != null) el.setAttribute(key, value);
          else el.removeAttribute(key);
        });
        // ⚠️ 不调 syncImageSource(newHTMLAttributes.src) —— 会覆盖我们的 blob URL！
        previousHTMLAttributes = newHTMLAttributes;
        return true;
      };

      const nodeView = new ResizableNodeView({
        element: el,
        editor,
        node,
        getPos,
        onResize: (width, height) => {
          el.style.width = `${width}px`;
          el.style.height = `${height}px`;
        },
        onCommit: (width, height) => {
          const pos = getPos();
          if (pos === undefined) return;
          this.editor
            .chain()
            .setNodeSelection(pos)
            .updateAttributes(this.name, { width, height })
            .run();
        },
        onUpdate,
        options: {
          directions,
          min: { width: minWidth, height: minHeight },
          preserveAspectRatio: alwaysPreserveAspectRatio === true,
        },
      });

      const dom = nodeView.dom;
      const showNodeView = () => {
        dom.style.visibility = '';
        dom.style.pointerEvents = '';
      };
      dom.style.visibility = 'hidden';
      dom.style.pointerEvents = 'none';
      if (el.complete && el.naturalWidth > 0) showNodeView();
      else {
        el.onload = showNodeView;
        el.onerror = showNodeView;
      }
      return { dom, contentDOM: null };
    };
  },

  addAttributes() {
    return {
      ...this.parent?.(),
      src: {
        default: null,
        parseHTML: (element: HTMLElement) => {
          const src = element.getAttribute('src') || '';
          // 跳过 base64 占位 src（我们自己塞的），回退从 data-auth-src 读
          if (src.startsWith('data:')) {
            const authSrc = element.getAttribute('data-auth-src') || element.getAttribute('data-auth-src-alt') || '';
            if (authSrc) {
              const full = authSrc.match(REPORT_IMAGE_PATH_FULL);
              if (full) { try { return decodeURIComponent(full[1]); } catch { return full[1]; } }
              return extractObjectPath(authSrc) || authSrc;
            }
            return null;
          }
          const full = src.match(REPORT_IMAGE_PATH_FULL);
          const objectPath = full
            ? (() => { try { return decodeURIComponent(full[1]); } catch { return full[1]; } })()
            : extractObjectPath(src);
          return objectPath || src || null;
        },
      },
      alt: {
        default: '图片',
        parseHTML: (element: HTMLElement) => {
          const raw = element.getAttribute('alt') || '图片';
          return parseAltSizeForEditor(raw).alt;
        },
      },
      width: {
        default: null as number | string | null,
        parseHTML: (element: HTMLElement) => {
          const wAttr = element.getAttribute('width');
          if (wAttr) {
            const num = parseInt(wAttr, 10);
            if (!isNaN(num)) return num;
            return wAttr;
          }
          const raw = element.getAttribute('alt') || '';
          return parseAltSizeForEditor(raw).width || null;
        },
      },
      // 自定义 attr：renderHTML 从 src 自动算出 data-auth-src，
      // 让 ResizableNodeView.onUpdate 每次都重建，不会被删掉
      dataAuthSrc: {
        default: null,
        rendered: true, // 必须 true 才会出现在 getRenderedAttributes 里
        renderHTML: (attrs: Record<string, any>) => {
          const src = attrs?.src;
          if (!src) return null;
          return { 'data-auth-src': buildReportImageUrl(String(src)) };
        },
      },
    };
  },

  parseHTML() {
    return [{
      tag: 'img[src]',
      getAttrs: (element: HTMLElement) => {
        const src = element.getAttribute('src') || '';
        // 跳过 base64 占位图，看 data-auth-src 或原 src
        const effectiveSrc = src.startsWith('data:')
          ? (element.getAttribute('data-auth-src') || '')
          : src;
        const path = effectiveSrc
          ? (() => {
              const full = effectiveSrc.match(REPORT_IMAGE_PATH_FULL);
              return full
                ? (() => { try { return decodeURIComponent(full[1]); } catch { return full[1]; } })()
                : extractObjectPath(effectiveSrc);
            })()
          : '';
        return path ? {} : false;
      },
    }];
  },

  renderHTML({ node }) {
    const path = node.attrs.src || '';
    const url = buildReportImageUrl(path);
    if (!url) return ['span', {}, node.attrs.alt || '图片'];
    // renderHTML 是 addNodeView 返回 null 时的 fallback（非浏览器环境/resize 未开启）。
    // 主流程走 addNodeView 自定义版本，src 完全由 fetch + Bearer token 填充。
    const attrs: Record<string, string> = {
      alt: imageAltClean(node.attrs.alt || '图片'),
      draggable: 'true',
      'data-auth-src': url,
    };
    const w = node.attrs.width as number | string | undefined;
    if (w != null) attrs.width = String(w);
    return ['img', attrs];
  },

  parseMarkdown(token: MarkdownToken, helpers: MarkdownParseHelpers): MarkdownParseResult {
    const href = token.href || '';
    const path = extractObjectPath(href) || href;
    if (!path) return { type: 'text', text: token.text || '图片' };
    const altRaw = imageAltClean(token.text || '图片');
    const { alt, width } = parseAltSizeForEditor(altRaw);
    return helpers.createNode('image', {
      src: path,
      alt: alt || '图片',
      width: width || null,
    });
  },

  renderMarkdown(node: JSONContent, _helpers: MarkdownRendererHelpers, _ctx: RenderContext): string {
    const attrs = node.attrs ?? {};
    const path = (attrs.src as string) || '';
    if (!path) return (attrs.alt as string) || '图片';
    const baseAlt = imageAltClean((attrs.alt as string) || '图片');
    // width 可能是 number（resize 写回的 px）→ 转 "300px"；也可能是 "50%"（字符串）→ 原样
    const rawW = attrs.width as number | string | undefined;
    const w = rawW != null
      ? (typeof rawW === 'number' ? `${rawW}px` : String(rawW))
      : undefined;
    const alt = w ? `${baseAlt}|${w}` : baseAlt;
    return `![${alt}](${buildReportImageUrl(path)})`;
  },
});

// ---------------------------------------------------------------------------
// 对外接口
// ---------------------------------------------------------------------------
export interface ArchiveImageRefItem { path: string; name: string }

export interface ArchiveReportRichEditorHandle {
  focus: () => void;
  /** 外部上传成功后，把图片插入当前光标位置 */
  insertImages: (items: ArchiveImageRefItem[]) => void;
}

interface Props {
  /** Markdown 原始内容（与 AI/后端接口格式一致） */
  value: string;
  /** 编辑器内容变更，回传 Markdown 字符串（readOnly 模式可不传） */
  onChange?: (value: string) => void;
  /** 只读模式：审核人查看已提交报告 */
  readOnly?: boolean;
  /** 粘贴/拖拽图片时触发，外部负责上传后调 insertImages */
  onUploadImages?: (files: File[]) => void;
}

const ArchiveReportRichEditor = forwardRef<ArchiveReportRichEditorHandle, Props>(
  function ArchiveReportRichEditor({ value, onChange, readOnly = false, onUploadImages }, ref) {
    const callbacks = useRef({ onChange, onUploadImages });
    callbacks.current = { onChange, onUploadImages };
    const lastMd = useRef(value);
    const [preview, setPreview] = useState<{ src: string; alt: string }>();

    const editor = useEditor({
      extensions: [
        StarterKit.configure({
          heading: { levels: [2, 3] },
          underline: false,
          link: false, // 知识沉淀报告通常不需要超链接按钮
        }),
        ReportImage,
        Markdown.configure({ markedOptions: { gfm: true, breaks: true } }),
        Extension.create({
          name: 'archiveImagePaste',
          addProseMirrorPlugins() {
            if (!callbacks.current.onUploadImages) return [];
            return []; // paste/drop 走 editorProps.handleDOMEvents
          },
        }),
      ],
      content: value,
      contentType: 'markdown',
      editable: !readOnly,
      enableInputRules: false,
      enablePasteRules: false,
      shouldRerenderOnTransaction: true,
      editorProps: {
        attributes: {
          role: 'textbox',
          'aria-label': '知识沉淀报告',
          'aria-multiline': 'true',
          class: 'archive-report-rich-editor',
        },
        handleDOMEvents: {
          paste: (view, event) => {
            if (!view.editable || !callbacks.current.onUploadImages) return false;
            const files = Array.from(event.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'));
            if (!files.length) return false;
            event.preventDefault();
            callbacks.current.onUploadImages(files);
            return true;
          },
          drop: (view, event) => {
            if (!view.editable || !callbacks.current.onUploadImages) return false;
            const files = Array.from(event.dataTransfer?.files || []).filter((f) => f.type.startsWith('image/'));
            if (!files.length) return false;
            event.preventDefault();
            const pos = view.posAtCoords({ left: event.clientX, top: event.clientY });
            if (pos) view.dispatch(view.state.tr.setSelection(TextSelection.near(view.state.doc.resolve(pos.pos))));
            callbacks.current.onUploadImages(files);
            return true;
          },
        },
      },
      onUpdate: ({ editor: cur }) => {
        const md = cur.getMarkdown();
        lastMd.current = md;
        callbacks.current.onChange?.(md);
      },
    });

    // readOnly 切换
    useEffect(() => {
      if (!editor) return;
      editor.setEditable(!readOnly, false);
      editor.view.dom.setAttribute('aria-disabled', String(readOnly));
    }, [editor, readOnly]);

    // 外部 value 变更（如 AI 生成完写回）→ 同步到编辑器
    useEffect(() => {
      if (!editor || value === lastMd.current) return;
      editor.commands.setContent(value, { contentType: 'markdown', emitUpdate: false });
      lastMd.current = value;
    }, [editor, value]);

    // ── 鉴权图片加载 ──────────────────────────────────────────────────────
    // renderHTML 返回 src:null + data-auth-src → ResizableNodeView 不会设 src，
    // 这里统一 fetch + Bearer token 拿 blob URL 回填。
    // 驱动方式：editor.on('update') 实时触发 + setInterval 2s 兜底（覆盖 init/nodeView mount 延迟）。
    useEffect(() => {
      if (!editor) return;
      const root = editor.view.dom;
      let cancelled = false;
      const inflight = new WeakSet<HTMLImageElement>();

      const processOne = async (img: HTMLImageElement) => {
        if (img.dataset.authSrc === undefined) return;
        if (img.getAttribute('data-auth-loaded') === '1') return;
        if (inflight.has(img)) return;
        inflight.add(img);

        const url = img.dataset.authSrc;
        // 命中缓存立即回填
        const cached = getAuthImgCache(url);
        if (cached?.status === 'loaded') {
          img.src = cached.blobUrl;
          img.setAttribute('data-auth-loaded', '1');
          delete img.dataset.authSrc;
          inflight.delete(img);
          return;
        }
        if (cached?.status === 'direct' || cached?.status === 'error') {
          img.src = url;
          img.setAttribute('data-auth-loaded', '1');
          delete img.dataset.authSrc;
          inflight.delete(img);
          return;
        }
        // 未命中 → fetch
        const entry = await loadAuthImage(url);
        if (cancelled || !img.isConnected) { inflight.delete(img); return; }
        if (entry.status === 'loaded') {
          img.src = entry.blobUrl;
        } else {
          img.src = url;
        }
        img.setAttribute('data-auth-loaded', '1');
        delete img.dataset.authSrc;
        inflight.delete(img);
      };

      const processAll = () => {
        root.querySelectorAll<HTMLImageElement>('img[data-auth-src]').forEach(processOne);
      };

      // 实时：editor 每次更新（内容/selection/任何事务）都触发
      const onTx = () => processAll();
      editor.on('update', onTx);
      editor.on('selectionUpdate', onTx);

      // 兜底：init 立即跑 + 2s 间隔轮询（覆盖 ResizableNodeView 异步 mount）
      processAll();
      const iv = setInterval(() => {
        processAll();
        // 所有 auth 图片都加载完就停止轮询
        if (!root.querySelector('img[data-auth-src]')) clearInterval(iv);
      }, 2000);

      return () => {
        cancelled = true;
        clearInterval(iv);
        editor.off('update', onTx);
        editor.off('selectionUpdate', onTx);
      };
    }, [editor]);

    useImperativeHandle(ref, () => ({
      focus: () => { if (editor && !editor.isDestroyed) editor.view.focus(); },
      insertImages: (items) => {
        if (!editor || editor.isDestroyed) return;
        items.forEach(({ path, name }) => {
          const node = editor.schema.nodes.image.create({
            src: path,
            alt: imageAltClean(name || '图片'),
          });
          editor.commands.insertContent(node);
        });
      },
    }), [editor]);

    const openImageLightbox = (e: React.MouseEvent) => {
      if (readOnly) return; // 编辑态下双击放大预览不太需要，避免干扰
      const img = e.target;
      if (img instanceof HTMLImageElement) {
        setPreview({ src: img.src, alt: img.alt });
      }
    };

    return (
      <div className="archive-report-document">
        {!readOnly && (
          <div role="toolbar" aria-label="报告格式" className="archive-report-format">
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleHeading({ level: 2 }).run()}>H2 二级标题</button>
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleHeading({ level: 3 }).run()}>H3 三级标题</button>
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleBold().run()}>加粗</button>
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleItalic().run()}>斜体</button>
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleBulletList().run()}>列表</button>
            <button type="button" disabled={!editor} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().toggleOrderedList().run()}>编号</button>
            <button type="button" disabled={!editor?.can().undo()} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().undo().run()}>撤销</button>
            <button type="button" disabled={!editor?.can().redo()} onMouseDown={(e) => e.preventDefault()} onClick={() => editor?.chain().focus().redo().run()}>重做</button>
          </div>
        )}
        <EditorContent editor={editor} onDoubleClick={openImageLightbox} />
        <ImageLightbox src={preview?.src} alt={preview?.alt} open={!!preview} onClose={() => setPreview(undefined)} />
      </div>
    );
  },
);

export default ArchiveReportRichEditor;
