/**
 * 详情页「详细问题文档」入口（原「问题文档」详情卡片，2026-10-08 改为标题行入口 + 弹窗）：
 * - 入口按钮挂在「问题描述」标题行右侧（标题左对齐 / 按钮右对齐）；问题描述为空也渲染；
 *   无文档且无编辑权限时不渲染（先等加载完成再判断，避免按钮闪现后消失）
 * - 点击弹窗：移动端底部弹层 / PC 居中弹窗（口径同 ProjectInfoEditDrawer），点遮罩可关
 * - 弹窗内容沿用原卡片：markdown 正文 + 附件 + 可编辑者的「AI 汇总讨论内容」/「编辑补充」
 *   - 编辑补充：md 在线编辑，乐观锁（409 提示刷新后重试）
 *   - AI 汇总讨论内容：把讨论区评论交给大模型整理成补充段（复用 AiProblemDocGenerator，
 *     scene="discussion"；确认后经 replaceUserSection 只替换分隔线以下的补充段，
 *     系统段原样保留，再走 saveSpecDoc 乐观锁落库）
 */
import { useCallback, useEffect, useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SpecDocEditor from '@/shared/components/SpecDocEditor';
import AiProblemDocGenerator from '@/shared/components/AiProblemDocGenerator';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { isPC } from '@/shared/utils/device';
import { getSpecDoc, saveSpecDoc, type SpecDoc, type ProblemDocSourceItem } from '@/api/specDoc';
import { listShareDocRootTags, replaceUserSection, splitShareDoc } from '@/shared/utils/shareDoc';
// 自带样式：弹窗（.spec-sheet）与入口按钮（.detail-card__h-action）用到的都在这里
// （SpecDocEditor 也 import 了本文件，幂等）
import '@/shared/styles/specDoc.css';

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
  /** 弹窗开关（移动端底部 / PC 居中） */
  const [visible, setVisible] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  /** 「AI 汇总讨论内容」的素材：讨论区评论（接口按时间倒序 → 转正序） */
  const [commentItems, setCommentItems] = useState<ProblemDocSourceItem[]>([]);
  // UA 判定一次即可（同一会话不会变）：PC 居中弹窗、移动端底部弹层
  const pc = isPC();

  const load = useCallback(async () => {
    try {
      const d = await getSpecDoc(taskId);
      setDoc(d);
    } catch {
      /* 加载失败静默，入口不阻塞详情页 */
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

  const exists = doc?.exists === true;
  const docContent = doc?.content ?? '';
  const sourceFiles = doc?.source_files ?? [];
  /** 系统段里实际带入的一级标签（只读；上传/手写文档解析为空 → 整块不渲染） */
  const rootTags = listShareDocRootTags(docContent);
  /** 副标题：谁更新过 · 修订号（都为空则不渲染这一行） */
  const meta = [
    doc?.updated_by_name ? `${doc.updated_by_name} 更新` : '',
    doc?.revision ? `修订 ${doc.revision}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  // 无文档且无编辑权限：入口整体不渲染。
  // 先等加载完成再判断（与改动前卡片一致），否则按钮会先出现再消失。
  if (!loaded || (!exists && !canEdit)) return null;

  return (
    <>
      <button
        type="button"
        className="detail-card__h-action"
        aria-haspopup="dialog"
        onClick={() => setVisible(true)}
      >
        详细问题文档
      </button>

      <Popup
        visible={visible}
        placement={pc ? 'center' : 'bottom'}
        onClose={() => setVisible(false)}
        showOverlay
        closeOnOverlayClick
        // 层级链：本弹窗 12000 < 编辑器 SpecDocEditor 13000 < AI 预览 13010
        style={{ zIndex: 12000 }}
      >
        {/* 懒挂载：不可见时不渲染内容子树，避免无谓拉取与渲染 */}
        {visible && (
          <div className={`spec-sheet${pc ? ' spec-sheet--center' : ''}`}>
            <div className="spec-sheet__head">
              <span className="spec-sheet__title">详细问题文档</span>
              <button type="button" className="spec-sheet__close" onClick={() => setVisible(false)}>
                关闭
              </button>
            </div>

            {/* 唯一滚动容器：正文 / 附件都在这里滚，避免与详情页双滚动条 */}
            <div className="spec-sheet__body">
              {meta ? <div className="spec-sheet__meta">{meta}</div> : null}

              {/* 带入的项目背景信息标签（只读）：来自系统段 `### ` 一级标题，
                  让人不点「编辑补充」也知道这份文档带了哪几块；改动勾选仍回提单流程 */}
              {rootTags.length > 0 && (
                <div className="spec-sheet__tags" data-testid="spec-sheet-tags">
                  <span className="spec-sheet__tags-label">带入标签</span>
                  {rootTags.map((tag) => (
                    <span key={tag} className="spec-sheet__tag">
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {canEdit && (
                <div className="spec-sheet__actions">
                  <AiProblemDocGenerator
                    items={commentItems}
                    scene="discussion"
                    hasUserContent={hasUserContent}
                    label="AI 汇总讨论内容"
                    triggerClassName="spec-sheet__btn spec-sheet__btn--primary"
                    onApply={(md) => void applyDiscussionSummary(md)}
                  />
                  <button
                    type="button"
                    className="spec-sheet__btn"
                    onClick={() => setEditing(true)}
                  >
                    {exists ? '编辑补充' : '编写'}
                  </button>
                </div>
              )}

              {exists ? (
                <>
                  <div className="spec-sheet__doc">
                    <MarkdownRenderer content={docContent} />
                  </div>
                  {sourceFiles.length > 0 && (
                    <div className="spec-sheet__files">
                      {sourceFiles.map((f) => (
                        <div key={f.object_path} className="spec-sheet__file">
                          📎 {f.filename || f.object_path}
                        </div>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <div className="spec-sheet__empty">尚未编写问题文档，可补充完整问题说明（选填）</div>
              )}
            </div>

            <SpecDocEditor
              visible={editing}
              title="详细问题文档"
              initialValue={exists ? docContent : ''}
              saving={saving}
              onClose={() => setEditing(false)}
              onSave={handleSave}
            />
          </div>
        )}
      </Popup>
    </>
  );
}
