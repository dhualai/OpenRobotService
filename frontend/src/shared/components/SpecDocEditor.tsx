/**
 * 问题文档编辑器（md 在线编辑）。
 *
 * - 全屏 Popup + `@uiw/react-md-editor`（成熟库）
 * - 微信窄屏：编辑 / 预览 Tab 切换（不分栏），规避横向滚动与失焦
 * - 受控：父级传 initialValue，点保存回调 onSave(content)
 * - 插入图片：粘贴 / 拖拽 / 工具栏按钮 → 上传 MinIO → markdown 只存 URL（不落 base64）
 * - 导入文档：.md/.txt/.doc/.docx → 解析为 markdown 追加进正文
 * - 粘贴富文本：剪贴板 HTML → markdown（shared/utils/htmlToMarkdown）
 */
import { useEffect, useRef, useState } from 'react';
import { Loading, Popup, Toast } from 'tdesign-mobile-react';
import MDEditor, { type RefMDEditor } from '@uiw/react-md-editor';
import '@uiw/react-md-editor/markdown-editor.css';
import '@uiw/react-markdown-preview/markdown.css';
import {
  externalizeInlineImages,
  foldHugeInlineImages,
  parseSpecDocFile,
  uploadSpecDocImage,
} from '@/api/specDoc';
import { htmlToMarkdown } from '@/shared/utils/htmlToMarkdown';
import { appUrlTransform } from '@/shared/utils/markdown';
import { isImageFile, SPEC_DOC_ACCEPT, SPEC_DOC_FILE_MAX_MB } from '@/shared/utils/fileKind';
import '@/shared/styles/specDoc.css';

interface SpecDocEditorProps {
  visible: boolean;
  title?: string;
  initialValue: string;
  saving?: boolean;
  onClose: () => void;
  /** 返回 Promise 时，期间按钮进入 saving 态由父级控制 */
  onSave: (content: string) => void | Promise<void>;
}

export default function SpecDocEditor({
  visible,
  title = '问题文档',
  initialValue,
  saving = false,
  onClose,
  onSave,
}: SpecDocEditorProps) {
  const [content, setContent] = useState(initialValue);
  const [mode, setMode] = useState<'edit' | 'preview'>('edit');
  const [uploading, setUploading] = useState(false);
  // 打开编辑器前的「大图外置」准备态：准备完成前不挂载 MDEditor
  const [preparing, setPreparing] = useState(false);
  const editorRef = useRef<RefMDEditor | null>(null);
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const docInputRef = useRef<HTMLInputElement | null>(null);

  // 每次打开：重置编辑态；正文含大体积内嵌 base64 图时，先把它们外置为在线图片再挂载编辑器。
  // 关键：MDEditor（@uiw/react-md-editor）mount 即对内容做 Prism/refractor 语法高亮，
  // 其 setext 标题正则在「超长单行 base64 + == 结尾」形态下是 O(n²) 灾难性回溯
  // （工单 836 实测阻塞主线程 81s），因此必须先完成外置/折叠才能渲染编辑器。
  useEffect(() => {
    if (!visible) {
      setPreparing(false);
      return;
    }
    let cancelled = false;
    setMode('edit');
    setUploading(false);

    if (!initialValue.includes('data:image')) {
      setContent(initialValue);
      setPreparing(false);
      return;
    }

    setPreparing(true);
    externalizeInlineImages(initialValue)
      .then((r) => {
        if (cancelled) return;
        let next = r.content;
        if (r.failed > 0) {
          // 外置失败的大图折叠兜底：宁可让用户重新插入，也不能让编辑器卡死
          const folded = foldHugeInlineImages(next);
          next = folded.content;
          Toast({ message: `有 ${r.failed} 张内嵌图片转为在线图片失败，已折叠以免页面卡顿，请重新插入`, theme: 'warning' });
        } else if (r.replaced > 0) {
          Toast({ message: `已自动将 ${r.replaced} 张内嵌图片转为在线图片，保存后生效`, theme: 'success' });
        }
        setContent(next);
      })
      .catch(() => {
        if (cancelled) return;
        const folded = foldHugeInlineImages(initialValue);
        setContent(folded.content);
        if (folded.folded > 0) {
          Toast({ message: '内嵌图片处理失败，已折叠超大图片以免页面卡顿', theme: 'warning' });
        }
      })
      .finally(() => {
        if (!cancelled) setPreparing(false);
      });

    return () => {
      cancelled = true;
    };
  }, [visible, initialValue]);

  /** 在光标位置插入文本（textarea 不存在时追加到尾部） */
  const insertAtCursor = (text: string) => {
    const ta = editorRef.current?.textarea;
    if (!ta) {
      setContent((c) => (c ? `${c}\n\n${text}` : text));
      return;
    }
    const start = ta.selectionStart ?? ta.value.length;
    const end = ta.selectionEnd ?? start;
    const before = ta.value.slice(0, start);
    const after = ta.value.slice(end);
    setContent(`${before}${text}${after}`);
    // 受控组件重渲染后恢复光标到插入内容之后
    const pos = (before + text).length;
    requestAnimationFrame(() => {
      try {
        ta.focus();
        ta.setSelectionRange(pos, pos);
      } catch { /* 预览态无 textarea，忽略 */ }
    });
  };

  /** 上传图片并插入 markdown（多张依次插入） */
  const uploadAndInsertImages = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    try {
      for (const file of files) {
        try {
          const url = await uploadSpecDocImage(file);
          insertAtCursor(`![图片](${url})\n`);
        } catch (e) {
          Toast({ message: e instanceof Error ? e.message : '图片上传失败', theme: 'error' });
        }
      }
    } finally {
      setUploading(false);
    }
  };

  /** 导入文档：解析为 markdown 追加到正文尾部 */
  const importDoc = async (file: File) => {
    if (file.size > SPEC_DOC_FILE_MAX_MB * 1024 * 1024) {
      Toast({ message: `文档过大，上限 ${SPEC_DOC_FILE_MAX_MB}MB`, theme: 'error' });
      return;
    }
    setUploading(true);
    try {
      const result = await parseSpecDocFile(file);
      const parsed = (result.content || '').trim();
      if (!parsed) {
        Toast({ message: '文档解析结果为空', theme: 'error' });
        return;
      }
      setContent((c) => (c.trim() ? `${c.trimEnd()}\n\n${parsed}` : parsed));
      setMode('edit');
      Toast({ message: '文档已导入，可继续编辑', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '文档解析失败', theme: 'error' });
    } finally {
      setUploading(false);
    }
  };

  /** 粘贴：剪贴板图片 → 上传插入；富文本 HTML → markdown；其余走默认纯文本 */
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (uploading) return;
    const dt = e.clipboardData;
    if (!dt) return;
    const images = Array.from(dt.files || []).filter((f) => f.type.startsWith('image/'));
    if (images.length) {
      e.preventDefault();
      void uploadAndInsertImages(images);
      return;
    }
    const html = dt.getData('text/html');
    if (html) {
      const md = htmlToMarkdown(html);
      if (md.trim()) {
        e.preventDefault();
        insertAtCursor(md);
        return;
      }
    }
    // 无图片无 HTML：默认纯文本粘贴，不干预
  };

  /** 拖拽落下：图片 → 上传插入；文档 → 解析追加 */
  const handleDrop = (e: React.DragEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.dataTransfer?.files || []);
    if (!files.length) return;
    const images = files.filter((f) => isImageFile(f));
    const docs = files.filter((f) => !isImageFile(f));
    e.preventDefault();
    if (images.length) void uploadAndInsertImages(images);
    docs.forEach((d) => void importDoc(d));
  };

  const handleDragOver = (e: React.DragEvent<HTMLTextAreaElement>) => {
    if (e.dataTransfer?.types?.includes('Files')) e.preventDefault();
  };

  return (
    <Popup
      visible={visible}
      onClose={onClose}
      placement="bottom"
      showOverlay
      closeOnOverlayClick={false}
      style={{ zIndex: 13000 }}
    >
      {/* 懒挂载：visible=false 时完全不渲染编辑器子树（含重型 MDEditor）。
          否则展示态也会挂载 MDEditor 并触发 Prism 高亮 —— 超大 base64 正文
          会因此把主线程卡死数十秒（详见上方 useEffect 注释）。 */}
      {visible && (
      <div className="spec-editor" data-color-mode="light">
        <div className="spec-editor__head">
          <span className="spec-editor__title">{title}</span>
          <button type="button" className="spec-editor__close" onClick={onClose} aria-label="关闭">
            关闭
          </button>
        </div>

        <div className="spec-editor__tabs">
          <button
            type="button"
            className={`spec-editor__tab${mode === 'edit' ? ' is-active' : ''}`}
            onClick={() => setMode('edit')}
          >
            编辑
          </button>
          <button
            type="button"
            className={`spec-editor__tab${mode === 'preview' ? ' is-active' : ''}`}
            onClick={() => setMode('preview')}
          >
            预览
          </button>

          <div className="spec-editor__tools">
            <button
              type="button"
              className="spec-editor__tool"
              onClick={() => imgInputRef.current?.click()}
              disabled={uploading || preparing || mode !== 'edit'}
              aria-label="插入图片"
            >
              图片
            </button>
            <button
              type="button"
              className="spec-editor__tool"
              onClick={() => docInputRef.current?.click()}
              disabled={uploading || preparing}
              aria-label="导入文档"
            >
              {uploading ? '处理中…' : '导入文档'}
            </button>
          </div>

          <input
            ref={imgInputRef}
            type="file"
            accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
            multiple
            hidden
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              e.target.value = ''; // 允许重复选择同一文件
              if (files.length) void uploadAndInsertImages(files);
            }}
          />
          <input
            ref={docInputRef}
            type="file"
            accept={SPEC_DOC_ACCEPT}
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void importDoc(file);
            }}
          />
        </div>

        <div className="spec-editor__body">
          {preparing ? (
            <div className="spec-editor__preparing">
              <Loading size="20px" />
              <span>正在把内嵌图片转为在线图片…</span>
            </div>
          ) : (
          <MDEditor
            ref={editorRef}
            value={content}
            onChange={(v) => setContent(v || '')}
            preview={mode}
            height="100%"
            visibleDragbar={false}
            // 预览渲染统一走 appUrlTransform：md 正文里的图片只存裸 /api/... 代理路径
            // （后端返回即入库），test/prod 网关只路由带环境前缀的 /t|/p/api/，
            // 不补前缀则预览 Tab 裂图（附件代理接口免登录，img 直连即可）。
            previewOptions={{ urlTransform: appUrlTransform }}
            textareaProps={{
              onPaste: handlePaste,
              onDrop: handleDrop,
              onDragOver: handleDragOver,
            }}
          />
          )}
        </div>

        <div className="spec-editor__btns">
          <button
            type="button"
            className="spec-editor__btn spec-editor__btn--cancel"
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="spec-editor__btn spec-editor__btn--confirm"
            onClick={() => onSave(content)}
            disabled={saving || uploading || preparing}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
      )}
    </Popup>
  );
}
