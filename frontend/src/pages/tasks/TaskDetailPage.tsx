import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { TICKET_TYPE_DISPLAY_MAP, STATUS_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface Attachment { id: string; url: string; }
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; ai_summary?: string; comments?: Comment[];
}

export default function TaskDetailPage() {
  const { id: detailId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const { refreshTasks, setChatContext, goToTab } = useWorkbenchStore();
  const currentUsername = useAuthStore((s) => s.username);

  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '' });
  const [escalateUser, setEscalateUser] = useState<UserItem | null>(null);
  const [showEscalatePopup, setShowEscalatePopup] = useState(false);
  const [commentText, setCommentText] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!detailId) { setDetail(null); return; }
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`)
      .then(setDetail)
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  }, [detailId]);

  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [detail?.comments?.length]);



  const getActionButton = () => {
    const status = detail?.status?.toLowerCase();
    if (!status) return null;
    
    const actions: Record<string, { label: string; nextStatus: string; theme: string }> = {
      new: { label: '开始处理', nextStatus: 'in_progress', theme: 'primary' },
      in_progress: { label: '处理完成', nextStatus: 'resolved', theme: 'success' },
      pending: { label: '继续处理', nextStatus: 'in_progress', theme: 'primary' },
      resolved: { label: '确认关闭', nextStatus: 'closed', theme: 'default' },
      canceled: { label: '重新打开', nextStatus: 'new', theme: 'primary' },
    };
    
    return actions[status] || null;
  };

  const handleStatusChange = async () => {
    const action = getActionButton();
    if (!action || !detail) return;
    
    try {
      const updated = await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: action.nextStatus }),
      });
      setDetail(updated);
      Toast({ message: `状态已更新为${STATUS_DISPLAY_MAP[action.nextStatus] || action.nextStatus}`, theme: 'success' });
    } catch (err) {
      Toast({ message: `状态更新失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handleEscalate = async (t: Ticket) => {
    if (!escalateUser) {
      Toast({ message: '请先选择升级对象', theme: 'warning' });
      return;
    }
    const target = escalateUser.name || escalateUser.username;
    try {
      await request('/', {
        method: 'POST',
        body: JSON.stringify({
          title: `【升级→${target}】${t.title}`,
          description: `原工单 #${t.id}「${t.title}」申请升级给 ${target}，请处理。\n\n原始描述：${t.description || '无'}`,
          ticket_type: t.ticket_type || 'problem',
          priority: 'urgent',
          related_resource_id: Number(t.id),
          assigned_to: escalateUser.id,
        }),
      });
      Toast({ message: `已升级，已指派给 ${target}`, theme: 'success' });
      setEscalateUser(null);
      setShowEscalatePopup(false);
    } catch (err) {
      Toast({ message: `升级失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const startEdit = () => { if (!detail) return; setEditForm({ title: detail.title, description: detail.description }); setEditing(true); };

  const saveEdit = async () => {
    if (!detail) return;
    try {
      const updated = await request<Ticket>(`/${detail.id}`, { method: 'PUT', body: JSON.stringify(editForm) });
      Toast({ message: '修改成功', theme: 'success' });
      setEditing(false);
      refreshTasks();
      setDetail(updated);
    } catch (err) {
      Toast({ message: `修改失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };



  const copyId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => Toast({ message: '已复制工单号', theme: 'success' }));
  };

  const handleAddComment = async () => {
    if (!detail || !commentText.trim()) {
      Toast({ message: '请输入评论内容', theme: 'warning' });
      return;
    }
    setSubmittingComment(true);
    try {
      const newComment = await request<Comment>(`/${detail.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: commentText.trim(), is_public: true }),
      });
      setDetail((prev) => {
        if (!prev) return prev;
        const updatedComments = prev.comments ? [...prev.comments, newComment] : [newComment];
        return { ...prev, comments: updatedComments };
      });
      setCommentText('');
      Toast({ message: '评论已添加', theme: 'success' });
    } catch (err) {
      Toast({ message: `添加评论失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingComment(false);
    }
  };

  if (detailLoading) return <Loading text="加载中…" />;
  if (!detail) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/tasks')} />
      <div style={{ padding: 32, textAlign: 'center', color: '#999', marginTop: 56 }}>工单不存在</div>
    </div>
  );

  return (
    <div className="task-detail-page" style={{ paddingBottom: 72 }}>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/tasks')} />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {detail.attachments?.[0] && (
          <img src={detail.attachments[0].url} alt="附件" className="ticket-detail__cover" />
        )}

        <div className="detail-card">
          <div className="detail-card__header">
            <div className="detail-card__meta">
              <Tag theme="primary">{TICKET_TYPE_DISPLAY_MAP[detail.ticket_type] || detail.ticket_type || '其他'}</Tag>
              <span className="detail-card__id" onClick={() => copyId(detail.id)}>#${detail.id}</span>
            </div>
            {(() => {
              const action = getActionButton();
              return action ? (
                <Button 
                  size="small" 
                  theme={action.theme as any} 
                  onClick={handleStatusChange}
                  className="detail-card__action-btn"
                >
                  {action.label}
                </Button>
              ) : null;
            })()}
          </div>
          <h2 className="detail-card__title">{detail.title}</h2>
          <DetailRow label="创建时间" value={formatDateTime(detail.created_at)} />
          <DetailRow label="更新时间" value={formatDateTime(detail.updated_at)} />
        </div>

        <div className="detail-card">
          <h4 className="detail-card__h">问题描述</h4>
          <SafeHtml html={detail.description || '<p style="color:#999">无描述</p>'} />
        </div>

        <div className="detail-card">
          <h4 className="detail-card__h">🤖 AI 讨论摘要</h4>
          <p>{detail.ai_summary || 'AI 摘要生成中…'}</p>
        </div>

        {(detail.contact || detail.reporter_name || detail.assignee_name) && (
          <div className="detail-card">
            <h4 className="detail-card__h">联系方式</h4>
            {detail.contact && <DetailRow label="联系电话" value={detail.contact} />}
            {detail.reporter_name && <DetailRow label="提交人" value={detail.reporter_name} />}
            {detail.assignee_name && <DetailRow label="处理人" value={detail.assignee_name} />}
          </div>
        )}

        {detail.project_name && (
          <div className="detail-card">
            <DetailRow label="所属项目" value={detail.project_name} />
          </div>
        )}

        <div className="detail-card detail-chat-container">
          <h4 className="detail-card__h">讨论（{detail.comments?.length || 0}）</h4>
          <div className="detail-chat-messages" ref={chatMessagesRef}>
            {detail.comments && detail.comments.length > 0 ? (
              detail.comments.map((c) => {
                const authorName = c.created_by_name || c.created_by || '未知用户';
                const isCurrentUser = (c.created_by?.toLowerCase() === currentUsername?.toLowerCase()) ||
                                     (c.created_by_name?.toLowerCase() === currentUsername?.toLowerCase());
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
            <input
              className="detail-chat-input-field"
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddComment(); }}
              placeholder="直接评论或者 @AI 进行讨论。"
              disabled={submittingComment}
            />
            <Button
              size="small"
              theme="primary"
              onClick={handleAddComment}
              disabled={submittingComment || !commentText.trim()}
            >
              {submittingComment ? '发送中' : '发送'}
            </Button>
          </div>
        </div>

        <div className="detail-actions">
          <div className="detail-actions__btns">
            <Button size="small" theme="default" onClick={startEdit}>修改工单</Button>
            <Button size="small" theme="danger" onClick={() => setShowEscalatePopup(true)}>升级上报</Button>
          </div>
        </div>
      </div>

      <Popup visible={editing} onClose={() => setEditing(false)} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4>修改工单</h4>
          <input className="tasks-search" value={editForm.title} onChange={(e) => setEditForm((p) => ({ ...p, title: e.target.value }))} placeholder="标题" />
          <Textarea value={editForm.description} onChange={(v) => setEditForm((p) => ({ ...p, description: String(v) }))} autosize={{ minRows: 4, maxRows: 10 }} placeholder="描述" />
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => setEditing(false)}>取消</Button>
            <Button theme="primary" onClick={saveEdit}>保存</Button>
          </div>
        </div>
      </Popup>

      <Popup visible={showEscalatePopup} onClose={() => setShowEscalatePopup(false)} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4>升级上报</h4>
          <p style={{ color: '#999', fontSize: '13px', marginBottom: '12px' }}>请选择升级对象</p>
          <UserSelect value={escalateUser?.id ?? null} onChange={setEscalateUser} />
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => setShowEscalatePopup(false)}>取消</Button>
            <Button theme="danger" onClick={() => handleEscalate(detail!)}>确认升级</Button>
          </div>
        </div>
      </Popup>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-row">
      <span className="detail-row__label">{label}</span>
      <span className="detail-row__value">{value}</span>
    </div>
  );
}