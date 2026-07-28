// 工单详情 · 共享讨论区组件
// 历史工单详情页（pages/call/TicketDetailPage）与系统任务工单详情页（pages/tasks/TaskDetailPage）复用。
// 数据共享：两端都走 /api/tasks/{id}/comments（同一工单 → 同一评论流）。
// 布局：当前用户消息靠右（is-right + is-self 蓝气泡），他人靠左。
// 功能开关：enableAttach（附件上传，历史工单用）/ enableAI（@AI 讨论，系统任务用）。
import { useState, useRef, useEffect } from 'react';
import { Button, Toast } from 'tdesign-mobile-react';
import SafeHtml from '@/shared/components/SafeHtml';
import { useAuthStore } from '@/stores/auth';
import { formatTime } from '@/shared/utils/url';

export interface DiscussionComment {
  id: string | number;
  content: string;
  created_by_name?: string;
  created_by?: string;
  created_at: string;
}

interface DiscussionPanelProps {
  /** 评论列表（两端共用 /api/tasks/{id}/comments 数据） */
  comments: DiscussionComment[];
  /** 发送：父级处理 POST 评论 / @AI 路由 / 附件上传；返回 true=成功（组件清空输入），false=失败（保留输入） */
  onSend: (text: string, files: File[]) => Promise<boolean>;
  /** 发送中（禁用输入与按钮、按钮文案变“发送中”） */
  sending?: boolean;
  /** 整体禁用（如工单号缺失） */
  disabled?: boolean;
  placeholder?: string;
  /** 附件上传（历史工单） */
  enableAttach?: boolean;
  /** @AI 讨论（系统任务）：点击在输入框前缀 @AI */
  enableAI?: boolean;
  /** 消息区点击（系统任务：点诊断报告链接打开弹窗） */
  onMessagesClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
  /** 标题，默认“讨论（N）” */
  title?: string;
  className?: string;
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
  title,
  className = '',
}: DiscussionPanelProps) {
  const { username, name } = useAuthStore();
  const [commentText, setCommentText] = useState('');
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  // 新消息到达 → 滚到底部
  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [comments]);

  const handleSelectFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) setPendingFiles((prev) => [...prev, ...files]);
    e.target.value = '';
  };
  const removeFile = (idx: number) => setPendingFiles((prev) => prev.filter((_, i) => i !== idx));

  // @AI：在输入框前缀 @AI（父级 onSend 依此前缀路由到 AI 讨论）
  const handleAIClick = () => {
    if (!commentText.startsWith('@AI ')) setCommentText('@AI ' + commentText);
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

  const ph = placeholder ?? (enableAI ? '直接评论或者 @AI 进行讨论。' : '参与讨论…');

  return (
    <div className={`detail-card detail-chat-container ${className}`.trim()}>
      <h4 className="detail-card__h">{title ?? `讨论（${comments.length}）`}</h4>
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
                  <SafeHtml html={c.content} />
                  <div className="detail-chat-time">{formatTime(c.created_at)}</div>
                </div>
              </div>
            );
          })
        ) : (
          <div className="detail-chat-empty">暂无评论</div>
        )}
      </div>
      <div className="detail-chat-input">
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
        <input
          className="detail-chat-input-field"
          value={commentText}
          onChange={(e) => setCommentText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleSend(); }}
          placeholder={disabled ? '工单号缺失，无法评论' : ph}
          disabled={sending || disabled}
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
        {enableAI && (
          <Button size="small" theme="default" onClick={handleAIClick} disabled={sending || disabled}>
            @AI
          </Button>
        )}
        <Button size="small" theme="primary" onClick={handleSend} disabled={!canSend}>
          {sending ? '发送中' : '发送'}
        </Button>
        {enableAttach && (
          <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleSelectFile} />
        )}
      </div>
    </div>
  );
}
