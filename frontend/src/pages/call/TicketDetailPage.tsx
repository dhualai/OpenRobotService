// 摇人 · 历史工单详情页（U老师诊断生成的工单）
// 数据源：AI 模块 GET /api/ai/qa/ticket?session_id=...；操作：催办 / 上报（任务服务通知）
// 路由 /app/call/ticket/:id 中的 :id 即 AI 会话 session_id
import { useState, useEffect, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag, Popup } from 'tdesign-mobile-react';
import { NotificationIcon, UploadIcon, RollbackIcon } from 'tdesign-icons-react';
import { qaGetTicket } from '@/api/ai';
import { cancelTicket, urgeTicket, reportTicket, uploadCommentAttachment } from '@/api/ticket';
import {
  STATUS_DISPLAY_MAP,
  isTerminalTicketStatus,
  canUrgeTicket,
  canReportTicket,
  canCancelTicket,
} from '@/shared/constants/ticket';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import UserSelect from '@/shared/components/UserSelect';
import SafeHtml from '@/shared/components/SafeHtml';
import { useAuthStore } from '@/stores/auth';
import { formatDateTime } from '@/shared/utils/url';
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
  const [submittingComment, setSubmittingComment] = useState(false);
  const [aiSummary, setAiSummary] = useState('');
  const tempIdRef = useRef<string>(typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`);

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
            const taskDetail = await request<{ comments: Comment[]; metadata_info?: { ai_summary?: string } }>(`/${aiTicket.ticket_id}?load_comments=true`);
            setTicket((prev) => prev ? { ...prev, comments: taskDetail.comments || [] } : prev);
            setAiSummary(typeof taskDetail.metadata_info?.ai_summary === 'string' ? taskDetail.metadata_info.ai_summary : '');
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

  // 发送评论（附件上传 + POST /api/tasks/{ticket_id}/comments）；返回 true=成功（组件清空输入）
  const handleSendComment = async (text: string, files: File[]): Promise<boolean> => {
    if (!ticket?.ticket_id) {
      Toast({ message: '工单号缺失，无法评论', theme: 'warning' });
      return false;
    }
    setSubmittingComment(true);
    try {
      const tempId = tempIdRef.current;
      // 先逐个上传附件（temp_id 关联，后端登记到 comment_attachment_map）
      for (const f of files) {
        await uploadCommentAttachment(f, tempId);
      }
      const newComment = await request<Comment>(`/${ticket.ticket_id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true, attachments: files.length ? [tempId] : [] }),
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
      tempIdRef.current = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`;
      Toast({ message: '评论已添加', theme: 'success' });
      return true;
    } catch (err) {
      Toast({ message: `添加评论失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return false;
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

        {/* AI 讨论摘要（与系统任务共用 tasks 表 metadata_info.ai_summary）*/}
        {ticket?.ticket_id && (
          <div className="detail-card">
            <h4 className="detail-card__h">讨论摘要</h4>
            {aiSummary ? (
              <SafeHtml html={aiSummary} />
            ) : (
              <p style={{ color: '#999' }}>暂无摘要，U老师 将自动总结讨论进展</p>
            )}
          </div>
        )}

        <DiscussionPanel
          comments={ticket.comments || []}
          onSend={handleSendComment}
          sending={submittingComment}
          disabled={!ticket?.ticket_id}
          enableAttach
        />

        {/* 操作：已解决/已取消/已关闭（终态）三个按钮不显示，改为状态提示；
            新建/待处理可催办、撤回；处理中仅可上报；不可用按钮禁用 */}
        <div className="detail-actions">
          {isTerminalTicketStatus(ticket.status) ? (
            <p className="detail-actions__tip">
              工单{STATUS_DISPLAY_MAP[(ticket.status || '').trim().toLowerCase()] || ticket.status}，无需催办 / 上报 / 撤回
            </p>
          ) : (
            <div className="detail-actions__btns">
              <Button
                size="small" variant="outline" theme="default" icon={<NotificationIcon />}
                disabled={!canUrgeTicket(ticket.status) || acting}
                title={canUrgeTicket(ticket.status) ? undefined : '仅新建/待处理工单可催办'}
                onClick={() => openActionPopup('urge')}
              >催办</Button>
              <Button
                size="small" variant="outline" theme="default" icon={<UploadIcon />}
                disabled={!canReportTicket(ticket.status) || acting}
                title={canReportTicket(ticket.status) ? undefined : '仅处理中工单可上报'}
                onClick={() => openActionPopup('report')}
              >上报</Button>
              <Button
                size="small" variant="outline" theme="default" icon={<RollbackIcon />}
                disabled={!canCancelTicket(ticket.status) || acting}
                title={canCancelTicket(ticket.status) ? undefined : '仅新建/待处理工单可撤回'}
                onClick={handleCancel}
              >撤回</Button>
            </div>
          )}
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
