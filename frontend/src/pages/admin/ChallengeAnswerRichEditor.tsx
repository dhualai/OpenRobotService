import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { EditorContent, Extension, useEditor } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Image from '@tiptap/extension-image';
import { TableKit } from '@tiptap/extension-table';
import { Markdown } from '@tiptap/markdown';
import { Plugin, TextSelection } from '@tiptap/pm/state';
import ImageLightbox from '@/shared/components/ImageLightbox';
import { ENV_PREFIX } from '@/config/api';
import { toAppUrl } from '@/shared/utils/markdown';

const MAX_LENGTH = 20000;
const UPLOADED_IMAGE = /^\/api\/tasks\/files\/[A-Za-z0-9_-]+\/spec-doc\/images\/[0-9a-f]{32}\.(?:png|jpg|jpeg|gif|webp|bmp)$/;

function imagePath(value: string): string {
  const path = ENV_PREFIX && value.startsWith(`${ENV_PREFIX}/api/`) ? value.slice(ENV_PREFIX.length) : value;
  return UPLOADED_IMAGE.test(path) ? path : '';
}

function imageAlt(value: string): string {
  return value.replace(/[\\[\]\r\n]/g, '_') || '解答图片';
}

const AnswerImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      src: { default: null, parseHTML: (element) => imagePath(element.getAttribute('src') || '') },
      alt: { default: '解答图片', parseHTML: (element) => imageAlt(element.getAttribute('alt') || '') },
    };
  },
  parseHTML() {
    return [{ tag: 'img[src]', getAttrs: (element) => imagePath(element.getAttribute('src') || '') ? {} : false }];
  },
  renderHTML({ node }) {
    const path = imagePath(node.attrs.src || '');
    return path
      ? ['img', { src: toAppUrl(path), alt: imageAlt(node.attrs.alt || ''), draggable: 'true' }]
      : ['span', {}, node.attrs.alt || '图片'];
  },
  parseMarkdown(token, helpers) {
    const path = imagePath(token.href || '');
    return path
      ? helpers.createNode('image', { src: path, alt: imageAlt(token.text || '') })
      : helpers.createTextNode(token.text || '图片');
  },
  renderMarkdown(node) {
    const path = imagePath(node.attrs?.src || '');
    return path ? `![${imageAlt(node.attrs?.alt || '')}](${path})` : node.attrs?.alt || '图片';
  },
});

export interface AnswerSelection { from: number; to: number }

export interface ChallengeAnswerRichEditorHandle {
  selection: () => AnswerSelection;
  insertImage: (selection: AnswerSelection, url: string, name: string) => AnswerSelection;
  focus: () => void;
}

interface Props {
  content: string;
  disabled: boolean;
  onChange: (content: string) => void;
  onUploadImages: (files: File[]) => void;
  onError: (message: string) => void;
}

const ChallengeAnswerRichEditor = forwardRef<ChallengeAnswerRichEditorHandle, Props>(function ChallengeAnswerRichEditor(
  { content, disabled, onChange, onUploadImages, onError }, ref,
) {
  const callbacks = useRef({ onChange, onUploadImages, onError });
  callbacks.current = { onChange, onUploadImages, onError };
  const lastContent = useRef(content);
  const [preview, setPreview] = useState<{ src: string; alt: string }>();
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        underline: false,
        link: { openOnClick: false, isAllowedUri: (value) => {
          try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password; }
          catch { return false; }
        } },
      }),
      AnswerImage,
      TableKit,
      Markdown.configure({ markedOptions: { gfm: true, breaks: true } }),
      Extension.create({
        name: 'answerLengthLimit',
        addProseMirrorPlugins() {
          return [new Plugin({ filterTransaction: (transaction) => {
            if (!transaction.docChanged || !this.editor.markdown) return true;
            if (this.editor.markdown.serialize(transaction.doc.toJSON()).trimEnd().length <= MAX_LENGTH) return true;
            callbacks.current.onError('答案内容不能超过20000字');
            return false;
          } })];
        },
      }),
    ],
    content,
    contentType: 'markdown',
    editable: !disabled,
    enableInputRules: false,
    enablePasteRules: false,
    shouldRerenderOnTransaction: true,
    editorProps: {
      attributes: { role: 'textbox', 'aria-label': '答案内容', 'aria-multiline': 'true', 'aria-required': 'true', class: 'challenge-answer-rich-editor' },
      handleDOMEvents: { paste: (view, event) => {
        if (!view.editable) return true;
        const files = Array.from(event.clipboardData?.files || []).filter((file) => file.type.startsWith('image/'));
        if (!files.length) return false;
        event.preventDefault();
        callbacks.current.onUploadImages(files);
        return true;
      }, drop: (view, event) => {
        const files = Array.from(event.dataTransfer?.files || []).filter((file) => file.type.startsWith('image/'));
        if (!files.length) return false;
        event.preventDefault();
        if (!view.editable) return true;
        const position = view.posAtCoords({ left: event.clientX, top: event.clientY });
        if (position) view.dispatch(view.state.tr.setSelection(TextSelection.near(view.state.doc.resolve(position.pos))));
        callbacks.current.onUploadImages(files);
        return true;
      } },
    },
    onUpdate: ({ editor: current }) => {
      const value = current.getMarkdown().trimEnd();
      lastContent.current = value;
      callbacks.current.onChange(value);
    },
  });

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!disabled, false);
    editor.view.dom.setAttribute('aria-disabled', String(disabled));
  }, [editor, disabled]);

  useEffect(() => {
    if (!editor || content === lastContent.current) return;
    editor.commands.setContent(content, { contentType: 'markdown', emitUpdate: false });
    lastContent.current = content;
  }, [editor, content]);

  useImperativeHandle(ref, () => ({
    selection: () => ({ from: editor?.state.selection.from ?? 1, to: editor?.state.selection.to ?? 1 }),
    insertImage: (selection, url, name) => {
      if (!editor || editor.isDestroyed) throw new Error('编辑器尚未就绪，请重试');
      const path = imagePath(url);
      if (!path) throw new Error('请使用上传成功的图片');
      const image = editor.schema.nodes.image.create({ src: path, alt: imageAlt(name) });
      const transaction = editor.state.tr
        .replaceRangeWith(selection.from, selection.to, image);
      let imageEnd = -1;
      transaction.doc.descendants((node, position) => {
        if (node === image) imageEnd = position + node.nodeSize;
      });
      if (imageEnd < 0) throw new Error('此位置无法插入图片，请将光标放在正文中重试');
      if (!transaction.doc.resolve(imageEnd).nodeAfter?.isTextblock) {
        transaction.insert(imageEnd, editor.schema.nodes.paragraph.create());
      }
      transaction.setSelection(TextSelection.create(transaction.doc, imageEnd + 1));
      if (!editor.markdown || editor.markdown.serialize(transaction.doc.toJSON()).trimEnd().length > MAX_LENGTH) {
        throw new Error('插入图片后答案内容不能超过20000字，请缩短内容后重试');
      }
      editor.view.dispatch(transaction);
      return { from: editor.state.selection.from, to: editor.state.selection.to };
    },
    focus: () => { if (editor && !editor.isDestroyed) editor.view.focus(); },
  }), [editor]);

  return <div className="challenge-answer-document">
    <span className="challenge-answer-document__label">答案内容</span>
    <div role="toolbar" aria-label="答案格式" className="challenge-answer-format">
      <button type="button" disabled={disabled || !editor} aria-pressed={editor?.isActive('bold') ?? false} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleBold().run()}>加粗</button>
      <button type="button" disabled={disabled || !editor} aria-pressed={editor?.isActive('italic') ?? false} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleItalic().run()}>斜体</button>
      <button type="button" disabled={disabled || !editor} aria-pressed={editor?.isActive('bulletList') ?? false} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleBulletList().run()}>项目列表</button>
      <button type="button" disabled={disabled || !editor} aria-pressed={editor?.isActive('orderedList') ?? false} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().toggleOrderedList().run()}>编号列表</button>
      <button type="button" disabled={disabled || !editor?.can().undo()} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().undo().run()}>撤销</button>
      <button type="button" disabled={disabled || !editor?.can().redo()} onMouseDown={(event) => event.preventDefault()} onClick={() => editor?.chain().focus().redo().run()}>重做</button>
    </div>
    <EditorContent editor={editor} onDoubleClick={(event) => {
      const image = event.target;
      if (image instanceof HTMLImageElement && imagePath(image.getAttribute('src') || '')) {
        setPreview({ src: image.src, alt: image.alt });
      }
    }} />
    <ImageLightbox src={preview?.src} alt={preview?.alt} open={!!preview} onClose={() => setPreview(undefined)} />
  </div>;
});

export default ChallengeAnswerRichEditor;
