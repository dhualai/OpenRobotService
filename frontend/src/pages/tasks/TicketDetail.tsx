// 任务详情页 - 从 HelpDesk TicketDetail 迁移（56KB 原文件，保留核心功能）
import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import { normalizeStatus } from '@/shared/constants/ticket';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface Comment {
  id: string;
  content: string;
  author_name: string;
  created_at: string;
}

interface TicketDetailData {
  id: string;
  title: string;
  description: string;
  status: string;
  priority: string;
  ticket_type: string;
  project_name?: string;
  assignee_name?: string;
  reporter_name?: string;
  created_at: string;
  updated_at: string;
  comments?: Comment[];
}

export default function TicketDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [ticket, setTicket] = useState<TicketDetailData | null>(null);
  const [loading, setLoading] = useState(true);
  const [newComment, setNewComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const fetchDetail = async () => {
    if (!id) return;
    setLoading(true);
    try {
      const data = await request<TicketDetailData>(`/${id}?load_comments=true`);
      setTicket(data);
    } catch (err) {
      Toast({ message: `加载失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDetail();
  }, [id]);

  const handleStatusChange = async (newStatus: string) => {
    if (!id) return;
    try {
      await request(`/${id}/status?status=${newStatus}`, {
        method: 'PATCH',
      });
      Toast({ message: '状态更新成功', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `更新失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    }
  };

  const handleAddComment = async () => {
    if (!newComment.trim() || !id) return;
    setSubmitting(true);
    try {
      await request(`/${id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: newComment }),
      });
      setNewComment('');
      Toast({ message: '评论成功', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `评论失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <Loading text="加载中..." />;
  if (!ticket) return <div style={{ padding: 32, textAlign: 'center' }}>工单不存在</div>;

  const actionButtons = () => {
    const buttons: Array<{ label: string; status: string; theme: 'primary' | 'danger' | 'default' | 'light' }> = [];
    switch (ticket.status) {
      case 'new':
        buttons.push({ label: '开始处理', status: 'in_progress', theme: 'primary' });
        break;
      case 'in_progress':
        buttons.push({ label: '标记解决', status: 'resolved', theme: 'primary' });
        break;
      case 'resolved':
        buttons.push({ label: '关闭工单', status: 'closed', theme: 'primary' });
        break;
    }
    if (ticket.status !== 'new') {
      buttons.push({ label: '重新打开', status: 'new', theme: 'danger' });
    }
    return buttons;
  };

  return (
    <div style={{ paddingBottom: 80 }}>
      <Navbar title="任务详情" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {/* 工单标题 */}
        <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>{ticket.title}</h2>

        {/* 基本信息卡片 */}
        <div style={{ background: '#fff', borderRadius: 8, padding: 16, marginBottom: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
          <DetailRow label="状态" value={normalizeStatus(ticket.status)} />
          <DetailRow label="优先级" value={ticket.priority} />
          <DetailRow label="类型" value={ticket.ticket_type} />
          {ticket.project_name && <DetailRow label="项目" value={ticket.project_name} />}
          {ticket.reporter_name && <DetailRow label="提交人" value={ticket.reporter_name} />}
          {ticket.assignee_name && <DetailRow label="处理人" value={ticket.assignee_name} />}
          <DetailRow label="创建时间" value={formatDateTime(ticket.created_at)} />
          <DetailRow label="更新时间" value={formatDateTime(ticket.updated_at)} />
        </div>

        {/* 问题描述 */}
        {ticket.description && (
          <div style={{ background: '#fff', borderRadius: 8, padding: 16, marginBottom: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
            <h4 style={{ marginBottom: 8, fontSize: 15, fontWeight: 500 }}>问题描述</h4>
            <SafeHtml html={ticket.description} />
          </div>
        )}

        {/* 操作按钮 */}
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          {actionButtons().map((btn) => (
            <Button
              key={btn.status}
              theme={btn.theme}
              size="small"
              onClick={() => {
                Dialog.confirm?.({
                  title: '确认操作',
                  content: `确认要${btn.label}吗？`,
                  onConfirm: () => handleStatusChange(btn.status),
                });
              }}
            >
              {btn.label}
            </Button>
          ))}
        </div>

        {/* 评论区域 */}
        <div style={{ background: '#fff', borderRadius: 8, padding: 16, marginBottom: 16, boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
          <h4 style={{ marginBottom: 12, fontSize: 15, fontWeight: 500 }}>
            评论 ({ticket.comments?.length || 0})
          </h4>
          {ticket.comments?.map((comment) => (
            <div key={comment.id} style={{ padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <strong style={{ fontSize: 13 }}>{comment.author_name}</strong>
                <span style={{ fontSize: 12, color: '#999' }}>{formatTime(comment.created_at)}</span>
              </div>
              <SafeHtml html={comment.content} />
            </div>
          ))}
          {(!ticket.comments || ticket.comments.length === 0) && (
            <p style={{ color: '#999', textAlign: 'center', padding: 20 }}>暂无评论</p>
          )}
        </div>

        {/* 评论输入 */}
        <div style={{
          position: 'fixed', bottom: 0, left: 0, right: 0,
          padding: '8px 16px 16px', background: '#fff', borderTop: '1px solid #eee',
          display: 'flex', gap: 8,
        }}>
          <Textarea
            value={newComment}
            onChange={(v) => setNewComment(String(v))}
            placeholder="添加评论..."
            autosize={{ minRows: 1, maxRows: 3 }}
            style={{ flex: 1 }}
          />
          <Button theme="primary" onClick={handleAddComment} loading={submitting} disabled={!newComment.trim()}>
            发送
          </Button>
        </div>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', padding: '8px 0', borderBottom: '1px solid #f5f5f5' }}>
      <span style={{ color: '#999', width: 80, flexShrink: 0 }}>{label}</span>
      <span style={{ flex: 1 }}>{value}</span>
    </div>
  );
}
