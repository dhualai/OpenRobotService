// 摇人 · 历史工单详情页（小U诊断生成的工单）
// 数据源：AI 模块 GET /api/ai/qa/ticket?session_id=...；操作：催办 / 上报（任务服务通知）
// 路由 /app/call/ticket/:id 中的 :id 即 AI 会话 session_id
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag, Popup } from 'tdesign-mobile-react';
import { NotificationIcon, UploadIcon, RollbackIcon } from 'tdesign-icons-react';
import { qaGetTicket } from '@/api/ai';
import { cancelTicket, urgeTicket, reportTicket, uploadCommentAttachment } from '@/api/ticket';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import UserSelect from '@/shared/components/UserSelect';
import { useAuthStore } from '@/stores/auth';
import { formatDateTime, formatTime } from '@/shared/utils/url';
import type { UserItem } from '@/api/users';

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
  const { username, name } = useAuthStore();

  const [ticket, setTicket] = useState<AiTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [commentText, setCommentText] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  const tempIdRef = useRef<string>(typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  // 催办/上报：先选用户再调接口
  const [actionType, setActionType] = useState<'urge' | 'report'>('urge');
  const [actionUser, setActionUser] = useState<UserItem | null>(null);
  const [showActionPopup, setShowActionPopup] = useState(false);
  const [acting, setActing] = useState(false);

  const openActionPopup = (type: 'urge' | 'report') => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActionType(type);
    setActionUser(null);
    setShowActionPopup(true);
  };

  const handleActionConfirm = async () => {
    if (!ticket?.ticket_id || !actionUser) { Toast({ message: '请选择通知用户', theme: 'warning' }); return; }
    setActing(true);
    try {
      if (actionType === 'urge') {
        await urgeTicket(ticket.ticket_id, actionUser.id);
        Toast({ message: '已催办，已通知处理人', theme: 'success' });
      } else {
        await reportTicket(ticket.ticket_id, actionUser.id);
        Toast({ message: '已上报，已通知上级', theme: 'success' });
      }
      setShowActionPopup(false);
    } catch (err) {
      Toast({ message: `${actionType === 'urge' ? '催办' : '上报'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setActing(false);
    }
  };

  // 撤回：将工单状态置为已取消（Canceled）
  const handleCancel = async () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法撤回', theme: 'warning' }); return; }
    try {
      await cancelTicket(ticket.ticket_id);
      Toast({ message: '已撤回，工单已取消', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `撤回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handleSelectFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) setPendingFiles((prev) => [...prev, ...files]);
    e.target.value = '';
  };
  const removeFile = (idx: number) => setPendingFiles((prev) => prev.filter((_, i) => i !== idx));

  const handleAddComment = async () => {
    if (!ticket?.ticket_id || (!commentText.trim() && pendingFiles.length === 0)) {
      Toast({ message: '请输入评论内容或选择附件', theme: 'warning' });
      return;
    }
    setSubmittingComment(true);
    try {
      const tempId = tempIdRef.current;
      // 先逐个上传附件（temp_id 关联，后端登记到 comment_attachment_map）
      for (const f of pendingFiles) {
        await uploadCommentAttachment(f, tempId);
      }
      const newComment = await request<Comment>(`/${ticket.ticket_id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: commentText.trim(), is_public: true, attachments: pendingFiles.length ? [tempId] : [] }),
      });
      const enrichedComment = {
        ...newComment,
        created_by_name: newComment.created_by_name || name || newComment.created_by || '未知用户',
        created_by: newComment.created_by || username,
      };
      setTicket((prev) => {
        if (!prev) return prev;
        const updatedComments = prev.comments ? [...prev.comments, enrichedComment] : [enrichedComment];
        return { ...prev, comments: updatedComments };
      });
      setCommentText('');
      setPendingFiles([]);
      tempIdRef.current = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`;
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
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/call', { state: { showHistory: true } })} />
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
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/call', { state: { showHistory: true } })} />
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
          <DetailRow label="创建时间" value={ticket.created_at ? formatDateTime(typeof ticket.created_at === 'number' ? new Date(ticket.created_at * 1000).toISOString() : String(ticket.created_at)) : ''} />
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
            <h4 className="detail-card__h">🤖 小U 诊断</h4>
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
                const isCurrentUser = (c.created_by?.toLowerCase() === username?.toLowerCase()) ||
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
            {pendingFiles.length > 0 && (
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
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddComment(); }}
              placeholder={ticket?.ticket_id ? '参与讨论…' : '工单号缺失，无法评论'}
              disabled={submittingComment || !ticket?.ticket_id}
            />
            <button type="button" className="detail-chat-attach" onClick={() => fileInputRef.current?.click()} disabled={submittingComment || !ticket?.ticket_id} aria-label="上传图片或文件">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
            </button>
            <Button
              size="small"
              theme="primary"
              onClick={handleAddComment}
              disabled={submittingComment || (!commentText.trim() && pendingFiles.length === 0) || !ticket?.ticket_id}
            >
              {submittingComment ? '发送中' : '发送'}
            </Button>
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleSelectFile} />
          </div>
        </div>

        {/* 操作 */}
        <div className="detail-actions">
          <div className="detail-actions__btns">
            <Button size="small" variant="outline" theme="default" icon={<NotificationIcon />} onClick={() => openActionPopup('urge')}>催办</Button>
            <Button size="small" variant="outline" theme="default" icon={<UploadIcon />} onClick={() => openActionPopup('report')}>上报</Button>
            <Button size="small" variant="outline" theme="default" icon={<RollbackIcon />} onClick={handleCancel}>撤回</Button>
          </div>
        </div>
      </div>

      {/* 催办/上报 用户选择弹窗 */}
      <Popup visible={showActionPopup} onClose={() => setShowActionPopup(false)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <h4 className="conv-dialog__title">{actionType === 'urge' ? '催办 — 选择通知用户' : '上报 — 选择通知用户'}</h4>
          <div style={{ marginBottom: 16 }}>
            <UserSelect
              value={actionUser?.id ?? null}
              onChange={(u) => setActionUser(u)}
              placeholder="选择通知对象"
              title="选择通知用户"
            />
          </div>
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setShowActionPopup(false)}>取消</Button>
            <Button block theme="primary" loading={acting} onClick={handleActionConfirm}>确定</Button>
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
