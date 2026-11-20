/**
 * 提单确认弹窗内的「问题文档」字段（非必填）。
 *
 * - 上传文档（.md/.doc/.docx/.txt）→ 后端解析为 markdown 并保留原文件
 * - 在线编写 → 打开 SpecDocEditor（md 在线编辑）
 * - 已填内容折叠预览 + 清除
 */
import { useRef, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SpecDocEditor from '@/shared/components/SpecDocEditor';
import { parseSpecDocFile, type SpecDocSourceFile } from '@/api/specDoc';
import { SPEC_DOC_ACCEPT } from '@/shared/utils/fileKind';

/** 提单弹窗内暂存的「问题文档」草稿（随 overrides.spec_doc 透传给后端） */
export interface SpecDocDraft {
  content: string;
  source: 'inline' | 'upload' | 'ai_summary';
  source_files: SpecDocSourceFile[];
}

interface SpecDocFieldProps {
  value: SpecDocDraft | null;
  onChange: (v: SpecDocDraft | null) => void;
  disabled?: boolean;
}

export default function SpecDocField({ value, onChange, disabled = false }: SpecDocFieldProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [editing, setEditing] = useState(false);
  const [parsing, setParsing] = useState(false);

  const handleUpload = async (file: File) => {
    if (!file) return;
    setParsing(true);
    try {
      const res = await parseSpecDocFile(file);
      onChange({
        content: res.content,
        source: 'upload',
        source_files: res.object_path
          ? [{ object_path: res.object_path, filename: res.filename, size: res.size }]
          : [],
      });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '文档解析失败', theme: 'error' });
    } finally {
      setParsing(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleEditorSave = (content: string) => {
    onChange({
      content,
      source: 'inline',
      // 之前上传过原件则保留引用，否则清空
      source_files: value?.source === 'upload' ? value.source_files : [],
    });
    setEditing(false);
  };

  return (
    <div className="spec-field">
      <div className="spec-field__head">
        <div className="spec-field__actions">
          <button
            type="button"
            className="spec-field__btn"
            disabled={disabled || parsing}
            onClick={() => fileInputRef.current?.click()}
          >
            {parsing ? '解析中…' : '上传文档'}
          </button>
          <button
            type="button"
            className="spec-field__btn"
            disabled={disabled}
            onClick={() => setEditing(true)}
          >
            在线编写
          </button>
        </div>
      </div>
      <p className="spec-field__hint">
        复杂问题可上传 .md / .doc / .docx 或在线编写完整说明（选填），接单人可在此基础上补充
      </p>

      <input
        ref={fileInputRef}
        type="file"
        accept={SPEC_DOC_ACCEPT}
        style={{ display: 'none' }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleUpload(f);
        }}
      />

      {value?.content ? (
        <div className="spec-field__preview">
          {value.source_files?.length > 0 && (
            <div className="spec-field__file">📎 {value.source_files[0].filename}</div>
          )}
          <MarkdownRenderer content={value.content} compact />
          <button type="button" className="spec-field__clear" onClick={() => onChange(null)}>
            清除文档
          </button>
        </div>
      ) : null}

      <SpecDocEditor
        visible={editing}
        title="问题文档（在线编写）"
        initialValue={value?.content || ''}
        onClose={() => setEditing(false)}
        onSave={handleEditorSave}
      />
    </div>
  );
}
