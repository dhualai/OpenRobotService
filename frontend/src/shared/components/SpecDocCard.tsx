/**
 * 详情页「问题文档」卡片：
 * - 展示提单人结构化描述（markdown 渲染）+ 接单人补充
 * - 有编辑权限时提供「编辑补充」入口（md 在线编辑，乐观锁）
 */
import { useCallback, useEffect, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SpecDocEditor from '@/shared/components/SpecDocEditor';
import { getSpecDoc, saveSpecDoc, type SpecDoc } from '@/api/specDoc';

interface SpecDocCardProps {
  taskId: number | string;
  /** 是否可编辑（提单人/接单人/管理员） */
  canEdit: boolean;
}

export default function SpecDocCard({ taskId, canEdit }: SpecDocCardProps) {
  const [doc, setDoc] = useState<SpecDoc | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await getSpecDoc(taskId);
      setDoc(d);
    } catch {
      /* 加载失败静默，卡片不阻塞详情页 */
    } finally {
      setLoaded(true);
    }
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async (content: string) => {
    setSaving(true);
    try {
      const saved = await saveSpecDoc(taskId, {
        content,
        revision: doc?.revision,
        source: doc?.source || 'inline',
        source_files: doc?.source_files || [],
      });
      setDoc(saved);
      setEditing(false);
      Toast({ message: '问题文档已保存', theme: 'success' });
    } catch (e) {
      const msg = e instanceof Error ? e.message : '';
      if (msg.includes('409') || msg.includes('已被他人更新')) {
        Toast({ message: '文档已被他人更新，请刷新后重试', theme: 'error' });
        void load();
      } else {
        Toast({ message: `保存失败：${msg || '未知错误'}`, theme: 'error' });
      }
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return null;

  // 无文档：仅可编辑者看到「编写」入口
  if (!doc?.exists) {
    if (!canEdit) return null;
    return (
      <div className="spec-card">
        <div className="spec-card__head">
          <span className="spec-card__title">问题文档</span>
          <div className="spec-card__actions">
            <button type="button" className="spec-card__btn" onClick={() => setEditing(true)}>
              编写
            </button>
          </div>
        </div>
        <div className="spec-card__empty">尚未编写问题文档，可补充完整问题说明（选填）</div>
        <SpecDocEditor
          visible={editing}
          title="问题文档"
          initialValue=""
          saving={saving}
          onClose={() => setEditing(false)}
          onSave={handleSave}
        />
      </div>
    );
  }

  return (
    <div className="spec-card">
      <div className="spec-card__head">
        <span className="spec-card__title">问题文档</span>
        <span className="spec-card__meta">
          {doc.updated_by_name ? `${doc.updated_by_name} 更新` : ''}
          {doc.revision ? ` · 修订 ${doc.revision}` : ''}
        </span>
        {canEdit && (
          <div className="spec-card__actions">
            <button type="button" className="spec-card__btn" onClick={() => setEditing(true)}>
              编辑补充
            </button>
          </div>
        )}
      </div>
      <div className="spec-card__body">
        <MarkdownRenderer content={doc.content} />
      </div>
      {doc.source_files?.length > 0 && (
        <div className="spec-card__files">
          {doc.source_files.map((f) => (
            <div key={f.object_path} className="spec-card__file">
              📎 {f.filename || f.object_path}
            </div>
          ))}
        </div>
      )}
      <SpecDocEditor
        visible={editing}
        title="问题文档"
        initialValue={doc.content}
        saving={saving}
        onClose={() => setEditing(false)}
        onSave={handleSave}
      />
    </div>
  );
}
