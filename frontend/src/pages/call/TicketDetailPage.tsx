// 摇人 · 历史工单详情操作页（全屏）
// 从 摇人「历史工单」点入，不跳系统任务页。合并旧 TicketDetail.tsx（状态流转+评论）
// 与 TasksView 详情弹层（催办/修改/讨论/上报）的能力。
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import { useWorkbenchStore } from '@/stores/workbench';
import { normalizeStatus, STATUS_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface Attachment { id: string; url: string; }
interface Comment { id: string; content: string; author_name: string; created_at: string; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; comments?: Comment[];
}

const statusTheme = (s: string): 'success' | 'primary' | 'warning' =>
  s === 'closed' ? 'success' : s === 'new' ? 'primary' : 'warning';

export default function TicketDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const { setChatContext, goToTab } = useWorkbenchStore();

  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [loading, setLoading] = useState(true);
  const [newComment, setNewComment] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '' });

  const fetchDetail = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    try {
      const data = await request<Ticket>(`/${id}?load_comments=true`);
      setTicket(data);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  const handleStatusChange = async (newStatus: string) => {
    if (!id) return;
    try {
      await request(`/${id}/status?status=${newStatus}`, { method: 'PATCH' });
      Toast({ message: '状态更新成功', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `更新失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    }
  };

  const handleUrge = async () => {
    if (!ticket) return;
    try { await request(`/${ticket.id}/urge`, { method: 'POST' }).catch(() => {}); Toast({ message: '已催办', theme: 'success' }); }
    catch { Toast({ message: '催办失败', theme: 'error' }); }
  };
  const handleEscalate = async () => {
    if (!ticket) return;
    try { await request(`/${ticket.id}/escalate`, { method: 'POST' }).catch(() => {}); Toast({ message: '已升级上报', theme: 'success' }); }
    catch { Toast({ message: '上报失败', theme: 'error' }); }
  };

  const handleAddComment = async () => {
    if (!newComment.trim() || !id) return;
    setSubmitting(true);
    try {
      await request(`/${id}/comments`, { method: 'POST', body: JSON.stringify({ content: newComment }) });
      setNewComment('');
      Toast({ message: '评论成功', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `评论失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  const startEdit = () => { if (!ticket) return; setEditForm({ title: ticket.title, description: ticket.description }); setEditing(true); };
  const saveEdit = async () => {
    if (!ticket) return;
    try {
      await request(`/${ticket.id}`, { method: 'PUT', body: JSON.stringify(editForm) });
      Toast({ message: '修改成功', theme: 'success' });
      setEditing(false);
      fetchDetail();
    } catch (err) {
      Toast({ message: `修改失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // 讨论：带工单上下文回到摇人 AI 对话
  const discuss = () => {
    if (!ticket) return;
    const ctx = { ticketId: ticket.id, title: ticket.title, description: ticket.description };
    setChatContext(ctx);
    goToTab('call', { chatContext: ctx });
    navigate('/app/call');
  };

  // 状态流转按钮
  const flowButtons = (): Array<{ label: string; status: string; theme: 'primary' | 'danger' | 'default' }> => {
    const btns: Array<{ label: string; status: string; theme: 'primary' | 'danger' | 'default' }> = [];
    if (!ticket) return btns;
    switch (ticket.status) {
      case 'new': btns.push({ label: '开始处理', status: 'in_progress', theme: 'primary' }); break;
      case 'in_progress': btns.push({ label: '标记解决', status: 'resolved', theme: 'primary' }); break;
      case 'resolved': btns.push({ label: '关闭工单', status: 'closed', theme: 'primary' }); break;
    }
    if (ticket.status !== 'new') btns.push({ label: '重新打开', status: 'new', theme: 'danger' });
    return btns;
  };

  if (loading) return <Loading text="加载中..." />;
  if (!ticket) return <div style={{ padding: 32, textAlign: 'center' }}>工单不存在</div>;

  return (
    <div className="ticket-detail-page" style={{ paddingBottom: 72 }}>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {ticket.attachments?.[0] && (
          <img src={ticket.attachments[0].url} alt="附件" className="ticket-detail__cover" />
        )}

        {/* 标题 + 基本信息卡片 */}
        <div className="detail-card">
          <div className="detail-card__meta">
            <Tag theme={statusTheme(ticket.status)}>{normalizeStatus(ticket.status)}</Tag>
            <span className="detail-card__type">{TICKET_TYPE_DISPLAY_MAP[ticket.ticket_type] || ticket.ticket_type || '其他'}</span>
            <span className="detail-card__id">#{ticket.id}</span>
          </div>
          <h2 className="detail-card__title">{ticket.title}</h2>
          <DetailRow label="优先级" value={ticket.priority} />
          {ticket.project_name && <DetailRow label="项目" value={ticket.project_name} />}
          {ticket.reporter_name && <DetailRow label="提交人" value={ticket.reporter_name} />}
          {ticket.assignee_name && <DetailRow label="处理人" value={ticket.assignee_name} />}
          <DetailRow label="创建" value={formatDateTime(ticket.created_at)} />
          <DetailRow label="更新" value={formatDateTime(ticket.updated_at)} />
        </div>

        {/* 问题描述（即"梗概"来源） */}
        <div className="detail-card">
          <h4 className="detail-card__h">问题描述</h4>
          <SafeHtml html={ticket.description || '<p style="color:#999">无描述</p>'} />
        </div>

        {/* 操作按钮 */}
        <div className="detail-actions">
          {flowButtons().map((b) => (
            <Button
              key={b.status}
              theme={b.theme}
              size="small"
              onClick={() => Dialog.confirm?.({ title: '确认操作', content: `确认要${b.label}吗？`, onConfirm: () => handleStatusChange(b.status) })}
            >{b.label}</Button>
          ))}
          <Button size="small" theme="default" onClick={handleUrge}>催办</Button>
          <Button size="small" theme="default" onClick={startEdit}>修改</Button>
          <Button size="small" theme="primary" onClick={discuss}>讨论</Button>
          <Button size="small" theme="danger" onClick={handleEscalate}>升级上报</Button>
        </div>

        {/* 评论 */}
        <div className="detail-card">
          <h4 className="detail-card__h">讨论评论（{ticket.comments?.length || 0}）</h4>
          {ticket.comments?.length ? ticket.comments.map((c) => (
            <div key={c.id} className="detail-comment">
              <div className="detail-comment__head">
                <strong>{c.author_name}</strong>
                <span>{formatTime(c.created_at)}</span>
              </div>
              <SafeHtml html={c.content} />
            </div>
          )) : <p style={{ color: '#999', textAlign: 'center', padding: 16 }}>暂无评论</p>}
        </div>
      </div>

      {/* 底部评论输入 */}
      <div className="detail-comment-bar">
        <Textarea value={newComment} onChange={(v) => setNewComment(String(v))} placeholder="添加评论..." autosize={{ minRows: 1, maxRows: 3 }} style={{ flex: 1 }} />
        <Button theme="primary" onClick={handleAddComment} loading={submitting} disabled={!newComment.trim()}>发送</Button>
      </div>

      {/* 修改工单弹层 */}
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
