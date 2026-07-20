// 摇人 · 历史工单详情页（AI 诊断生成的工单）
// 数据源：AI 模块 GET /api/ai/qa/ticket?session_id=...；操作：POST /api/ai/qa/ticket/ack（确认派单）
// 路由 /app/call/ticket/:id 中的 :id 即 AI 会话 session_id
import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag } from 'tdesign-mobile-react';
import { qaGetTicket, qaTicketAck } from '@/api/ai';
import { useWorkbenchStore } from '@/stores/workbench';
import { formatDateTime } from '@/shared/utils/url';

interface AiDiagnosis {
  problem_summary?: string;
  hypotheses?: string[];
  ruled_out?: string[];
  collected_info?: Record<string, unknown>;
  rounds?: number;
}
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
  const refreshTasks = useWorkbenchStore((s) => s.refreshTasks);

  const [ticket, setTicket] = useState<AiTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [acking, setAcking] = useState(false);
  const [msg, setMsg] = useState('');

  const fetchDetail = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const res = await qaGetTicket(sessionId);
      if (res?.code === 0 && res.data) setTicket(res.data as AiTicket);
      else setMsg(res?.message || '该会话尚未生成工单');
    } catch (err) {
      setMsg(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  const handleAck = async () => {
    if (!sessionId) return;
    setAcking(true);
    try {
      const res = await qaTicketAck(sessionId, '', 'dispatched');
      if (res?.code === 0) {
        Toast({ message: '已确认派单', theme: 'success' });
        refreshTasks();
        navigate(-1);
      } else {
        Toast({ message: res?.message || '派单失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `派单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setAcking(false);
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

        {/* 操作 */}
        <div className="detail-actions">
          <Button theme="primary" size="small" loading={acking} onClick={handleAck}>确认派单</Button>
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
