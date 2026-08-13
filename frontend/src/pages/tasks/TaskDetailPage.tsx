import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog, Form, FormItem, DateTimePicker } from 'tdesign-mobile-react';
import ClearableInput from '@/shared/components/ClearableInput';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { getOperationLogs, type OperationLog } from '@/api/ticket';
import { uploadCommentAttachment } from '@/api/ticket';
import { TICKET_TYPE_DISPLAY_MAP, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP } from '@/shared/constants/ticket';
import { formatDateTimeShort, deadlinePickerValue } from '@/shared/utils/url';
import { fetchWithAuth } from '@/api/ai';
import { getProjectMembers } from '@/api/projects';
import type { ProjectMember } from '@/api/projects';
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
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; attachments?: Array<string | { path?: string; filename?: string; size?: number }>; reply_to?: string | number; quoted?: { id: string | number; content: string; created_by_name?: string }; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; project_id?: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  reporter_name?: string; assignee_name?: string;
  contact?: string; customer?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; metadata_info?: Record<string, unknown>; comments?: Comment[];
  deadline_at?: string | null;
}

const generateTempId = () =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`;

const AI_NAME = 'U老师';

export default function TaskDetailPage() {
  const { id: detailId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '管理服务');

  const { refreshTasks } = useWorkbenchStore();
  const { username, name } = useAuthStore();

  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editDeadlinePickerVisible, setEditDeadlinePickerVisible] = useState(false);
  // 每次打开最晚解决时间选择器都自增 key，强制 DateTimePicker 重新挂载，
  // 规避 tdesign-mobile-react Picker 在嵌套 Popup（destroyOnClose + CSSTransition）下
  // 二次打开时 Picker 实例竞态、事件绑到旧 DOM 节点导致桌面端无法编辑/滚动的问题
  const [deadlinePickerKey, setDeadlinePickerKey] = useState(0);
  const [editForm, setEditForm] = useState<{ title: string; description: string; priority: string; ticket_type: string; deadline_at?: string }>({ title: '', description: '', priority: 'medium', ticket_type: 'problem' });
  const [escalateUser, setEscalateUser] = useState<UserItem | null>(null);
  const [showEscalatePopup, setShowEscalatePopup] = useState(false);
  const [resumeUser, setResumeUser] = useState<UserItem | null>(null);
  const [showResumePopup, setShowResumePopup] = useState(false);
  const [reassignUser, setReassignUser] = useState<UserItem | null>(null);
  const [showReassignPopup, setShowReassignPopup] = useState(false);
  const [showReturnConfirmPopup, setShowReturnConfirmPopup] = useState(false);
  const [escalateReason, setEscalateReason] = useState('');
  const [returnReason, setReturnReason] = useState('');
  const [reassignReason, setReassignReason] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [askingAI, setAskingAI] = useState(false);
  const [statusChanging, setStatusChanging] = useState(false);

  // U老师 诊断
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosisReport, setDiagnosisReport] = useState('');  // raw Markdown
  const [reportVisible, setReportVisible] = useState(false);

  // AI 摘要（后端定时写入评论，前端从评论提取展示）
  const [aiSummary, setAiSummary] = useState('');

  // 公司/部门审核
  const [approving, setApproving] = useState(false);
  const [showAdjustPopup, setShowAdjustPopup] = useState(false);
  const [adjustName, setAdjustName] = useState('');
  const [showRejectPopup, setShowRejectPopup] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  // 工单动态（操作日志，用于详情页滚动展示）
  const [opLogs, setOpLogs] = useState<OperationLog[]>([]);

  // 项目成员（用于讨论区 @ 提及）
  const [projectMembers, setProjectMembers] = useState<ProjectMember[]>([]);

  useEffect(() => {
    if (!detailId) { setDetail(null); return; }
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true })
      .then((t) => {
        setDetail(t);
        // 摘要存 metadata_info.ai_summary（不混入讨论区）
        const meta = t.metadata_info || {};
        setAiSummary(typeof meta.ai_summary === 'string' ? meta.ai_summary as string : '');

        // 获取项目成员用于 @ 提及（无项目时也能拉到提单人和被指派人）
        getProjectMembers(detailId)
          .then((members) => {
            const reporterUsername = t.created_by;
            const sorted = [...members].sort((a, b) => {
              if (a.username === reporterUsername) return -1;
              if (b.username === reporterUsername) return 1;
              return 0;
            });
            setProjectMembers(sorted);
          })
          .catch(() => setProjectMembers([]));
      })
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  }, [detailId]);

  // 进入详情页即静默预置微信分享卡片：用户点右上角「…」可直接转发到群/好友/朋友圈，无需额外按钮
  useEffect(() => {
    if (!detail?.id) return;
    setupWechatShare({
      title: detail.title || '工单详情',
      desc: (detail.description || '').slice(0, 120) || `工单 #${detail.id}`,
      link: window.location.href,
      imgUrl: WECHAT_CONFIG.shareImgUrl,
    });
  }, [detail?.id]);

  // 加载工单动态（操作日志）
  const fetchOpLogs = async () => {
    if (!detailId) return;
    try {
      const data = await getOperationLogs(detailId);
      setOpLogs(data || []);
    } catch { /* ignore */ }
  };

  useEffect(() => { fetchOpLogs(); }, [detailId]);

  const getCurrentUserRoles = () => {
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

    return { isAssignee, isReporter };
  };

  const getActionButtons = () => {
    const status = detail?.status?.toLowerCase();
    if (!status) return [];

    const isClosed = status === 'closed';
    const isCanceled = status === 'canceled' || status === 'cancelled';
    if (isClosed || isCanceled) return [];

    const { isAssignee, isReporter } = getCurrentUserRoles();

    const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
    if (assigneeOnlyStatuses.includes(status) && !isAssignee) return [];

    if (status === 'resolved' && !isReporter) return [];

    const actions: Record<string, { label: string; nextStatus: string; theme: string; actionType?: string; customStyle?: Record<string, string> }[]> = {
      new: [{ label: '开始处理', nextStatus: 'in_progress', theme: 'primary', customStyle: { backgroundColor: '#0052d9', color: '#fff', borderRadius: '10px', border: 'none' } }],
      in_progress: [
        { label: '暂停任务', nextStatus: 'pending', theme: 'warning', customStyle: { backgroundColor: '#faad14', color: '#fff', borderRadius: '10px', border: 'none' } },
        { label: '处理完成', nextStatus: 'resolved', theme: 'success', customStyle: { backgroundColor: '#52c41a', color: '#fff', borderRadius: '10px', border: 'none' } },
      ],
      pending: [{ label: '继续处理', nextStatus: 'in_progress', theme: 'primary', actionType: 'resume', customStyle: { backgroundColor: '#0052d9', color: '#fff', borderRadius: '10px', border: 'none' } }],
      resolved: [
        { label: '未解决', nextStatus: 'in_progress', theme: 'warning', customStyle: { backgroundColor: '#faad14', color: '#fff', borderRadius: '10px', border: 'none' } },
        { label: '确认关闭', nextStatus: 'closed', theme: 'default', customStyle: { backgroundColor: '#1a1a1a', color: '#fff', borderRadius: '10px', border: 'none' } },
      ],
      canceled: [{ label: '重新打开', nextStatus: 'new', theme: 'primary', customStyle: { backgroundColor: '#0052d9', color: '#fff', borderRadius: '10px', border: 'none' } }],
    };

    return actions[status] || [];
  };

  const handleStatusChange = async (action: { nextStatus: string }) => {
    if (!detail) return;
    if (statusChanging) return;

    setStatusChanging(true);
    try {
      await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: action.nextStatus }),
      });
      refreshTasks();
      const statusLabel = STATUS_DISPLAY_MAP[action.nextStatus] || action.nextStatus;
      await refreshDetail();
      Toast({ message: `状态已更新为${statusLabel}`, theme: 'success' });
    } catch (err) {
      Toast({ message: `状态更新失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setStatusChanging(false);
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
          body: JSON.stringify({ assigned_to: resumeUser.username, operation_type: 'reassign' }),
        });
      }

      const target = resumeUser?.name || resumeUser?.username || '原处理人';
      await refreshDetail();
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
    if (!escalateReason.trim()) {
      Toast({ message: '请填写变更原因', theme: 'warning' });
      return;
    }
    const target = escalateUser.name || escalateUser.username;
    try {
      await request(`/${t.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          assigned_to: escalateUser.username,
          operation_type: 'escalate',
        }),
      });

      await refreshDetail();
      Toast({ message: `已升级，处理人已变更为 ${target}`, theme: 'success' });
      setEscalateUser(null);
      setEscalateReason('');
      setShowEscalatePopup(false);
    } catch (err) {
      Toast({ message: `升级失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const getOperatorLabel = (): string => {
    return name || username || '当前用户';
  };

  // WS 工单状态变更（派单完成/改派/状态流转）实时更新详情，替代轮询
  const handleWsTaskUpdated = (patch: { status?: string; assigned_to?: string | null; assigned_to_name?: string | null }) => {
    setDetail((prev) => {
      if (!prev) return prev;
      // WS 推送的 assigned_to 可能为 null（退单/清空处理人），状态类型为 string | undefined，
      // null → undefined 以兼容类型，展示层有 || 兜底，null 与 undefined 表现一致。
      return {
        ...prev,
        ...(patch.status ? { status: patch.status } : {}),
        ...(patch.assigned_to !== undefined ? { assigned_to: patch.assigned_to ?? undefined } : {}),
        ...(patch.assigned_to_name !== undefined ? { assigned_to_name: patch.assigned_to_name ?? undefined } : {}),
      };
    });
  };

  const refreshDetail = async () => {
    if (!detailId) return;
    const refreshed = await request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true });
    setDetail(refreshed);
    refreshTasks();
    fetchOpLogs();
  };

  // ===== 公司/部门审核 =====
  const approvalInfo = (() => {
    const meta = detail?.metadata_info || {};
    const approvalType = meta.approval_type as string | undefined;
    if (!approvalType) return null;
    return {
      type: approvalType as 'new_company' | 'new_department',
      targetTable: (meta.target_table as string) || '',
      targetId: (meta.target_id as string) || '',
      targetName: (meta.target_name as string) || '',
      companyName: meta.company_name as string | undefined,
    };
  })();

  const handleApprove = async () => {
    if (!approvalInfo) return;
    setApproving(true);
    try {
      const targetType = approvalInfo.type === 'new_company' ? 'company' : 'department';
      await adminRequest(`/users/options/${targetType}/${approvalInfo.targetId}/approve`, {
        method: 'PUT',
      });
      await request(`/${detail!.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'closed' }),
      });
      Toast({ message: '已审核通过', theme: 'success' });
      await refreshDetail();
    } catch (err) {
      Toast({ message: `审核失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setApproving(false);
    }
  };

  // 调整名称后通过
  const handleAdjustApprove = async () => {
    if (!approvalInfo) return;
    const trimmed = (adjustName || '').trim();
    if (!trimmed) {
      Toast({ message: '请输入名称', theme: 'warning' });
      return;
    }
    setApproving(true);
    try {
      const targetType = approvalInfo.type === 'new_company' ? 'company' : 'department';
      await adminRequest(`/users/options/${targetType}/${approvalInfo.targetId}/approve`, {
        method: 'PUT',
        body: JSON.stringify({ new_name: trimmed }),
      });
      await request(`/${detail!.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'closed' }),
      });
      Toast({ message: '已调整名称并审核通过', theme: 'success' });
      setShowAdjustPopup(false);
      setAdjustName('');
      await refreshDetail();
    } catch (err) {
      Toast({ message: `操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setApproving(false);
    }
  };

  const handleReject = async () => {
    if (!approvalInfo) return;
    if (!rejectReason.trim()) {
      Toast({ message: '请填写驳回原因', theme: 'warning' });
      return;
    }
    setApproving(true);
    try {
      const targetType = approvalInfo.type === 'new_company' ? 'company' : 'department';
      await adminRequest(`/users/options/${targetType}/${approvalInfo.targetId}/reject`, {
        method: 'PUT',
        body: JSON.stringify({ reason: rejectReason.trim() }),
      });
      await request(`/${detail!.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'closed' }),
      });
      Toast({ message: '已驳回', theme: 'success' });
      setShowRejectPopup(false);
      setRejectReason('');
      await refreshDetail();
    } catch (err) {
      Toast({ message: `驳回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setApproving(false);
    }
  };

  const handleReturn = async () => {
    if (!detail) return;
    if (!returnReason.trim()) {
      Toast({ message: '请填写变更原因', theme: 'warning' });
      return;
    }
    try {
      const returnTo = detail.created_by_name || detail.reporter_name || detail.created_by || '创建人';

      await request(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'pending' }),
      });

      if (detail.created_by) {
        await request(`/${detail.id}`, {
          method: 'PUT',
          body: JSON.stringify({ assigned_to: detail.created_by, operation_type: 'return' }),
        });
      }

      await refreshDetail();
      Toast({ message: `已退回工单，处理人变更为 ${returnTo}`, theme: 'success' });
      setReturnReason('');
      setShowReturnConfirmPopup(false);
    } catch (err) {
      Toast({ message: `退回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const handleReassign = async () => {
    if (!detail || !reassignUser) return;
    if (!reassignReason.trim()) {
      Toast({ message: '请填写变更原因', theme: 'warning' });
      return;
    }
    const target = reassignUser.name || reassignUser.username;
    try {
      await request(`/${detail.id}`, {
        method: 'PUT',
        body: JSON.stringify({ assigned_to: reassignUser.username, operation_type: 'reassign' }),
      });

      await refreshDetail();
      Toast({ message: `已重新指派给 ${target}`, theme: 'success' });
      setReassignUser(null);
      setReassignReason('');
      setShowReassignPopup(false);
    } catch (err) {
      Toast({ message: `重新指派失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const [downloadingIdx, setDownloadingIdx] = useState<number | null>(null);
  const [viewer, setViewer] = useState<AttachmentViewItem | null>(null);

  const buildAttachmentDownloadUrl = (att: Attachment): string | null => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) return null;
    let minioPath = rawPath;
    if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
      const parsed = parseMinioPath(rawPath);
      if (!parsed) return null;
      minioPath = `${parsed.bucket}/${parsed.objectKey}`;
    }
    const authToken = localStorage.getItem('auth_token') || '';
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;
  };

  /** 微信内下载中转页 URL（/download?path=...&filename=...&token=...）。
   *  微信内不能直接 window.location.href = 下载URL（Content-Disposition:attachment 会被微信拦截回退），
   *  需先跳到同源中转页，用户点「在浏览器中打开」时 WebView 地址栏是中转页 URL，外部浏览器打开中转页再跳下载。 */
  const buildDownloadRedirectUrl = (att: Attachment): string | null => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) return null;
    let minioPath = rawPath;
    if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
      const parsed = parseMinioPath(rawPath);
      if (!parsed) return null;
      minioPath = `${parsed.bucket}/${parsed.objectKey}`;
    }
    const authToken = localStorage.getItem('auth_token') || '';
    return `/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;
  };

  const handleAttachmentDownload = async (att: Attachment, idx: number) => {
    const downloadUrl = buildAttachmentDownloadUrl(att);
    if (!downloadUrl) { Toast({ message: '附件路径无效', theme: 'error' }); return; }

    setDownloadingIdx(idx);
    try {
      // 微信内置 WebView 会拦截程序化 a.click() 下载并强制弹「在浏览器打开」，
      // 且系统浏览器打开缺环境前缀的 URL 会 404 → 回跳微信。
      // 故微信内用 window.location.href 跳绝对地址（弹「在浏览器打开」横幅，浏览器内可下载）；
      // 非微信环境用 a.click() 直接触发下载。
      if (/MicroMessenger/i.test(navigator.userAgent)) {
        // 微信内：跳中转页（非下载 URL），用户点「在浏览器中打开」后中转页跳下载
        const redirectUrl = buildDownloadRedirectUrl(att);
        if (redirectUrl) window.location.href = redirectUrl;
      } else {
        const a = document.createElement('a');
        a.href = downloadUrl;
        a.download = att.filename || '';
        a.target = '_blank';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
      }
    } catch (err) {
      Toast({ message: `下载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setDownloadingIdx(null);
    }
  };

  /** 构造附件内联预览 URL（/api/tasks/files/{minioPath}），供缩略图 <img> src 与 AttachmentViewer 共用 */
  const buildPreviewUrl = (att: Attachment): string | null => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) return null;
    let minioPath = rawPath;
    if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
      const parsed = parseMinioPath(rawPath);
      if (!parsed) return null;
      minioPath = `${parsed.bucket}/${parsed.objectKey}`;
    }
    return `${API_CONFIG.TASKS.BASE_URL}/files/${encodeURIComponent(minioPath)}`;
  };

  /** 打开附件预览（图片走灯箱、PDF/Markdown 内联渲染） */
  const openAttachmentViewer = (att: Attachment) => {
    const previewUrl = buildPreviewUrl(att);
    if (!previewUrl) { Toast({ message: '附件路径无效', theme: 'error' }); return; }
    // 微信内 downloadUrl 指向中转页（避免直接跳下载URL被微信拦截回退）
    const isWechat = /MicroMessenger/i.test(navigator.userAgent);
    const dl = isWechat ? buildDownloadRedirectUrl(att) : buildAttachmentDownloadUrl(att);
    setViewer({
      filename: att.filename || '未命名文件',
      size: att.size,
      previewUrl,
      downloadUrl: dl || previewUrl,
    });
  };

  const startEdit = () => {
    if (!detail) return;
    setEditForm({
      title: detail.title,
      description: detail.description,
      priority: detail.priority || 'medium',
      ticket_type: detail.ticket_type || 'problem',
      deadline_at: detail.deadline_at || undefined,
    });
    setEditing(true);
  };

  const saveEdit = async () => {
    if (!detail) return;
    try {
      await request<Ticket>(`/${detail.id}`, {
        method: 'PUT',
        body: JSON.stringify({ ...editForm, operation_type: 'update' }),
      });
      await refreshDetail();
      Toast({ message: '修改成功', theme: 'success' });
      setEditing(false);
    } catch (err) {
      Toast({ message: `修改失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // ── 最晚解决时间（截止时间）：编辑页只读回显已有 deadline_at，进度条仅提单时用 ──
  const formatEditDeadlineAbs = (iso?: string): string => {
    if (!iso) return '未设置';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return '未设置';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())} 前`;
  };



  const copyId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => Toast({ message: '已复制工单号', theme: 'success' }));
  };

  // ── 普通评论：POST /api/tasks/{id}/comments；返回 true=成功（组件清空输入） ──
  const handleAddComment = async (text: string, files: File[] = [], options?: { replyTo?: string | number }): Promise<boolean> => {
    if (!detail) {
      Toast({ message: '请输入评论内容', theme: 'warning' });
      return false;
    }
    setSubmittingComment(true);
    try {
      // 上传附件
      const tempId = generateTempId();
      for (const f of files) {
        await uploadCommentAttachment(f, tempId);
      }
      const newComment = await request<Comment>(`/${detail.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true, attachments: files.length ? [tempId] : [], reply_to: options?.replyTo }),
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
      Toast({ message: files.length ? '评论和附件已添加' : '评论已添加', theme: 'success' });
      return true;
    } catch (err) {
      Toast({ message: `添加评论失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return false;
    } finally {
      setSubmittingComment(false);
    }
  };

  // 删除评论（后端按创建人鉴权）：成功后从本地列表移除
  const handleDeleteComment = async (id: string | number): Promise<void> => {
    if (!detail) return;
    await request(`/comments/${id}`, { method: 'DELETE' });
    setDetail((prev) => {
      if (!prev) return prev;
      return { ...prev, comments: (prev.comments || []).filter((c) => String(c.id) !== String(id)) };
    });
    Toast({ message: '评论已删除', theme: 'success' });
  };

  // ── @U老师 讨论：先存用户消息 → 调 POST /api/ai/task/discuss → 重新加载评论；返回 true=成功 ──
  const handleAIDiscuss = async (text: string, files: File[] = [], options?: { replyTo?: string | number }): Promise<boolean> => {
    if (!detail) return false;
    const userMsg = text;
    setAskingAI(true);
    try {
      // 上传附件
      const tempId = generateTempId();
      for (const f of files) {
        await uploadCommentAttachment(f, tempId);
      }
      // 1. 先保存用户的 @U老师 消息到 task_comments
      try {
        const newComment = await request<Comment>(`/${detail.id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: userMsg, is_public: true, attachments: files.length ? [tempId] : [], reply_to: options?.replyTo }),
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
  const handleSendComment = async (text: string, files: File[], options?: { replyTo?: string | number }): Promise<boolean> => {
    if (text.startsWith('@U老师 ')) {
      return handleAIDiscuss(text, files, options);
    }
    return handleAddComment(text, files, options);
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

  // 派单完成 / 状态变更由 WS task.updated 实时推送（见 DiscussionPanel onTaskUpdated），不再轮询。

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
      <Navbar
        title="工单详情"
        fixed
        leftArrow
        onLeftClick={handleBack}
      />
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
                  disabled={statusChanging}
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
          <div className="detail-card__info-grid">
            <div className="detail-info-item">
              <span className="detail-info-item__icon">👤</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">创建人</span>
                <span className="detail-info-item__value">{detail.created_by_name || detail.reporter_name || detail.created_by || '-'}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon">🎯</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">处理人</span>
                {/* AI 单派单中（status=new 且处理人未写入，Worker 60s 轮询派单）→ 呼吸动效「派单中」；
                    手动单未指派 → 「未指派」 */}
                {(() => {
                  const noAssignee = !detail.assignee_name && !detail.assigned_to_name && !detail.assigned_to;
                  const isAiTicket = !!detail.metadata_info?.session_id;
                  if (noAssignee && isAiTicket && detail.status === 'new') {
                    return (
                      <span className="detail-info-item__value task-card2__person-name--dispatching">
                        <i className="dispatch-pulse dispatch-pulse--inline" />派单中
                      </span>
                    );
                  }
                  return (
                    <span className="detail-info-item__value">
                      {detail.assignee_name || detail.assigned_to_name || detail.assigned_to || (noAssignee ? '未指派' : '-')}
                    </span>
                  );
                })()}
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon">📁</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">所属项目</span>
                <span className="detail-info-item__value">{detail.project_name || '-'}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon">⏰</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">最晚解决时间</span>
                <span className="detail-info-item__value">{detail.deadline_at ? formatDateTimeShort(detail.deadline_at) : '未设置'}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon">🕐</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">创建时间</span>
                <span className="detail-info-item__value">{formatDateTimeShort(detail.created_at)}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon">🔄</span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">更新时间</span>
                <span className="detail-info-item__value">{formatDateTimeShort(detail.updated_at)}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="detail-card">
          <h4 className="detail-card__h">问题描述</h4>
          <SafeHtml html={detail.description || '<p style="color:#999">无描述</p>'} />
        </div>

        {/* 公司/部门审核入口：仅管理员可见，工单 metadata_info 含 approval_type 时展示 */}
        {approvalInfo && username === 'admin' && (
          <div className="detail-card" style={{ border: '2px solid #0052d9', borderRadius: 12 }}>
            <h4 className="detail-card__h" style={{ color: '#0052d9' }}>
              {approvalInfo.type === 'new_company' ? '🏢 新公司录入审核' : '🏬 新部门录入审核'}
            </h4>
            <div style={{ fontSize: 14, color: '#333', lineHeight: 1.8, marginBottom: 16 }}>
              <div>
                <strong>{approvalInfo.type === 'new_company' ? '公司名称' : '部门名称'}：</strong>
                {approvalInfo.targetName}
              </div>
              {approvalInfo.companyName && (
                <div><strong>所属公司：</strong>{approvalInfo.companyName}</div>
              )}
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                请审核以下信息，通过后该{approvalInfo.type === 'new_company' ? '公司' : '部门'}将对所有用户可见
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button
                block
                theme="primary"
                loading={approving}
                onClick={handleApprove}
                style={{ borderRadius: '10px', backgroundColor: '#52c41a', color: '#fff', border: 'none' }}
              >
                通过
              </Button>
              <Button
                block
                theme="default"
                variant="outline"
                disabled={approving}
                onClick={() => { setAdjustName(approvalInfo.targetName); setShowAdjustPopup(true); }}
                style={{ borderRadius: '10px' }}
              >
                调整名称
              </Button>
              <Button
                block
                theme="danger"
                variant="outline"
                disabled={approving}
                onClick={() => setShowRejectPopup(true)}
                style={{ borderRadius: '10px' }}
              >
                驳回
              </Button>
            </div>
          </div>
        )}

        <div
          className="detail-card ticket-dynamics-card"
          onClick={() => navigate(`/tasks/${detailId}/operations`)}
          role="button"
          tabIndex={0}
        >
          <h4 className="detail-card__h">
            工单动态
            <span className="ticket-dynamics-card__more">查看全部 ›</span>
          </h4>
          {(() => {
            if (opLogs.length === 0) {
              return <p style={{ color: '#999' }}>暂无动态</p>;
            }
            const displayName = (l: OperationLog) => l.operator_name || l.operator;
            const formatTime = (ts: string) => {
              const d = new Date(ts);
              return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            };
            const items = opLogs.map((l) => ({
              key: l.id,
              text: `${formatTime(l.created_at)} · ${l.description || l.operation_type}`,
            }));
            // 复制一份用于无缝滚动
            const loopItems = [...items, ...items];
            return (
              <div className="ticket-dynamics-scroll" style={{ '--count': items.length } as React.CSSProperties}>
                <div className="ticket-dynamics-scroll__track">
                  {loopItems.map((it, i) => (
                    <div className="ticket-dynamics-scroll__item" key={`${it.key}-${i}`}>{it.text}</div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>

        <div className="detail-card">
          <h4 className="detail-card__h">讨论摘要</h4>
          {aiSummary ? (
            <SafeHtml html={aiSummary} />
          ) : (
            <p style={{ color: '#999' }}>暂无摘要，U老师 将自动总结讨论进展</p>
          )}
        </div>

        {detail.attachments && detail.attachments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">📎 附件 ({detail.attachments.length})</h4>
            {(() => {
              const imgItems = detail.attachments.filter((a) => isImageFile(a.filename || ''));
              const fileItems = detail.attachments
                .map((a, i) => ({ att: a, origIdx: i }))
                .filter(({ att }) => !isImageFile(att.filename || ''));
              return (
                <>
                  {/* 图片缩略图网格：直接可见，点击放大（src 用代理 URL，避免 object_path 直链 404） */}
                  {imgItems.length > 0 && (
                    <div className="detail-attachment-thumbs">
                      {imgItems.map((att, i) => {
                        const thumbSrc = buildPreviewUrl(att);
                        if (!thumbSrc) return null;
                        return (
                          <img
                            key={`img-${i}`}
                            src={thumbSrc}
                            alt={att.filename || '图片'}
                            className="detail-attachment-thumb"
                            loading="lazy"
                            onClick={() => openAttachmentViewer(att)}
                            onError={(e) => {
                              // 微信 WebView 偶发 img 静默渲染失败（HTTP 200 但白屏）：破缓存重试一次，仍失败换文件名占位
                              const el = e.currentTarget;
                              if (!el.dataset.retried) {
                                el.dataset.retried = '1';
                                const sep = thumbSrc.includes('?') ? '&' : '?';
                                el.src = `${thumbSrc}${sep}_r=${Date.now()}`;
                              } else {
                                el.style.display = 'none';
                                const ph = document.createElement('div');
                                ph.className = 'detail-attachment-thumb detail-attachment-thumb--fallback';
                                ph.textContent = '🖼️';
                                ph.onclick = () => openAttachmentViewer(att);
                                el.parentNode?.appendChild(ph);
                              }
                            }}
                          />
                        );
                      })}
                    </div>
                  )}
                  {/* 非图片文件卡片（图标 + 文件名 + 下载，保留下载进度态） */}
                  {fileItems.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {fileItems.map(({ att, origIdx }) => {
                        const filename = att.filename || '未命名文件';
                        const size = att.size ?? 0;
                        const icon = getFileIcon(filename);
                        const sizeLabel = formatFileSize(size);
                        const isDownloading = downloadingIdx === origIdx;
                        return (
                          <div
                            key={`file-${origIdx}`}
                            role="button"
                            tabIndex={0}
                            onClick={() => openAttachmentViewer(att)}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openAttachmentViewer(att); }}
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
                            <span style={{ fontSize: 22, marginRight: 10, flexShrink: 0 }}>{icon}</span>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: 13, fontWeight: 500, color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {filename}
                              </div>
                              <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>
                                {sizeLabel || '未知大小'}
                              </div>
                            </div>
                            <span
                              role="button"
                              aria-label="下载附件"
                              title="下载"
                              onClick={(e) => { e.stopPropagation(); handleAttachmentDownload(att, origIdx); }}
                              style={{ fontSize: 16, color: '#0052d9', marginLeft: 8, flexShrink: 0, cursor: 'pointer' }}
                            >
                              {isDownloading ? '⏳' : '⬇'}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        )}

        <DiscussionPanel
          comments={detail.comments || []}
          onSend={handleSendComment}
          onDeleteComment={handleDeleteComment}
          sending={submittingComment || askingAI}
          enableAI
          enableAttach
          mentionUsers={projectMembers}
          taskId={detail?.id}
          onTaskUpdated={handleWsTaskUpdated}
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

          const { isAssignee, isReporter } = getCurrentUserRoles();
          const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
          const showRoleActions = assigneeOnlyStatuses.includes(status) ? isAssignee : (status === 'resolved' ? isReporter : false);

          return (
            <div className="detail-actions">
              <div className="detail-actions__btns">
                {showRoleActions && (
                  <>
                    <Button size="small" theme="default" style={{ backgroundColor: '#333333', color: '#fff', border: 'none' }} onClick={startEdit}>修改工单</Button>
                    <Button size="small" theme="default" style={{ backgroundColor: '#faad14', color: '#fff', border: 'none' }} onClick={() => setShowReturnConfirmPopup(true)}>退回工单</Button>
                    <Button size="small" theme="default" style={{ backgroundColor: '#0052d9', color: '#fff', border: 'none' }} onClick={() => setShowReassignPopup(true)}>重新指派</Button>
                    <Button size="small" theme="default" style={{ backgroundColor: '#d54941', color: '#fff', border: 'none' }} onClick={() => setShowEscalatePopup(true)}>升级上报</Button>
                  </>
                )}
              </div>
            </div>
          );
        })()}
      </div>

      <Popup visible={editing} onClose={() => setEditing(false)} placement="bottom" showOverlay>
        <div className="ticket-edit-form">
          <div className="ticket-edit-form__header">
            <span className="ticket-edit-form__title">修改工单</span>
            <span className="ticket-edit-form__close" onClick={() => setEditing(false)}>×</span>
          </div>
          <div className="ticket-edit-form__body">
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">标题</label>
              <ClearableInput
                value={editForm.title}
                onChange={(v) => setEditForm((p) => ({ ...p, title: String(v) }))}
                placeholder="请输入工单标题"
              />
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">问题描述</label>
              <Textarea
                value={editForm.description}
                onChange={(v) => setEditForm((p) => ({ ...p, description: String(v) }))}
                placeholder="请详细描述问题..."
                autosize={{ minRows: 5, maxRows: 12 }}
                maxlength={2000}
              />
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">优先级</label>
              <div className="tasks-create-modal__radio-group">
                {Object.entries(PRIORITY_DISPLAY_MAP).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${editForm.priority === value ? 'is-active' : ''}`}
                    onClick={() => setEditForm((p) => ({ ...p, priority: value }))}
                  >{label}</button>
                ))}
              </div>
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">类型</label>
              <div className="tasks-create-modal__radio-group">
                {Object.entries(TICKET_TYPE_DISPLAY_MAP).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${editForm.ticket_type === value ? 'is-active' : ''}`}
                    onClick={() => setEditForm((p) => ({ ...p, ticket_type: value }))}
                  >{label}</button>
                ))}
              </div>
            </div>
            {/* 最晚解决时间：DateTimePicker 选择，最小单位小时 */}
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">最晚解决时间</label>
              <div className="ticket-confirm__deadline">
                <div
                  className="ticket-confirm__deadline-field"
                  onClick={() => { setDeadlinePickerKey((k) => k + 1); setEditDeadlinePickerVisible(true); }}
                >
                  <span className={editForm.deadline_at ? 'ticket-confirm__deadline-value' : 'ticket-confirm__deadline-placeholder'}>
                    {editForm.deadline_at ? formatEditDeadlineAbs(editForm.deadline_at) : '点击选择'}
                  </span>
                  <span className="ticket-confirm__deadline-arrow">›</span>
                </div>
                {editForm.deadline_at && (
                  <button
                    type="button"
                    className="ticket-confirm__deadline-clear"
                    onClick={() => setEditForm((p) => ({ ...p, deadline_at: undefined }))}
                  >清除</button>
                )}
              </div>
            </div>
          </div>
          <div className="ticket-edit-form__footer">
            <Button theme="default" block onClick={() => setEditing(false)}>取消</Button>
            <Button theme="primary" block onClick={saveEdit}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 最晚解决时间选择器：mode=hour 最小单位小时
          destroyOnClose + preventScrollThrough={false} 修复微信内置浏览器两层 Popup 嵌套时
          DateTimePicker 滚轮无法滚动（详见 TicketDetailPage 同名注释） */}
      <Popup visible={editDeadlinePickerVisible} onClose={() => setEditDeadlinePickerVisible(false)} placement="bottom" destroyOnClose preventScrollThrough={false}>
        <DateTimePicker
          key={deadlinePickerKey}
          mode="hour"
          title="选择最晚解决时间"
          format="YYYY-MM-DD HH:00"
          value={deadlinePickerValue(editForm.deadline_at)}
          onConfirm={(v) => {
            const d = new Date(typeof v === 'number' ? v : String(v));
            if (!isNaN(d.getTime())) {
              d.setMinutes(0, 0, 0);
              setEditForm((p) => ({ ...p, deadline_at: d.toISOString() }));
            }
            setEditDeadlinePickerVisible(false);
          }}
          onCancel={() => setEditDeadlinePickerVisible(false)}
        />
      </Popup>

      <Popup visible={showEscalatePopup} onClose={() => { setShowEscalatePopup(false); setEscalateReason(''); }} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">升级上报</h4>
          <p style={{ color: '#999', fontSize: '13px', marginBottom: '12px' }}>请选择升级对象</p>
          <UserSelect value={escalateUser?.id ?? null} onChange={setEscalateUser} title="选择升级对象" />
          <Form initialData={{}}>
            <FormItem label="变更原因" name="escalateReason" labelAlign="top" requiredMark>
              <Textarea
                value={escalateReason}
                onChange={(v) => setEscalateReason(String(v))}
                placeholder="请输入升级原因（必填）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowEscalatePopup(false); setEscalateReason(''); }}>取消</Button>
            <Button theme="danger" onClick={() => handleEscalate(detail!)} disabled={!escalateUser || !escalateReason.trim()}>确认升级</Button>
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

      <Popup visible={showReassignPopup} onClose={() => { setShowReassignPopup(false); setReassignUser(null); setReassignReason(''); }} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">重新指派</h4>
          <p style={{ color: '#999', fontSize: '13px', marginBottom: '12px' }}>选择新的处理人</p>
          <UserSelect value={reassignUser?.id ?? null} onChange={setReassignUser} placeholder="请选择处理人" title="选择处理人" />
          <Form initialData={{}}>
            <FormItem label="变更原因" name="reassignReason" labelAlign="top" requiredMark>
              <Textarea
                value={reassignReason}
                onChange={(v) => setReassignReason(String(v))}
                placeholder="请输入重新指派原因（必填）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowReassignPopup(false); setReassignUser(null); setReassignReason(''); }}>取消</Button>
            <Button theme="primary" onClick={handleReassign} disabled={!reassignUser || !reassignReason.trim()}>确认指派</Button>
          </div>
        </div>
      </Popup>

      <Popup visible={showReturnConfirmPopup} onClose={() => { setShowReturnConfirmPopup(false); setReturnReason(''); }} placement="bottom" showOverlay>
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">退回工单</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            确定要将此工单退回吗？<br />
            退回后工单状态将变更为<span style={{ color: '#faad14', fontWeight: 500 }}>挂起</span>，处理人变更为创建人。
          </p>
          <Form initialData={{}}>
            <FormItem label="变更原因" name="returnReason" labelAlign="top" requiredMark>
              <Textarea
                value={returnReason}
                onChange={(v) => setReturnReason(String(v))}
                placeholder="请输入退回原因（必填）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowReturnConfirmPopup(false); setReturnReason(''); }}>取消</Button>
            <Button theme="danger" onClick={handleReturn} disabled={!returnReason.trim()}>确认退回</Button>
          </div>
        </div>
      </Popup>

      {/* 驳回原因弹窗 */}
      <Popup
        visible={showRejectPopup}
        onClose={() => { if (!approving) { setShowRejectPopup(false); setRejectReason(''); } }}
        placement="bottom"
        showOverlay
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">驳回审核</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            确定要驳回{approvalInfo?.type === 'new_company' ? '公司' : '部门'}「{approvalInfo?.targetName}」的录入申请吗？
          </p>
          <Form initialData={{}}>
            <FormItem label="驳回原因" name="rejectReason" labelAlign="top" requiredMark>
              <Textarea
                value={rejectReason}
                onChange={(v) => setRejectReason(String(v))}
                placeholder="请输入驳回原因（必填）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={approving} onClick={() => { setShowRejectPopup(false); setRejectReason(''); }}>取消</Button>
            <Button theme="danger" loading={approving} onClick={handleReject} disabled={!rejectReason.trim()}>确认驳回</Button>
          </div>
        </div>
      </Popup>

      {/* 调整名称弹窗 */}
      <Popup
        visible={showAdjustPopup}
        onClose={() => { if (!approving) { setShowAdjustPopup(false); setAdjustName(''); } }}
        placement="bottom"
        showOverlay
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">调整名称</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            修改{approvalInfo?.type === 'new_company' ? '公司' : '部门'}名称后审核通过：
          </p>
          <Form initialData={{}}>
            <FormItem label="名称" name="adjustName" labelAlign="top" requiredMark>
              <ClearableInput
                value={adjustName}
                onChange={(v) => setAdjustName(String(v))}
                placeholder="请输入新的名称"
                maxlength={64}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={approving} onClick={() => { setShowAdjustPopup(false); setAdjustName(''); }}>取消</Button>
            <Button theme="primary" loading={approving} onClick={handleAdjustApprove} disabled={!adjustName.trim()}>确认通过</Button>
          </div>
        </div>
      </Popup>

      <Dialog
        visible={reportVisible}
        title="🤖 U老师 诊断报告"
        confirmBtn="关闭"
        onConfirm={() => setReportVisible(false)}
      >
        <div className="markdown-body" style={{ maxHeight: '60vh', overflowY: 'auto', textAlign: 'left', fontSize: 14, lineHeight: 1.8 }}>
          {diagnosisReport ? (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {diagnosisReport}
            </ReactMarkdown>
          ) : (
            <p>暂无诊断数据</p>
          )}
        </div>
      </Dialog>

      {/* 附件预览：图片灯箱 / PDF 内联 / Markdown 渲染 */}
      <AttachmentViewer item={viewer} onClose={() => setViewer(null)} />
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