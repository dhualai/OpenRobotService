// 工单详情 · 共享讨论区组件
// 历史工单详情页（pages/call/TicketDetailPage）与系统任务工单详情页（pages/tasks/TaskDetailPage）复用。
// 数据共享：两端都走 /api/tasks/{id}/comments（同一工单 → 同一评论流）。
// 布局：当前用户消息靠右（is-right + is-self 蓝气泡），他人靠左。
// 功能开关：enableAttach（附件上传，历史工单用）/ enableAI（@U老师 讨论，系统任务用）。
import { useState, useRef, useEffect, useMemo } from 'react';
import { Button, Toast } from 'tdesign-mobile-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { useAuthStore } from '@/stores/auth';
import { formatTime } from '@/shared/utils/url';
import API_CONFIG from '@/config/api';

export interface DiscussionComment {
  id: string | number;
  content: string;
  created_by_name?: string;
  created_by?: string;
  created_at: string;
  /** 附件列表：object_path 字符串 或 {path,filename,size} 字典（后端 task_comments.attachments JSON 列两种格式并存） */
  attachments?: Array<string | { path?: string; filename?: string; size?: number }>;
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp)$/i;
/** 解析评论附件（字符串 object_path 或字典），提取 object_path/filename/isImage */
const parseAttachment = (a: string | { path?: string; filename?: string; size?: number }) => {
  const objectPath = typeof a === 'string' ? a : (a.path || '');
  const filename = typeof a === 'string'
    ? (a.split('/').pop() || a)
    : (a.filename || objectPath.split('/').pop() || '文件');
  return { objectPath, filename, isImage: IMAGE_EXT.test(filename) };
};

export interface ProjectMember {
  id: string;
  username: string;
  name?: string | null;
  role_name?: string | null;
}

interface DiscussionPanelProps {
  /** 评论列表（两端共用 /api/tasks/{id}/comments 数据） */
  comments: DiscussionComment[];
  /** 发送：父级处理 POST 评论 / @U老师 路由 / 附件上传；返回 true=成功（组件清空输入），false=失败（保留输入） */
  onSend: (text: string, files: File[]) => Promise<boolean>;
  /** 发送中（禁用输入与按钮、按钮文案变“发送中”） */
  sending?: boolean;
  /** 整体禁用（如工单号缺失） */
  disabled?: boolean;
  placeholder?: string;
  /** 附件上传（历史工单） */
  enableAttach?: boolean;
  /** @U老师 讨论（系统任务）：点击在输入框前缀 @U老师 */
  enableAI?: boolean;
  /** 消息区点击（系统任务：点诊断报告链接打开弹窗） */
  onMessagesClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
  /** 标题右侧额外内容（如”帮我分析”按钮，仅系统任务用） */
  headerRight?: React.ReactNode;
  /** 标题，默认”讨论（N）” */
  title?: string;
  className?: string;
  /** @提及用户列表（系统任务：项目成员，用于 @ 弹窗选择） */
  mentionUsers?: ProjectMember[];
}

export default function DiscussionPanel({
  comments,
  onSend,
  sending = false,
  disabled = false,
  placeholder,
  enableAttach = false,
  enableAI = false,
  onMessagesClick,
  headerRight,
  title,
  className = '',
  mentionUsers,
}: DiscussionPanelProps) {
  const { username, name } = useAuthStore();
  const [commentText, setCommentText] = useState('');
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  // @mention state
  const [showMentions, setShowMentions] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);

  // 新消息到达 → 滚到底部
  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [comments]);

  // commentText 变化时自适应高度（覆盖 @U老师 按钮 / mention 选择等程序化修改）
  useEffect(() => {
    const el = inputRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 160) + 'px';
    }
  }, [commentText]);

  const handleSelectFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) setPendingFiles((prev) => [...prev, ...files]);
    e.target.value = '';
  };
  const removeFile = (idx: number) => setPendingFiles((prev) => prev.filter((_, i) => i !== idx));

  // @U老师：在输入框前缀 @U老师（父级 onSend 依此前缀路由到 AI 讨论）
  const handleAIClick = () => {
    if (!commentText.startsWith('@U老师 ')) setCommentText('@U老师 ' + commentText);
  };

  // ── @mention: 过滤项目成员 ──
  const filteredMentionUsers = useMemo(() => {
    if (!mentionUsers || mentionUsers.length === 0) return [];
    if (!mentionFilter) return mentionUsers;
    const kw = mentionFilter.toLowerCase();
    return mentionUsers.filter(
      (u) =>
        (u.username || '').toLowerCase().includes(kw) ||
        (u.name || '').toLowerCase().includes(kw),
    );
  }, [mentionUsers, mentionFilter]);

  // 重置 mentionIndex 当过滤结果变化时
  useEffect(() => {
    setMentionIndex(0);
  }, [filteredMentionUsers]);

  // ── @mention: 检测 @ 触发 + 自动增高 ──
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const el = e.target;
    const val = el.value;
    setCommentText(val);

    // 自动增高：先归零再用 scrollHeight 撑开
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';

    if (!mentionUsers || mentionUsers.length === 0) return;

    const cursorPos = el.selectionStart ?? val.length;
    const textBeforeCursor = val.slice(0, cursorPos);
    const atMatch = textBeforeCursor.match(/@([\w一-鿿]*)$/);

    if (atMatch) {
      setMentionFilter(atMatch[1]);
      setShowMentions(true);
    } else {
      setShowMentions(false);
    }
  };

  // ── @mention: 选中用户 → 替换 @filter 为 @名字  ──
  const handleMentionSelect = (user: ProjectMember) => {
    const cursorPos = inputRef.current?.selectionStart ?? commentText.length;
    const textBeforeCursor = commentText.slice(0, cursorPos);
    const textAfterCursor = commentText.slice(cursorPos);

    const displayName = user.name || user.username;
    const newBefore = textBeforeCursor.replace(/@([\w一-鿿]*)$/, `@${displayName} `);
    const newText = newBefore + textAfterCursor;

    setCommentText(newText);
    setShowMentions(false);

    setTimeout(() => {
      inputRef.current?.focus();
      const pos = newBefore.length;
      inputRef.current?.setSelectionRange(pos, pos);
    }, 0);
  };

  // ── 键盘事件：处理 @mention 导航 / Enter 发送 / Shift+Enter 换行 ──
  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showMentions && filteredMentionUsers.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionIndex((prev) => (prev + 1) % filteredMentionUsers.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionIndex((prev) => (prev - 1 + filteredMentionUsers.length) % filteredMentionUsers.length);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleMentionSelect(filteredMentionUsers[mentionIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowMentions(false);
        return;
      }
    }
    // Enter 发送（Shift+Enter 换行不做处理，让 textarea 原生行为插入换行）
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── 粘贴文件/图片：从剪贴板提取文件，加入待发送列表 ──
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const pastedFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) pastedFiles.push(file);
      }
    }
    // 优先从 files 属性获取（文件管理器复制场景）
    if (pastedFiles.length === 0 && e.clipboardData.files.length > 0) {
      for (let i = 0; i < e.clipboardData.files.length; i++) {
        pastedFiles.push(e.clipboardData.files[i]);
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault();
      setPendingFiles((prev) => [...prev, ...pastedFiles]);
    }
  };

  const canSend = !sending && !disabled && (commentText.trim().length > 0 || pendingFiles.length > 0);

  const handleSend = async () => {
    if (!canSend) return;
    const text = commentText.trim();
    const files = pendingFiles;
    let ok = false;
    try {
      ok = await onSend(text, files);
    } catch (err) {
      Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
    if (ok) {
      setCommentText('');
      setPendingFiles([]);
    }
  };

  const ph = placeholder ?? (enableAI ? '直接评论或者 @U老师 进行讨论。' : '参与讨论…');

  return (
    <div className={`detail-card detail-chat-container ${className}`.trim()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4 className="detail-card__h">{title ?? `讨论（${comments.length}）`}</h4>
        {headerRight}
      </div>
      <div className="detail-chat-messages" ref={chatMessagesRef} onClick={onMessagesClick}>
        {comments.length > 0 ? (
          comments.map((c) => {
            const authorName = c.created_by_name || c.created_by || '未知用户';
            const isCurrentUser =
              (c.created_by?.toLowerCase() === username?.toLowerCase()) ||
              (c.created_by_name?.toLowerCase() === username?.toLowerCase()) ||
              (c.created_by_name?.toLowerCase() === name?.toLowerCase());
            return (
              <div key={c.id} className={`detail-chat-row ${isCurrentUser ? 'is-right' : ''}`}>
                <div className={`detail-chat-bubble ${isCurrentUser ? 'is-self' : ''}`}>
                  <div className="detail-chat-name">{authorName}</div>
                  <MarkdownRenderer content={c.content} compact />
                  {c.attachments && c.attachments.length > 0 && (
                    <div className="detail-chat-attachments">
                      {c.attachments.map((a, i) => {
                        const att = parseAttachment(a);
                        if (!att.objectPath) return null;
                        const url = `${API_CONFIG.TASKS.BASE_URL}/files/${att.objectPath}`;
                        if (att.isImage) {
                          return (
                            <img
                              key={i}
                              src={url}
                              alt={att.filename}
                              className="detail-chat-attachment-img"
                              onClick={() => setPreviewUrl(url)}
                            />
                          );
                        }
                        return (
                          <div
                            key={i}
                            className="detail-chat-attachment-file"
                            onClick={() => window.open(url, '_blank', 'noopener,noreferrer')}
                          >
                            📎 {att.filename}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <div className="detail-chat-time">{formatTime(c.created_at)}</div>
                </div>
              </div>
            );
          })
        ) : (
          <div className="detail-chat-empty">暂无评论</div>
        )}
      </div>
      <div className="detail-chat-input" style={{ position: 'relative' }}>
        {/* @mention suggestion panel */}
        {showMentions && filteredMentionUsers.length > 0 && (
          <div className="detail-chat-mention-panel">
            {filteredMentionUsers.map((u, i) => (
              <div
                key={u.id}
                className={`detail-chat-mention-item ${i === mentionIndex ? 'is-active' : ''}`}
                onMouseDown={(e) => { e.preventDefault(); handleMentionSelect(u); }}
              >
                <span className="detail-chat-mention-name">{u.name || u.username}</span>
                <span className="detail-chat-mention-role">{u.role_name || ''}</span>
              </div>
            ))}
          </div>
        )}
        {(enableAI || (enableAttach && pendingFiles.length > 0)) && (
          <div className="detail-chat-toolbar">
            {enableAI && (
              <Button size="small" theme="default" onClick={handleAIClick} disabled={sending || disabled}>
                @U老师
              </Button>
            )}
            {enableAttach && pendingFiles.length > 0 && (
              <div className="detail-chat-files">
                {pendingFiles.map((f, i) => (
                  <span key={i} className="detail-chat-file">
                    <span className="detail-chat-file__name">{f.name}</span>
                    <button type="button" onClick={() => removeFile(i)} aria-label="移除">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="detail-chat-input-row">
          <textarea
            ref={inputRef}
            className="detail-chat-input-field"
            value={commentText}
            onChange={handleInputChange}
            onKeyDown={handleInputKeyDown}
            onPaste={handlePaste}
            placeholder={disabled ? '工单号缺失，无法评论' : ph}
            disabled={sending || disabled}
            rows={1}
          />
          {enableAttach && (
            <button
              type="button"
              className="detail-chat-attach"
              onClick={() => fileInputRef.current?.click()}
              disabled={sending || disabled}
              aria-label="上传图片或文件"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
            </button>
          )}
          <Button size="small" theme="primary" onClick={handleSend} disabled={!canSend}>
            {sending ? '发送中' : '发送'}
          </Button>
          {enableAttach && (
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleSelectFile} />
          )}
        </div>
      </div>

      {/* 图片预览：点击评论附件图片放大查看 */}
      {previewUrl && (
        <div className="chat-image-preview" onClick={() => setPreviewUrl(null)}>
          <img src={previewUrl} alt="预览" className="chat-image-preview__img" onClick={(e) => e.stopPropagation()} />
          <span className="chat-image-preview__close" onClick={() => setPreviewUrl(null)}>✕</span>
        </div>
      )}
    </div>
  );
}
