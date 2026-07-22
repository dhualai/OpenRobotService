import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { fetchWithAuth } from '@/api/ai';
import SafeHtml from '@/shared/components/SafeHtml';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime, formatTime } from '@/shared/utils/url';

interface Attachment { id: string; url: string; }
interface Comment {
  id: string; content: string; author_name: string; created_by?: string;
  created_at: string;
}
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; assignee_name?: string; reporter_name?: string;
  contact?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; comments?: Comment[];
}

const AI_NAME = 'AI任务助手';

export default function TaskDetailPage() {
  const { id: detailId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const { refreshTasks, setChatContext, goToTab } = useWorkbenchStore();

  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '' });
  const [escalateUser, setEscalateUser] = useState<UserItem | null>(null);

  // AI 分析
  const [diagnosing, setDiagnosing] = useState(false);
  const [reportHtml, setReportHtml] = useState('');
  const [reportVisible, setReportVisible] = useState(false);

  // 讨论区
  const [commentText, setCommentText] = useState('');
  const [askingAI, setAskingAI] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [aiSummary, setAiSummary] = useState('');

  // ── 工单加载 ──
  const loadDetail = () => {
    if (!detailId) return;
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`)
      .then((t) => {
        setDetail(t);
        // 从评论中取最新一条 AI 摘要
        const aiComments = (t.comments || []).filter((c) => c.author_name === AI_NAME || c.created_by === AI_NAME);
        const lastSummary = aiComments.find((c) => c.content?.startsWith('📝 讨论摘要'));
        if (lastSummary) setAiSummary(lastSummary.content.replace('📝 讨论摘要\n\n', ''));
      })
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  };

  useEffect(() => { loadDetail(); }, [detailId]);

  // ── AI 分析 → 调 POST /api/ai/task/diagnose ──
  const handleAIAnalyze = async () => {
    if (!detailId || diagnosing) return;
    setDiagnosing(true);
    try {
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/diagnose`, {
        method: 'POST',
        body: JSON.stringify({ task_id: detailId }),
      });
      const data = await res.json();
      if (data.code === 0) {
        const d = data.data;
        const html = `
<div style="line-height:1.8">
  <h3>📋 诊断报告</h3>
  <h4>根因分析</h4>
  <p>${d.root_cause_analysis || '暂未得出明确根因'}</p>
  ${(d.suggested_actions || []).length ? `<h4>建议步骤</h4><ol>${d.suggested_actions.map((a: string) => `<li>${a}</li>`).join('')}</ol>` : ''}
  ${(d.references || []).length ? `<h4>参考来源</h4><ul>${d.references.map((r: string) => `<li>${r}</li>`).join('')}</ul>` : ''}
  <p style="margin-top:12px;color:#999">置信度：${Math.round((d.confidence || 0) * 100)}%</p>
</div>`;
        setReportHtml(html);
        setReportVisible(true);
      } else {
        Toast({ message: data.message || '分析失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `分析失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDiagnosing(false);
    }
  };

  // ── @AI 讨论 → 调 POST /api/ai/task/discuss ──
  const handleAIDiscuss = async () => {
    if (!detailId || askingAI || !commentText.trim()) return;
    setAskingAI(true);
    try {
      const recentComments = (detail?.comments || []).slice(-10).map((c) => ({
        author: c.author_name || c.created_by || '?',
        content: c.content,
      }));
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/discuss`, {
        method: 'POST',
        body: JSON.stringify({ task_id: detailId, query: commentText.trim(), context: { recent_comments: recentComments } }),
      });
      const data = await res.json();
      if (data.code === 0) {
        Toast({ message: 'AI 已回复', theme: 'success' });
        setCommentText('');
        loadDetail(); // 刷新评论列表
      } else {
        Toast({ message: data.message || '提问失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `提问失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setAskingAI(false);
    }
  };

  // ── 普通评论发送 ──
  const handleSendComment = async () => {
    if (!detailId || !commentText.trim()) return;
    try {
      await request(`/${detailId}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: commentText.trim() }),
      });
      Toast({ message: '已发送', theme: 'success' });
      setCommentText('');
      loadDetail();
    } catch (err) {
      Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // ── AI 讨论摘要 → 调 POST /api/ai/task/summarize ──
  const handleAISummarize = async () => {
    if (!detailId || summarizing) return;
    setSummarizing(true);
    try {
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/summarize`, {
        method: 'POST',
        body: JSON.stringify({ task_id: detailId }),
      });
      const data = await res.json();
      if (data.code === 0 && !data.data.skipped) {
        setAiSummary(data.data.summary);
        Toast({ message: '摘要已生成', theme: 'success' });
        loadDetail();
      } else {
        Toast({ message: '暂无足够新讨论可总结', theme: 'warning' });
      }
    } catch (err) {
      Toast({ message: `摘要失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSummarizing(false);
    }
  };

  // ── 升级上报 ──
  const handleEscalate = async (t: Ticket) => {
    if (!escalateUser) { Toast({ message: '请先选择升级对象', theme: 'warning' }); return; }
    const target = escalateUser.name || escalateUser.username;
    try {
      await request('/', {
        method: 'POST',
        body: JSON.stringify({
          title: `【升级→${target}】${t.title}`,
          description: `原工单 #${t.id}「${t.title}」申请升级给 ${target}。\n\n原始描述：${t.description || '无'}`,
          ticket_type: t.ticket_type || 'problem', priority: 'urgent',
          related_resource_id: Number(t.id), assigned_to: escalateUser.id,
        }),
      });
      Toast({ message: `已升级，已指派给 ${target}`, theme: 'success' });
      setEscalateUser(null);
    } catch (err) {
      Toast({ message: `升级失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const copyId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => Toast({ message: '已复制工单号', theme: 'success' }));
  };

  // ── 状态文案 ──
  const statusLabel: Record<string, string> = {
    new: '新建', in_progress: '进行中', pending: '待处理', resolved: '已解决', closed: '已关闭',
  };
  const isResolved = detail?.status === 'resolved' || detail?.status === 'closed';
  const isOpen = !detail || !isResolved;

  // ── 渲染 ──
  if (detailLoading) return <Loading text="加载中…" />;
  if (!detail) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/tasks')} />
      <div style={{ padding: 32, textAlign: 'center', color: '#999', marginTop: 56 }}>工单不存在</div>
    </div>
  );

  return (
    <div className="task-detail-page" style={{ paddingBottom: 140 }}>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => navigate('/tasks')} />

      <div className="page-container" style={{ paddingTop: 56 }}>
        {/* 顶部工单信息 */}
        {detail.attachments?.[0] && (
          <img src={detail.attachments[0].url} alt="附件" className="ticket-detail__cover" />
        )}

        <div className="detail-card">
          <div className="detail-card__meta">
            <Tag theme="primary">{TICKET_TYPE_DISPLAY_MAP[detail.ticket_type] || detail.ticket_type || '其他'}</Tag>
            <span className="detail-card__id" onClick={() => copyId(detail.id)}>#{detail.id}</span>
            <Tag theme={isResolved ? 'success' : 'warning'} size="small">
              {statusLabel[detail.status] || detail.status}
            </Tag>
          </div>
          <h2 className="detail-card__title">{detail.title}</h2>

          {/* [帮我分析] 按钮 — 仅进行中/待处理时显示 */}
          {isOpen && (
            <div style={{ marginTop: 12 }}>
              <Button size="small" theme="primary" onClick={handleAIAnalyze} loading={diagnosing}>
                {diagnosing ? '分析中…' : '🤖 帮我分析'}
              </Button>
            </div>
          )}
        </div>

        {/* 问题描述 */}
        <div className="detail-card">
          <h4 className="detail-card__h">问题描述</h4>
          <SafeHtml html={detail.description || '<p style="color:#999">无描述</p>'} />
        </div>

        {/* AI 讨论摘要 + [生成摘要] 按钮 */}
        <div className="detail-card">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h4 className="detail-card__h">🤖 AI 讨论摘要</h4>
            {isOpen && (
              <Button size="small" theme="default" onClick={handleAISummarize} loading={summarizing}>
                生成摘要
              </Button>
            )}
          </div>
          {aiSummary ? (
            <SafeHtml html={aiSummary} />
          ) : (
            <p style={{ color: '#999' }}>暂无摘要，点击"生成摘要"让 AI 自动总结讨论进展</p>
          )}
        </div>

        {/* 联系方式 */}
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

        {/* 讨论区 */}
        {detail.comments && detail.comments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">讨论（{detail.comments.length}）</h4>
            {detail.comments.map((c) => (
              <div key={c.id} className="ticket-comment">
                <div className="ticket-comment__head">
                  <strong>{c.author_name || c.created_by || '?'}</strong>
                  <span>{formatTime(c.created_at)}</span>
                </div>
                <SafeHtml html={c.content} />
              </div>
            ))}
          </div>
        )}

        <div className="detail-card">
          <DetailRow label="创建时间" value={formatDateTime(detail.created_at)} />
          <DetailRow label="更新时间" value={formatDateTime(detail.updated_at)} />
        </div>
      </div>

      {/* 底部固定栏：评论输入 + 操作按钮 */}
      <div className="task-detail__bottom-bar">
        {isOpen && (
          <div className="task-detail__comment-row">
            <Textarea
              value={commentText}
              onChange={(v) => setCommentText(String(v))}
              placeholder="输入评论…"
              autosize={{ minRows: 1, maxRows: 4 }}
              style={{ flex: 1 }}
            />
            <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
              <Button size="small" theme="default" onClick={handleSendComment} disabled={!commentText.trim()}>
                发送
              </Button>
              <Button size="small" theme="primary" onClick={handleAIDiscuss} loading={askingAI} disabled={!commentText.trim()}>
                @AI
              </Button>
            </div>
          </div>
        )}

        <div className="detail-actions">
          <UserSelect value={escalateUser?.id ?? null} onChange={setEscalateUser} />
          <div className="detail-actions__btns">
            {isOpen && (
              <>
                <Button size="small" theme="primary" onClick={() => setChatContext({ ticketId: detail.id, title: detail.title, description: detail.description }); goToTab('call', { chatContext: { ticketId: detail.id, title: detail.title, description: detail.description } }); navigate('/call');}>
                  讨论
                </Button>
                <Button size="small" theme="danger" onClick={() => handleEscalate(detail)}>升级上报</Button>
              </>
            )}
            {isResolved && (
              <Button size="small" theme="primary" onClick={handleAIAnalyze} loading={diagnosing}>
                🤖 重新分析
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* AI 诊断报告弹窗 */}
      <Dialog
        visible={reportVisible}
        title="AI 诊断报告"
        content={reportHtml}
        confirmBtn="关闭"
        onConfirm={() => setReportVisible(false)}
      />
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
