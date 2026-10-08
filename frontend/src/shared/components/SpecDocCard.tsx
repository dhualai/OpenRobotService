/**
 * 详情页「问题文档」卡片：
 * - 展示提单人结构化描述（markdown 渲染）+ 接单人补充
 * - 有编辑权限时提供「编辑补充」入口（md 在线编辑，乐观锁）
 * - 有编辑权限时提供「AI 汇总讨论内容」：把讨论区评论交给大模型整理成补充段
 *   （复用提单页的 AiProblemDocGenerator，scene="discussion"；生成后可预览编辑，
 *   确认后经 replaceUserSection 只替换分隔线以下的补充段，系统段原样保留，
 *   再走 saveSpecDoc 乐观锁落库）
 */
import { useCallback, useEffect, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SpecDocEditor from '@/shared/components/SpecDocEditor';
import AiProblemDocGenerator from '@/shared/components/AiProblemDocGenerator';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { getSpecDoc, saveSpecDoc, type SpecDoc, type ProblemDocSourceItem } from '@/api/specDoc';
import { replaceUserSection, splitShareDoc } from '@/shared/utils/shareDoc';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

/** 讨论区评论行（GET /{task_id}/comments 的最小字段） */
interface TaskCommentRow {
  content: string;
  created_by_name?: string | null;
  created_by?: string;
  created_at?: string;
}

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
  /** 「AI 汇总讨论内容」的素材：讨论区评论（接口按时间倒序 → 转正序） */
  const [commentItems, setCommentItems] = useState<ProblemDocSourceItem[]>([]);

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

  // 讨论区评论（仅可编辑者需要：AI 汇总入口按 canEdit 显示；失败静默，入口自动禁用）
  useEffect(() => {
    if (!canEdit) return;
    let cancelled = false;
    request<TaskCommentRow[]>(`/${Number(taskId)}/comments`)
      .then((list) => {
        if (cancelled) return;
        const rows = (Array.isArray(list) ? list : []).slice().reverse();
        setCommentItems(
          rows
            .filter((row) => (row?.content ?? '').trim())
            .map((row) => ({
              author: row.created_by_name || row.created_by || '未知用户',
              content: row.content,
              created_at: row.created_at,
            })),
        );
      })
      .catch(() => {
        if (!cancelled) setCommentItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, [taskId, canEdit]);

  const handleSave = async (content: string, source?: string) => {
    setSaving(true);
    try {
      const saved = await saveSpecDoc(taskId, {
        content,
        revision: doc?.revision,
        source: source ?? (doc?.source || 'inline'),
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

  /** AI 汇总讨论内容：只替换分隔线以下的补充段（项目背景信息/此前写的保留），乐观锁写入 */
  const applyDiscussionSummary = async (markdown: string) => {
    await handleSave(replaceUserSection(doc?.content ?? '', markdown), 'ai_summary');
  };

  /** 补充段是否已有内容（AI 生成前要提示「这部分会被替换」） */
  const hasUserContent = splitShareDoc(doc?.content ?? '').user.trim().length > 0;

  if (!loaded) return null;

  // 无文档：仅可编辑者看到「编写」入口
  if (!doc?.exists) {
    if (!canEdit) return null;
    return (
      <div className="spec-card">
        <div className="spec-card__head">
          <span className="spec-card__title">问题文档</span>
          <div className="spec-card__actions">
            <AiProblemDocGenerator
              items={commentItems}
              scene="discussion"
              hasUserContent={hasUserContent}
              label="AI 汇总讨论内容"
              triggerClassName="spec-card__btn spec-card__btn--primary"
              onApply={(md) => void applyDiscussionSummary(md)}
            />
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
            <AiProblemDocGenerator
              items={commentItems}
              scene="discussion"
              hasUserContent={hasUserContent}
              label="AI 汇总讨论内容"
              triggerClassName="spec-card__btn spec-card__btn--primary"
              onApply={(md) => void applyDiscussionSummary(md)}
            />
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
