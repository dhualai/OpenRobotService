// 摇人 · 历史工单详情页（AI 诊断生成的工单）
// 数据源：AI 模块 GET /api/ai/qa/ticket?session_id=...；操作：催办 / 上报（任务服务通知）
// 路由 /app/call/ticket/:id 中的 :id 即 AI 会话 session_id
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag } from 'tdesign-mobile-react';
import { qaGetTicket } from '@/api/ai';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import { useAuthStore } from '@/stores/auth';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface AiDiagnosis {
  problem_summary?: string;
  hypotheses?: string[];
  ruled_out?: string[];
  collected_info?: Record<string, unknown>;
  rounds?: number;
}
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; }
interface AiTicket {
  ticket_id?: string;
  session_id: string;
  type?: string;
  title?: string;
  description?: string;
  priority?: string;
  status?: string;
  contact?: string;
  created_at?: number;
  diagnosis?: AiDiagnosis;
  attachments?: Array<Record<string, unknown>>;
  comments?: Comment[];
  // 类型专属
  location?: string; robot_type?: string; fault_code?: string; special_notes?: string;
  steps_to_reproduce?: string; expected_result?: string; actual_result?: string; severity?: string; version?: string;
  scenario?: string; expected_effect?: string; source?: string;
  support_type?: string; preferred_response?: string;
}

const TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};

export default function TicketDetailPage() {
  const { id: sessionId = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const currentUsername = useAuthStore((s) => s.username);

  const [ticket, setTicket] = useState<AiTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [commentText, setCommentText] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const chatMessagesRef = useRef<HTMLDivElement>(null);

  const fetchDetail = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await qaGetTicket(sessionId);
      if (res?.code === 0 && res.data) {
        const aiTicket = res.data as AiTicket;
        setTicket(aiTicket);
        if (aiTicket.ticket_id) {
          try {
            const taskDetail = await request<{ comments: Comment[] }>(`/${aiTicket.ticket_id}?load_comments=true`);
            setTicket((prev) => prev ? { ...prev, comments: taskDetail.comments || [] } : prev);
          } catch { /* 评论加载失败不阻塞主流程 */ }
        }
      } else {
        setMsg(res?.message || '该会话尚未生成工单');
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  useEffect(() => {
    if (chatMessagesRef.current) {
      chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight;
    }
  }, [ticket?.comments?.length]);

  const handleUrge = async () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法催办', theme: 'warning' }); return; }
    try {
      // 真实接口：后端任务服务催办通知（后端要求工单已设置截止时间且已分配处理人）
      await request('/cuiban-notification', {
        method: 'POST',
        body: JSON.stringify({ ticket_id: Number(ticket.ticket_id), notify_type: 1 }),
      });
      Toast({ message: '已催办，已通知处理人', theme: 'success' });
    } catch (err) {
      Toast({ message: `催办失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // 上报：催办通知功能，上报给上级/管理员（to_admin=true 区别于催办处理人）
  const handleReport = async () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法上报', theme: 'warning' }); return; }
    try {
      await request('/cuiban-notification', {
        method: 'POST',
        body: JSON.stringify({ ticket_id: Number(ticket.ticket_id), notify_type: 1, to_admin: true }),
      });
      Toast({ message: '已上报，已通知上级', theme: 'success' });
    } catch (err) {
      Toast({ message: `上报失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // 升级、讨论、确认派单按钮已按需求移除

  const handleAddComment = async () => {
    if (!ticket?.ticket_id || !commentText.trim()) {
      Toast({ message: '请输入评论内容', theme: 'warning' });
      return;
    }
    setSubmittingComment(true);
    try {
      const newComment = await request<Comment>(`/${ticket.ticket_id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: commentText.trim(), is_public: true }),
      });
      setTicket((prev) => {
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

  if (loading) return <Loading text="加载中..." />;
  if (!ticket) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div style={{ padding: 32, textAlign: 'center', color: '#999', marginTop: 56 }}>{msg || '工单不存在'}</div>
    </div>
  );

  const dx = ticket.diagnosis;
  const isProblem = ticket.type === 'problem';
  const isBug = ticket.type === 'bug';
  const isFeature = ticket.type === 'feature';
  const isSupport = ticket.type === 'support';

  return (
    <div className="ticket-detail-page" style={{ paddingBottom: 72 }}>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {/* 标题 + 基本信息 */}
        <div className="detail-card">
          <div className="detail-card__meta">
            {ticket.type && <Tag theme="primary">{TYPE_LABEL[ticket.type] || ticket.type}</Tag>}
            {ticket.priority && <Tag theme="warning">{ticket.priority}</Tag>}
            <span className="detail-card__type">{ticket.status || 'pending_dispatch'}</span>
            <span className="detail-card__id">{ticket.ticket_id || ''}</span>
          </div>
          <h2 className="detail-card__title">{ticket.title || '(无标题)'}</h2>
          {ticket.contact && <DetailRow label="联系人" value={ticket.contact} />}
          <DetailRow label="创建时间" value={ticket.created_at ? formatDateTime(new Date(ticket.created_at * 1000).toISOString()) : ''} />
        </div>

        {/* 问题描述 */}
        {ticket.description && (
          <div className="detail-card">
            <h4 className="detail-card__h">问题描述</h4>
            <div style={{ whiteSpace: 'pre-wrap', color: '#333', fontSize: 14, lineHeight: 1.7 }}>{ticket.description}</div>
          </div>
        )}

        {/* 诊断过程 */}
        {dx && (
          <div className="detail-card">
            <h4 className="detail-card__h">🤖 AI 诊断</h4>
            {dx.problem_summary && <DetailRow label="概述" value={dx.problem_summary} />}
            {dx.hypotheses?.length ? <DetailRow label="推测原因" value={dx.hypotheses.join('、')} /> : null}
            {dx.ruled_out?.length ? <DetailRow label="已排除" value={dx.ruled_out.join('、')} /> : null}
            {dx.rounds != null && <DetailRow label="诊断轮数" value={String(dx.rounds)} />}
          </div>
        )}

        {/* 类型专属字段 */}
        {(isProblem || isBug || isFeature || isSupport) && (
          <div className="detail-card">
            <h4 className="detail-card__h">补充信息</h4>
            {isProblem && (
              <>
                {ticket.location && <DetailRow label="位置" value={ticket.location} />}
                {ticket.robot_type && <DetailRow label="机器人型号" value={ticket.robot_type} />}
                {ticket.fault_code && <DetailRow label="故障码" value={ticket.fault_code} />}
                {ticket.special_notes && <DetailRow label="特殊说明" value={ticket.special_notes} />}
              </>
            )}
            {isBug && (
              <>
                {ticket.severity && <DetailRow label="严重程度" value={ticket.severity} />}
                {ticket.version && <DetailRow label="版本" value={ticket.version} />}
                {ticket.steps_to_reproduce && <DetailRow label="复现步骤" value={ticket.steps_to_reproduce} />}
                {ticket.expected_result && <DetailRow label="预期结果" value={ticket.expected_result} />}
                {ticket.actual_result && <DetailRow label="实际结果" value={ticket.actual_result} />}
              </>
            )}
            {isFeature && (
              <>
                {ticket.scenario && <DetailRow label="场景" value={ticket.scenario} />}
                {ticket.expected_effect && <DetailRow label="期望效果" value={ticket.expected_effect} />}
                {ticket.source && <DetailRow label="来源" value={ticket.source} />}
              </>
            )}
            {isSupport && (
              <>
                {ticket.support_type && <DetailRow label="支持类型" value={ticket.support_type} />}
                {ticket.preferred_response && <DetailRow label="期望响应" value={ticket.preferred_response} />}
              </>
            )}
          </div>
        )}

        <div className="detail-card detail-chat-container">
          <h4 className="detail-card__h">讨论（{ticket.comments?.length || 0}）</h4>
          <div className="detail-chat-messages" ref={chatMessagesRef}>
            {ticket.comments && ticket.comments.length > 0 ? (
              ticket.comments.map((c) => {
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
              placeholder={ticket?.ticket_id ? '直接评论或者 @AI 进行讨论。' : '工单号缺失，无法评论'}
              disabled={submittingComment || !ticket?.ticket_id}
            />
            <Button
              size="small"
              theme="primary"
              onClick={handleAddComment}
              disabled={submittingComment || !commentText.trim() || !ticket?.ticket_id}
            >
              {submittingComment ? '发送中' : '发送'}
            </Button>
          </div>
        </div>

        {/* 操作 */}
        <div className="detail-actions">
          <div className="detail-actions__btns">
            <Button size="small" theme="default" onClick={handleUrge}>一键催办</Button>
            <Button size="small" theme="light" onClick={handleReport}>上报</Button>
          </div>
        </div>
      </div>
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
