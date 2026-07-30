import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { TICKET_TYPE_DISPLAY_MAP, STATUS_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTime } from '@/shared/utils/url';
import { fetchWithAuth } from '@/api/ai';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const STATUS_COLOR_MAP: Record<string, string> = {
  new: '#0052d9',
  in_progress: '#2ba471',
  pending: '#e37318',
  paused: '#e37318',
  resolved: '#00a870',
  closed: '#999999',
  canceled: '#d54941',
  cancelled: '#d54941',
};

const getStatusColor = (status: string): string => {
  const key = (status || '').toLowerCase();
  return STATUS_COLOR_MAP[key] || '#666666';
};

const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

const getFileIcon = (filename?: string): string => {
  const ext = (filename || '').split('.').pop()?.toLowerCase() || '';
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'];
  const docExts = ['pdf', 'doc', 'docx', 'txt', 'md'];
  const sheetExts = ['xls', 'xlsx', 'csv'];
  const codeExts = ['json', 'js', 'ts', 'py', 'html', 'css'];
  const archiveExts = ['zip', 'rar', '7z', 'tar', 'gz'];
  if (imageExts.includes(ext)) return '🖼️';
  if (docExts.includes(ext)) return '📄';
  if (sheetExts.includes(ext)) return '📊';
  if (codeExts.includes(ext)) return '💻';
  if (archiveExts.includes(ext)) return '📦';
  return '📎';
};

const isImageFile = (filename?: string): boolean => {
  const ext = (filename || '').split('.').pop()?.toLowerCase() || '';
  return ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'].includes(ext);
};

interface MinioPathInfo { bucket: string; objectKey: string; }

const parseMinioPath = (rawPath: string): MinioPathInfo | null => {
  try {
    const url = new URL(rawPath);
    const parts = decodeURIComponent(url.pathname).replace(/^\//, '').split('/');
    if (parts.length < 2) return null;
    const bucket = parts[0];
    const objectKey = parts.slice(1).join('/');
    if (!bucket || !objectKey) return null;
    return { bucket, objectKey };
  } catch {
    const idx = rawPath.indexOf('://');
    if (idx === -1) return null;
    const pathPart = rawPath.slice(rawPath.indexOf('/', idx + 3));
    const cleaned = decodeURIComponent(pathPart.replace(/^\//, ''));
    const slashIdx = cleaned.indexOf('/');
    if (slashIdx === -1) return null;
    return { bucket: cleaned.slice(0, slashIdx), objectKey: cleaned.slice(slashIdx + 1) };
  }
};

interface Attachment { path: string; size?: number; filename?: string; url?: string; id?: string; }
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  reporter_name?: string; assignee_name?: string;
  contact?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; metadata_info?: Record<string, unknown>; comments?: Comment[];
}

const AI_NAME = 'U老师';

export default function TaskDetailPage() {
  const { id: detailId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

  const { refreshTasks } = useWorkbenchStore();
  const { username, name } = useAuthStore();

  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '' });
  const [escalateUser, setEscalateUser] = useState<UserItem | null>(null);
  const [showEscalatePopup, setShowEscalatePopup] = useState(false);
  const [resumeUser, setResumeUser] = useState<UserItem | null>(null);
  const [showResumePopup, setShowResumePopup] = useState(false);
  const [submittingComment, setSubmittingComment] = useState(false);
  const [askingAI, setAskingAI] = useState(false);

  // U老师 诊断
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosisReport, setDiagnosisReport] = useState('');  // raw Markdown
  const [reportVisible, setReportVisible] = useState(false);

  // AI 摘要（后端定时写入评论，前端从评论提取展示）
  const [aiSummary, setAiSummary] = useState('');

  useEffect(() => {
    if (!detailId) { setDetail(null); return; }
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true })
      .then((t) => {
        setDetail(t);
        // 摘要存 metadata_info.ai_summary（不混入讨论区）
        const meta = t.metadata_info || {};
        setAiSummary(typeof meta.ai_summary === 'string' ? meta.ai_summary as string : '');
      })
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  }, [detailId]);

  const getActionButtons = () => {
    const status = detail?.status?.toLowerCase();
    if (!status) return [];

    const isClosed = status === 'closed';
    const isCanceled = status === 'canceled' || status === 'cancelled';
    if (isClosed || isCanceled) return [];

    const currentUsername = username;
    const currentName = name || username;

    const isAssignee = !!(
      (detail?.assigned_to && detail.assigned_to === currentUsername) ||
      (detail?.assignee_name && (detail.assignee_name === currentUsername || detail.assignee_name === currentName)) ||
      (detail?.assigned_to_name && (detail.assigned_to_name === currentUsername || detail.assigned_to_name === currentName))
    );

    const isReporter = !!(
      (detail?.created_by && detail.created_by === currentUsername) ||
      (detail?.reporter_name && (detail.reporter_name === currentUsername || detail.reporter_name === currentName)) ||
      (detail?.created_by_name && (detail.created_by_name === currentUsername || detail.created_by_name === currentName))
    );

    const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
    if (assigneeOnlyStatuses.includes(status) && !isAssignee) return [];

    if (status === 'resolved' && !isReporter) return [];

    const actions: Record<string, { label: string; nextStatus: string; theme: string; actionType?: string; customStyle?: Record<string, string> }[]> = {
      new: [{ label: '开始处理', nextStatus: 'in_progress', theme: 'primary' }],
      in_progress: [
        { label: '暂停任务', nextStatus: 'pending', theme: 'warning', customStyle: { backgroundColor: '#faad14', color: '#fff', borderRadius: '10px', border: 'none' } },
        { label: '处理完成', nextStatus: 'resolved', theme: 'success', customStyle: { backgroundColor: '#52c41a', color: '#fff', borderRadius: '10px', border: 'none' } },
      ],
      pending: [{ label: '继续处理', nextStatus: 'in_progress', theme: 'primary', actionType: 'resume' }],
      resolved: [{ label: '确认关闭', nextStatus: 'closed', theme: 'default' }],
      canceled: [{ label: '重新打开', nextStatus: 'new', theme: 'primary' }],
    };

    return actions[status] || [];
  };

  const handleStatusChange = async (action: { nextStatus: string }) => {
    if (!detail) return;
    
    try {
      const updated = await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: action.nextStatus }),
      });
      setDetail(updated);
      refreshTasks();
      Toast({ message: `状态已更新为${STATUS_DISPLAY_MAP[action.nextStatus] || action.nextStatus}`, theme: 'success' });
    } catch (err) {
      Toast({ message: `状态更新失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handleResume = async () => {
    if (!detail) return;
    
    try {
      await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'in_progress' }),
      });

      if (resumeUser) {
        await request(`/${detail.id}`, {
          method: 'PUT',
          body: JSON.stringify({ assigned_to: resumeUser.id }),
        });
      }

      const refreshed = await request<Ticket>(`/${detail.id}?load_comments=true`, { skipCache: true });
      setDetail(refreshed);
      refreshTasks();
      
      const target = resumeUser?.name || resumeUser?.username || '原处理人';
      Toast({ message: `已继续处理，处理人${resumeUser ? `变更为 ${target}` : '保持不变'}`, theme: 'success' });
      setResumeUser(null);
      setShowResumePopup(false);
    } catch (err) {
      Toast({ message: `继续处理失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handleEscalate = async (t: Ticket) => {
    if (!escalateUser) {
      Toast({ message: '请先选择升级对象', theme: 'warning' });
      return;
    }
    const target = escalateUser.name || escalateUser.username;
    try {
      await request(`/${t.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          assigned_to: escalateUser.id,
        }),
      });

      await request(`/${t.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({
          content: `工单已升级，处理人变更为 ${target}`,
          is_public: true,
        }),
      });

      const refreshed = await request<Ticket>(`/${t.id}?load_comments=true`, { skipCache: true });
      setDetail(refreshed);
      refreshTasks();
      Toast({ message: `已升级，处理人已变更为 ${target}`, theme: 'success' });
      setEscalateUser(null);
      setShowEscalatePopup(false);
    } catch (err) {
      Toast({ message: `升级失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const [downloadingIdx, setDownloadingIdx] = useState<number | null>(null);

  const handleAttachmentDownload = async (att: Attachment, idx: number) => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) { Toast({ message: '附件路径无效', theme: 'error' }); return; }

    setDownloadingIdx(idx);
    try {
      let minioPath = rawPath;
      if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
        const parsed = parseMinioPath(rawPath);
        if (parsed) {
          minioPath = `${parsed.bucket}/${parsed.objectKey}`;
        } else {
          Toast({ message: '无法解析附件路径', theme: 'error' });
          return;
        }
      }

      const authToken = localStorage.getItem('auth_token') || '';
      const downloadUrl = `${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;

      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = att.filename || '';
      a.target = '_blank';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (err) {
      Toast({ message: `下载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDownloadingIdx(null);
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

  // ── 普通评论：POST /api/tasks/{id}/comments；返回 true=成功（组件清空输入） ──
  const handleAddComment = async (text: string): Promise<boolean> => {
    if (!detail) {
      Toast({ message: '请输入评论内容', theme: 'warning' });
      return false;
    }
    setSubmittingComment(true);
    try {
      const newComment = await request<Comment>(`/${detail.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true }),
      });
      const enrichedComment = {
        ...newComment,
        created_by_name: newComment.created_by_name || name || newComment.created_by || '未知用户',
        created_by: newComment.created_by || username,
      };
      setDetail((prev) => {
        if (!prev) return prev;
        const updatedComments = prev.comments ? [...prev.comments, enrichedComment] : [enrichedComment];
        return { ...prev, comments: updatedComments };
      });
      Toast({ message: '评论已添加', theme: 'success' });
      return true;
    } catch (err) {
      Toast({ message: `添加评论失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return false;
    } finally {
      setSubmittingComment(false);
    }
  };

  // ── @U老师 讨论：先存用户消息 → 调 POST /api/ai/task/discuss → 重新加载评论；返回 true=成功 ──
  const handleAIDiscuss = async (text: string): Promise<boolean> => {
    if (!detail) return false;
    const userMsg = text;
    setAskingAI(true);
    try {
      // 1. 先保存用户的 @U老师 消息到 task_comments
      try {
        const newComment = await request<Comment>(`/${detail.id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: userMsg, is_public: true }),
        });
        setDetail((prev) => {
          if (!prev) return prev;
          const updatedComments = prev.comments ? [...prev.comments, newComment] : [newComment];
          return { ...prev, comments: updatedComments };
        });
      } catch { /* 保存用户消息失败不阻塞 AI 调用 */ }
      // 2. 调 AI 讨论
      const recentComments = (detail.comments || []).slice(-10).map((c) => ({
        author: c.created_by_name || c.created_by || '?',
        content: c.content,
      }));
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/discuss`, {
        method: 'POST',
        body: JSON.stringify({
          task_id: String(detail.id),
          query: userMsg.replace(/^@U老师\s*/, ''),
          context: { recent_comments: recentComments },
        }),
      });
      const data = await res.json();
      if (data.code === 0) {
        Toast({ message: 'AI 已回复', theme: 'success' });
        loadDetail();  // 重新加载评论（含 AI 回复）
        return true;
      } else {
        Toast({ message: data.message || 'AI 回复失败', theme: 'error' });
        return false;
      }
    } catch (err) {
      Toast({ message: `AI 回复失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return false;
    } finally {
      setAskingAI(false);
    }
  };

  // ── onSend：检测 @U老师 前缀决定走普通评论还是 AI 讨论 ──
  const handleSendComment = async (text: string, _files: File[]): Promise<boolean> => {
    if (text.startsWith('@U老师 ')) {
      return handleAIDiscuss(text);
    }
    return handleAddComment(text);
  };

  // ── [帮我分析] → POST /api/ai/task/diagnose → 讨论区展示短链接 ──
  const handleDiagnose = async () => {
    if (!detail || diagnosing) return;
    setDiagnosing(true);
    try {
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/diagnose`, {
        method: 'POST',
        body: JSON.stringify({ task_id: String(detail.id) }),
      });
      const data = await res.json();
      if (data.code === 0) {
        const d = data.data;
        // 原始 Markdown 存 state，供 Dialog 用 react-markdown 渲染
        setDiagnosisReport(d.report_md || d.root_cause_analysis || '');
        // 短链接预览取根因分析首行
        const preview = d.root_cause_analysis?.slice(0, 40) || '点击查看';
        const shortLink = `📋 [U老师 诊断报告 — ${preview}…](#diagnosis-report)`;
        setDetail((prev) => {
          if (!prev) return prev;
          const aiComment: Comment = {
            id: `diagnosis_${Date.now()}`,
            content: shortLink,
            created_by_name: AI_NAME,
            created_by: AI_NAME,
            created_at: new Date().toISOString(),
          };
          return { ...prev, comments: [...(prev.comments || []), aiComment] };
        });
      } else {
        Toast({ message: data.message || '分析失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `分析失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDiagnosing(false);
    }
  };

  // ── 点击诊断短链接 → 弹窗 ──
  const handleOpenReport = (e: React.MouseEvent) => {
    const target = e.target as HTMLElement;
    const link = target.closest('a');
    if (link && link.getAttribute('href') === '#diagnosis-report') {
      e.preventDefault();
      setReportVisible(true);
    }
  };

  // ── 重新加载（AI 讨论后刷新评论） ──
  const loadDetail = () => {
    if (!detailId) return;
    request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true })
      .then((t) => {
        setDetail(t);
        const meta = t.metadata_info || {};
        setAiSummary(typeof meta.ai_summary === 'string' ? meta.ai_summary as string : '');
      })
      .catch(() => {});
  };

  // 返回任务列表，优先使用浏览器历史记录以保留筛选状态
  const handleBack = () => {
    if (window.history.length > 1) {
      navigate(-1);
    } else {
      navigate('/tasks');
    }
  };

  if (detailLoading) return <Loading text="加载中…" />;
  if (!detail) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={handleBack} />
      <div style={{ padding: 32, textAlign: 'center', color: '#999', marginTop: 56 }}>工单不存在</div>
    </div>
  );

  return (
    <div className="task-detail-page" style={{ paddingBottom: 72 }}>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={handleBack} />
      <div className="page-container" style={{ paddingTop: 56 }}>
        <div className="detail-card">
          <div className="detail-card__header">
            <div className="detail-card__meta">
              <Tag
                theme="primary"
                style={{
                  background: getStatusColor(detail.status),
                  color: '#fff',
                  border: 'none',
                  fontWeight: 500,
                }}
              >
                {STATUS_DISPLAY_MAP[detail.status?.toLowerCase()] || detail.status}
              </Tag>
              <Tag
                theme="primary"
                style={{
                  background: getStatusColor(detail.status),
                  color: '#fff',
                  border: 'none',
                  fontWeight: 500,
                }}
              >
                {TICKET_TYPE_DISPLAY_MAP[detail.ticket_type] || detail.ticket_type || '其他'}
              </Tag>
              <span className="detail-card__id" onClick={() => copyId(detail.id)}>#${detail.id}</span>
            </div>
            <div className="detail-card__action-btns">
              {getActionButtons().map((action, index) => (
                <Button 
                  key={index}
                  size="small" 
                  theme={action.theme as any} 
                  onClick={() => {
                    if (action.actionType === 'resume') {
                      setShowResumePopup(true);
                    } else {
                      handleStatusChange(action);
                    }
                  }}
                  className="detail-card__action-btn"
                  style={action.customStyle}
                >
                  {action.label}
                </Button>
              ))}
            </div>
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
          <h4 className="detail-card__h">讨论摘要</h4>
          {aiSummary ? (
            <SafeHtml html={aiSummary} />
          ) : (
            <p style={{ color: '#999' }}>暂无摘要，U老师 将自动总结讨论进展</p>
          )}
        </div>

        {(detail.contact || detail.reporter_name || detail.assignee_name || detail.created_by_name || detail.assigned_to_name) && (
          <div className="detail-card">
            <h4 className="detail-card__h">联系方式</h4>
            {detail.contact && <DetailRow label="联系电话" value={detail.contact} />}
            <DetailRow label="提交人" value={detail.reporter_name || detail.created_by_name || detail.created_by || '-'} />
            <DetailRow label="处理人" value={detail.assignee_name || detail.assigned_to_name || detail.assigned_to || '-'} />
          </div>
        )}

        {detail.attachments && detail.attachments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">📎 附件 ({detail.attachments.length})</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {detail.attachments.map((att, idx) => {
                const url = att.path || att.url || '';
                const filename = att.filename || '未命名文件';
                const size = att.size ?? 0;
                const isImg = isImageFile(filename);
                const icon = getFileIcon(filename);
                const sizeLabel = formatFileSize(size);
                const isDownloading = downloadingIdx === idx;
                return (
                  <div
                    key={idx}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleAttachmentDownload(att, idx)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleAttachmentDownload(att, idx); }}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      padding: '10px 12px',
                      borderRadius: 8,
                      border: '1px solid #e5e5e5',
                      background: '#fafafa',
                      color: 'inherit',
                      cursor: 'pointer',
                      transition: 'background 0.15s',
                      opacity: isDownloading ? 0.6 : 1,
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = '#f0f7ff'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = '#fafafa'; }}
                  >
                    {isImg ? (
                      <img
                        src={url}
                        alt={filename}
                        style={{
                          width: 40,
                          height: 40,
                          objectFit: 'cover',
                          borderRadius: 4,
                          marginRight: 10,
                          flexShrink: 0,
                        }}
                        onError={(e) => { (e.currentTarget as HTMLElement).style.display = 'none'; }}
                      />
                    ) : (
                      <span style={{ fontSize: 22, marginRight: 10, flexShrink: 0 }}>{icon}</span>
                    )}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div
                        style={{
                          fontSize: 13,
                          fontWeight: 500,
                          color: '#333',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {filename}
                      </div>
                      <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                        {sizeLabel || '未知大小'}
                      </div>
                    </div>
                    <span style={{ fontSize: 16, color: '#0052d9', marginLeft: 8, flexShrink: 0 }}>
                      {isDownloading ? '⏳' : '⬇'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {detail.project_name && (
          <div className="detail-card">
            <DetailRow label="所属项目" value={detail.project_name} />
          </div>
        )}

        <DiscussionPanel
          comments={detail.comments || []}
          onSend={handleSendComment}
          sending={submittingComment || askingAI}
          enableAI
          onMessagesClick={handleOpenReport}
          headerRight={
            <Button size="small" theme="primary" onClick={handleDiagnose} loading={diagnosing}>
              🤖 帮我分析
            </Button>
          }
        />

        {(() => {
          const status = detail.status?.toLowerCase();
          const isClosedOrCanceled = status === 'closed' || status === 'canceled' || status === 'cancelled';
          if (isClosedOrCanceled) return null;
          return (
            <div className="detail-actions">
              <div className="detail-actions__btns">
                <Button size="small" theme="default" onClick={startEdit}>修改工单</Button>
                <Button size="small" theme="danger" onClick={() => setShowEscalatePopup(true)}>升级上报</Button>
              </div>
            </div>
          );
        })()}
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
          <UserSelect value={escalateUser?.id ?? null} onChange={setEscalateUser} title="选择升级对象" />
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => setShowEscalatePopup(false)}>取消</Button>
            <Button theme="danger" onClick={() => handleEscalate(detail!)}>确认升级</Button>
          </div>
        </div>
      </Popup>

      <Popup visible={showResumePopup} onClose={() => { setShowResumePopup(false); setResumeUser(null); }} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4>继续处理</h4>
          <p style={{ color: '#999', fontSize: '13px', marginBottom: '12px' }}>选择新的受理人（可不选，直接确认则保持原处理人）</p>
          <UserSelect value={resumeUser?.id ?? null} onChange={setResumeUser} placeholder="请选择受理人（可选）" title="选择受理人" />
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowResumePopup(false); setResumeUser(null); }}>取消</Button>
            <Button theme="primary" onClick={handleResume}>确认继续</Button>
          </div>
        </div>
      </Popup>

      <Dialog
        visible={reportVisible}
        title="🤖 U老师 诊断报告"
        confirmBtn="关闭"
        onConfirm={() => setReportVisible(false)}
      >
        <div style={{ maxHeight: '60vh', overflowY: 'auto', textAlign: 'left', fontSize: 14, lineHeight: 1.8 }}>
          {diagnosisReport ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {diagnosisReport}
            </ReactMarkdown>
          ) : (
            <p>暂无诊断数据</p>
          )}
        </div>
      </Dialog>
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