// 摇人 · 历史工单详情页（U老师诊断生成的工单）
// 数据源：AI 模块 GET /api/ai/qa/ticket?session_id=...；操作：催办 / 上报（任务服务通知）
// 路由 /app/call/ticket/:id 中的 :id 即 AI 会话 session_id
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag, Popup, Input, Textarea } from 'tdesign-mobile-react';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { NotificationIcon, UploadIcon, RollbackIcon, EditIcon } from 'tdesign-icons-react';
import { getMyProjects, type ProjectItem } from '@/api/projects';
import { qaGetTicket } from '@/api/ai';
import { cancelTicket, urgeTicket, reportTicket, uploadCommentAttachment } from '@/api/ticket';
import {
  isTerminalTicketStatus,
  canUrgeTicket,
  canReportTicket,
  canCancelTicket,
  STATUS_DISPLAY_MAP,
  getStatusColor,
} from '@/shared/constants/ticket';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import UserSelect from '@/shared/components/UserSelect';
import SafeHtml from '@/shared/components/SafeHtml';
import { useAuthStore } from '@/stores/auth';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';
import { formatDateTime } from '@/shared/utils/url';
import type { UserItem } from '@/api/users';

interface AiDiagnosis {
  problem_summary?: string;
  hypotheses?: string[];
  ruled_out?: string[];
  collected_info?: Record<string, unknown>;
  rounds?: number;
}
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; attachments?: Array<string | { path?: string; filename?: string; size?: number }>; }
interface AiTicket {
  ticket_id?: string;
  session_id: string;
  type?: string;
  title?: string;
  description?: string;
  priority?: string;
  status?: string;
  contact?: string;
  // AI 接口返回 Unix 秒（number）；DB TicketResponse 返回 ISO 字符串（string），两者都支持
  created_at?: number | string;
  diagnosis?: AiDiagnosis;
  attachments?: Array<Record<string, unknown>>;
  comments?: Comment[];
  // 人员信息（来自 tasks 服务 GET /{ticket_id} 的 TicketResponse）
  created_by?: string;
  created_by_name?: string;
  assigned_to?: string;
  assigned_to_name?: string;
  // 项目（AI 接口返回 project；DB TicketResponse 返回 project_name，展示以 DB 为准）
  project?: string;
  project_name?: string;
  // 项目编码（DB TicketResponse 返回，编辑回显与提交用）
  project_id?: string;
  // 类型专属
  location?: string; robot_type?: string; fault_code?: string; special_notes?: string;
  steps_to_reproduce?: string; expected_result?: string; actual_result?: string; severity?: string; version?: string;
  scenario?: string; expected_effect?: string; source?: string;
  support_type?: string; preferred_response?: string;
}

const TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};
// DB priority 是英文枚举（low/medium/high/urgent），AI 接口是中文（低/中/高/紧急），统一转中文展示
const PRIORITY_CN: Record<string, string> = { urgent: '紧急', high: '高', medium: '中', low: '低' };
const PRIORITY_EN: Record<string, string> = { 紧急: 'urgent', 高: 'high', 中: 'medium', 低: 'low' };
const displayPriority = (p?: string) => (p ? PRIORITY_CN[p] || p : '');
/** priority 统一转英文枚举 value（编辑表单用）：英文原样，中文映射，缺省 medium */
const toEnPriority = (p?: string) => (p && PRIORITY_CN[p] ? p : PRIORITY_EN[p || ''] || 'medium');

// 附件相关工具：与系统任务详情页 TaskDetailPage 同款逻辑，保证两处体验一致
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
  } catch { return null; }
};
// AI/DB 返回的 attachments 可能是 object_path 字符串，或 {path,url,filename,size} 字典，统一归一化
interface NormalizedAttachment { path?: string; url?: string; filename?: string; size?: number; }
const normalizeAttachment = (a: unknown): NormalizedAttachment | null => {
  if (typeof a === 'string') {
    const segs = a.split('/');
    return { path: a, filename: segs[segs.length - 1] || '未命名文件' };
  }
  if (a && typeof a === 'object') {
    const o = a as Record<string, unknown>;
    return {
      path: typeof o.path === 'string' ? o.path : undefined,
      url: typeof o.url === 'string' ? o.url : undefined,
      filename: typeof o.filename === 'string' ? o.filename : undefined,
      size: typeof o.size === 'number' ? o.size : undefined,
    };
  }
  return null;
};

export default function TicketDetailPage() {
  const { id: sessionId = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const { username, name, isAdmin } = useAuthStore();

  const [ticket, setTicket] = useState<AiTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [aiSummary, setAiSummary] = useState('');
  const tempIdRef = useRef<string>(typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`);
  const [viewer, setViewer] = useState<AttachmentViewItem | null>(null);

  const fetchDetail = useCallback(async (silent = false) => {
    if (!sessionId) return;
    if (!silent) setLoading(true);
    try {
      // 手动工单（无 session_id，URL 形如 /call/ticket/db_<数字id>）：直接按 DB 工单 id 查询，
      // 跳过 qaGetTicket（AI 接口仅支持 session_id 查询，手动工单没有 session_id）。
      const dbIdMatch = /^db_(\d+)$/.exec(sessionId);
      if (dbIdMatch) {
        const dbId = dbIdMatch[1];
        const taskDetail = await request<{ comments: Comment[]; metadata_info?: { ai_summary?: string }; status?: string; created_by?: string; created_by_name?: string; assigned_to?: string; assigned_to_name?: string; title?: string; description?: string; priority?: string; ticket_type?: string; customer?: string; project_name?: string; project_id?: string; created_at?: string }>(`/${dbId}?load_comments=true`, { skipCache: true });
        setTicket({
          ticket_id: String(dbId),
          session_id: '',
          title: taskDetail.title || '',
          description: taskDetail.description ?? '',
          status: taskDetail.status || '',
          priority: taskDetail.priority || '',
          type: taskDetail.ticket_type || '',
          contact: taskDetail.customer || '',
          comments: taskDetail.comments || [],
          created_by: taskDetail.created_by || '',
          created_by_name: taskDetail.created_by_name || '',
          assigned_to: taskDetail.assigned_to || '',
          assigned_to_name: taskDetail.assigned_to_name || '',
          project_name: taskDetail.project_name || '',
          project_id: taskDetail.project_id || '',
          created_at: taskDetail.created_at || '',
          // 手动工单无 AI 诊断数据
          diagnosis: undefined,
          // 手动工单附件来自 tasks 服务 GET /{dbId} 的 attachments 字段（object_path 或字典数组）
          attachments: ((taskDetail as unknown as { attachments?: unknown[] }).attachments as Array<Record<string, unknown>> | undefined) ?? [],
        });
        setAiSummary(typeof taskDetail.metadata_info?.ai_summary === 'string' ? taskDetail.metadata_info.ai_summary : '');
        return;
      }
      const res = await qaGetTicket(sessionId);
      if (res?.code === 0 && res.data) {
        const aiTicket = res.data as AiTicket;
        // silent 刷新（编辑后 / 派单轮询）不重置 ticket：避免被 AI 滞后的 Redis 副本覆盖，
        // 仅由下方 DB 覆盖可编辑字段；diagnosis 等 AI 特有数据保持现有值不动。
        if (!silent) setTicket(aiTicket);
        if (aiTicket.ticket_id) {
          try {
            const taskDetail = await request<{ comments: Comment[]; metadata_info?: { ai_summary?: string }; status?: string; created_by?: string; created_by_name?: string; assigned_to?: string; assigned_to_name?: string; title?: string; description?: string; priority?: string; ticket_type?: string; customer?: string; project_name?: string; project_id?: string; created_at?: string }>(`/${aiTicket.ticket_id}?load_comments=true`, { skipCache: true });
            // 用 DB 的 status 覆盖 AI 的 status：AI(qaGetTicket) 返回 dispatched/escalated 等 AI 内部状态，
            // DB(tasks 表) 是 new/in_progress 等标准枚举。列表(qaListTickets)也来自 DB，
            // 覆盖后详情页按钮置灰(canUrgeTicket/canReportTicket)与列表一致。
            // 人员信息 + 可编辑字段（title/description/priority/type/contact/project）同样以 DB 为准——
            // 编辑只写 DB，AI 的 Redis 副本可能滞后；AI 接口只保留 diagnosis 等 AI 特有数据。
            setTicket((prev) => prev ? {
              ...prev,
              comments: taskDetail.comments || [],
              status: taskDetail.status || prev.status,
              created_by: taskDetail.created_by || prev.created_by,
              created_by_name: taskDetail.created_by_name || prev.created_by_name,
              assigned_to: taskDetail.assigned_to || prev.assigned_to,
              assigned_to_name: taskDetail.assigned_to_name || prev.assigned_to_name,
              // 创建时间以 DB 真实创建时间为准：AI 接口 get_ticket 每次读取都用 int(time.time()) 重写，
              // 并非工单真实创建时间，故用 DB 的 created_at 覆盖（与 status/created_by 同口径）。
              created_at: taskDetail.created_at ?? prev.created_at,
              title: taskDetail.title || prev.title,
              description: taskDetail.description ?? prev.description,
              priority: taskDetail.priority || prev.priority,
              type: taskDetail.ticket_type || prev.type,
              contact: taskDetail.customer || prev.contact,
              project_name: taskDetail.project_name || prev.project_name || prev.project,
              // 项目编码以 DB 为准（编辑回显与提交用），AI 接口不返回该字段
              project_id: taskDetail.project_id || prev.project_id,
            } : prev);
            setAiSummary(typeof taskDetail.metadata_info?.ai_summary === 'string' ? taskDetail.metadata_info.ai_summary : '');
          } catch { /* 评论加载失败不阻塞主流程 */ }
        }
      } else {
        setMsg(res?.message || '该会话尚未生成工单');
      }
    } catch (err) {
      setMsg(err instanceof Error ? err.message : '加载失败');
    } finally {
      if (!silent) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  // 进入详情页即静默预置微信分享卡片：用户点右上角「…」可直接转发到群/好友/朋友圈，无需额外按钮
  useEffect(() => {
    if (!ticket?.ticket_id) return;
    setupWechatShare({
      title: ticket.title || '工单详情',
      desc: (ticket.description || '').slice(0, 120) || `工单 ${ticket.ticket_id}`,
      link: window.location.href,
      imgUrl: WECHAT_CONFIG.shareImgUrl,
    });
  }, [ticket?.ticket_id]);

  // 催办/上报：先选用户再调接口
  const [actionType, setActionType] = useState<'urge' | 'report'>('urge');
  const [actionUser, setActionUser] = useState<UserItem | null>(null);
  const [showActionPopup, setShowActionPopup] = useState(false);
  type DetailActionType = 'urge' | 'report' | 'cancel';
  const [acting, setActing] = useState<DetailActionType | null>(null);

  const openActionPopup = (type: 'urge' | 'report') => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失', theme: 'warning' }); return; }
    setActionType(type);
    setActionUser(null);
    setShowActionPopup(true);
  };

  const handleActionConfirm = async () => {
    if (!ticket?.ticket_id || !actionUser) { Toast({ message: '请选择通知用户', theme: 'warning' }); return; }
    setActing(actionType);
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
      setActing(null);
    }
  };

  // 撤回：将工单状态置为已取消（Canceled）
  const handleCancel = async () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法撤回', theme: 'warning' }); return; }
    setActing('cancel');
    try {
      await cancelTicket(ticket.ticket_id);
      Toast({ message: '已撤回，工单已取消', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `撤回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setActing(null);
    }
  };

  // 派单中：AI 单 status=new 且处理人未写入（Worker 60s 轮询派单，期间 5s 轮询自动刷新）
  const isDispatching = !!ticket && ticket.status === 'new' && !ticket.assigned_to && !ticket.assigned_to_name;
  useEffect(() => {
    if (!isDispatching) return;
    const timer = setInterval(() => { fetchDetail(true); }, 5000);
    return () => clearInterval(timer);
  }, [isDispatching, fetchDetail]);

  // 编辑工单（标题/描述/优先级/类型/联系人；权限与后端对齐：admin/创建人/处理人，终态不可编辑）
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState({ title: '', description: '', priority: '中', ticket_type: 'problem', project_id: '', project_name: '' });
  const [savingEdit, setSavingEdit] = useState(false);
  // 所属项目下拉（当前用户名下项目，GET /api/admin/projects/me；支持关键词模糊搜索）
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const [projectKeyword, setProjectKeyword] = useState('');
  const [projectOptions, setProjectOptions] = useState<ProjectItem[]>([]);
  const [projectLoading, setProjectLoading] = useState(false);
  const loadMyProjects = async () => {
    try { setProjectLoading(true); setProjectOptions(await getMyProjects()); }
    catch { setProjectOptions([]); }
    finally { setProjectLoading(false); }
  };
  // 当前工单的 project_id 可能不在“名下项目”里（异常工单/老数据），始终置顶保证回显可见
  const projectList = useMemo(() => {
    const base = [...projectOptions];
    if (editForm.project_id && !base.some((p) => p.project_code === editForm.project_id)) {
      base.unshift({ id: editForm.project_id, project_code: editForm.project_id, name: editForm.project_name || editForm.project_id });
    }
    return base;
  }, [projectOptions, editForm.project_id, editForm.project_name]);
  const filteredProjects = useMemo(() => {
    const kw = projectKeyword.trim().toLowerCase();
    if (!kw) return projectList;
    return projectList.filter((p) => p.name.toLowerCase().includes(kw) || p.project_code.toLowerCase().includes(kw));
  }, [projectList, projectKeyword]);
  const openProjectPicker = () => { setShowProjectPicker(true); if (projectOptions.length === 0) loadMyProjects(); };
  const selectProject = (p: ProjectItem) => {
    setEditForm((prev) => ({ ...prev, project_id: p.project_code, project_name: p.name }));
    setShowProjectPicker(false);
    setProjectKeyword('');
  };
  const canEdit = !!ticket?.ticket_id && !isTerminalTicketStatus(ticket.status)
    && (isAdmin || username === ticket.created_by || username === ticket.assigned_to);
  const openEdit = () => {
    if (!ticket) return;
    setEditForm({
      title: ticket.title || '',
      description: ticket.description || '',
      priority: toEnPriority(ticket.priority),
      ticket_type: ticket.type || 'problem',
      project_id: ticket.project_id || '',
      project_name: ticket.project_name || ticket.project || '',
    });
    setShowEdit(true);
  };
  const handleEditSave = async () => {
    if (!ticket?.ticket_id) return;
    if (!editForm.title.trim()) { Toast({ message: '标题不能为空', theme: 'warning' }); return; }
    setSavingEdit(true);
    try {
      await request(`/${ticket.ticket_id}`, {
        method: 'PUT',
        body: JSON.stringify({
          title: editForm.title.trim(),
          description: editForm.description,
          priority: editForm.priority,
          ticket_type: editForm.ticket_type,
          project_name: editForm.project_name,
          project_id: editForm.project_id,
        }),
      });
      Toast({ message: '已保存', theme: 'success' });
      setShowEdit(false);
      await fetchDetail(true);  // 静默刷新，展示端以 DB 为准，编辑后立即可见（await 确保 DB 新值落定）
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSavingEdit(false);
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



  // 附件下载/预览（复用系统任务页同款逻辑；AI 上传的附件落在同一 MinIO 路径体系）
  const buildAttachmentDownloadUrl = (att: NormalizedAttachment): string | null => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) return null;
    let minioPath = rawPath;
    if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
      const parsed = parseMinioPath(rawPath);
      if (!parsed) return null;
      minioPath = `${parsed.bucket}/${parsed.objectKey}`;
    }
    const authToken = localStorage.getItem('auth_token') || '';
    return `${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;
  };

  const openAttachmentViewer = (att: NormalizedAttachment) => {
    const rawPath = att.path || att.url || '';
    if (!rawPath) { Toast({ message: '附件路径无效', theme: 'error' }); return; }
    let minioPath = rawPath;
    if (rawPath.startsWith('http://') || rawPath.startsWith('https://')) {
      const parsed = parseMinioPath(rawPath);
      if (!parsed) { Toast({ message: '无法解析附件路径', theme: 'error' }); return; }
      minioPath = `${parsed.bucket}/${parsed.objectKey}`;
    }
    const previewUrl = `${API_CONFIG.TASKS.BASE_URL}/files/${encodeURIComponent(minioPath)}`;
    setViewer({
      filename: att.filename || '未命名文件',
      size: att.size,
      previewUrl,
      downloadUrl: buildAttachmentDownloadUrl(att) || previewUrl,
    });
  };

  return (
    <div className="ticket-detail-page" style={{ paddingBottom: 72 }}>
      <Navbar
        title="工单详情"
        fixed
        leftArrow
        onLeftClick={() => navigate('/call', { state: { showHistory: true } })}
      />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {/* 标题 + 基本信息 */}
        <div className="detail-card">
          <div className="detail-card__meta">
            {ticket.type && <Tag theme="primary">{TYPE_LABEL[ticket.type] || ticket.type}</Tag>}
            {ticket.priority && <Tag theme="warning">{displayPriority(ticket.priority)}</Tag>}
            <Tag
              theme="primary"
              style={{ background: getStatusColor(ticket.status || ''), color: '#fff', border: 'none', fontWeight: 500 }}
            >
              {STATUS_DISPLAY_MAP[(ticket.status || '').toLowerCase()] || ticket.status || '待派单'}
            </Tag>
            <span className="detail-card__id">{ticket.ticket_id || ''}</span>
          </div>
          <h2 className="detail-card__title">{ticket.title || '(无标题)'}</h2>
          {(ticket.project_name || ticket.project) && <DetailRow label="所属项目" value={ticket.project_name || ticket.project || ''} />}
          {ticket.contact && <DetailRow label="联系人" value={ticket.contact} />}
          <DetailRow label="创建时间" value={ticket.created_at ? formatDateTime(typeof ticket.created_at === 'number' ? new Date(ticket.created_at * 1000).toISOString() : String(ticket.created_at)) : ''} />
        </div>

        {/* 人员流转：发起人 → 处理人（与历史工单列表页同款 task-card2__people 样式）
            人员信息来自 tasks 服务 GET /{ticket_id}；AI 未生成工单(无 ticket_id)时该字段缺失则不显示。
            派单中（status=new 且处理人未写入）：处理人位显示「派单中」呼吸动效，5s 轮询自动更新 */}
        {(ticket.created_by || ticket.created_by_name || ticket.assigned_to || ticket.assigned_to_name || isDispatching) && (
          <div className="detail-card">
            <div className="task-card2__people">
              <div className="task-card2__person task-card2__person--creator" title={`发起人：${ticket.created_by_name || ticket.created_by || '-'}`}>
                <span className="task-card2__avatar">{(ticket.created_by_name || ticket.created_by || '?').slice(0, 1).toUpperCase()}</span>
                <span className="task-card2__person-text">
                  <span className="task-card2__person-label">发起人</span>
                  <span className="task-card2__person-name">{ticket.created_by_name || ticket.created_by || '-'}</span>
                </span>
              </div>
              <span className="task-card2__person-arrow">➡️</span>
              {isDispatching ? (
                <div className="task-card2__person task-card2__person--assignee" title="U老师 正在派单，稍候自动更新">
                  <span className="task-card2__avatar task-card2__avatar--assignee task-card2__avatar--dispatching"><i className="dispatch-pulse" /></span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">处理人</span>
                    <span className="task-card2__person-name task-card2__person-name--dispatching">派单中</span>
                  </span>
                </div>
              ) : (
                <div className="task-card2__person task-card2__person--assignee" title={`处理人：${ticket.assigned_to_name || ticket.assigned_to || '-'}`}>
                  <span className="task-card2__avatar task-card2__avatar--assignee">{(ticket.assigned_to_name || ticket.assigned_to || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">处理人</span>
                    <span className="task-card2__person-name">{ticket.assigned_to_name || ticket.assigned_to || '-'}</span>
                  </span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* 问题描述 */}
        {ticket.description && (
          <div className="detail-card">
            <h4 className="detail-card__h">问题描述</h4>
            <div style={{ whiteSpace: 'pre-wrap', color: '#333', fontSize: 14, lineHeight: 1.7 }}>{ticket.description}</div>
          </div>
        )}



        {/* 工单附件（图片/PDF/Markdown 预览、其它格式下载；复用统一 AttachmentViewer，与系统任务页一致）*/}
        {ticket.attachments && ticket.attachments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">📎 附件 ({ticket.attachments.length})</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {ticket.attachments.map((rawAtt, idx) => {
                const att = normalizeAttachment(rawAtt);
                if (!att) return null;
                const url = att.path || att.url || '';
                const filename = att.filename || '未命名文件';
                const size = att.size ?? 0;
                const isImg = isImageFile(filename);
                const icon = getFileIcon(filename);
                const sizeLabel = formatFileSize(size);
                const dl = buildAttachmentDownloadUrl(att);
                return (
                  <div
                    key={idx}
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
                    }}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = '#f0f7ff'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = '#fafafa'; }}
                  >
                    {isImg && url ? (
                      <img
                        src={url}
                        alt={filename}
                        style={{ width: 40, height: 40, objectFit: 'cover', borderRadius: 4, marginRight: 10, flexShrink: 0 }}
                        onError={(e) => { (e.currentTarget as HTMLElement).style.display = 'none'; }}
                      />
                    ) : (
                      <span style={{ fontSize: 22, marginRight: 10, flexShrink: 0 }}>{icon}</span>
                    )}
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
                      onClick={(e) => {
                        e.stopPropagation();
                        if (dl) {
                          const a = document.createElement('a');
                          a.href = dl;
                          a.download = filename;
                          a.target = '_blank';
                          document.body.appendChild(a);
                          a.click();
                          document.body.removeChild(a);
                        } else {
                          Toast({ message: '附件路径无效', theme: 'error' });
                        }
                      }}
                      style={{ fontSize: 16, color: '#0052d9', marginLeft: 8, flexShrink: 0, cursor: 'pointer' }}
                    >
                      ⬇
                    </span>
                  </div>
                );
              })}
            </div>
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

        {/* 操作：与历史工单列表页完全一致 —— 终态（已解决/已取消/已关闭）整组不显示；
            新建/待处理可催办、撤回；处理中仅可上报；不可用按钮禁用；
            正在操作的按钮单独禁用（acting 标记当前动作） */}
        {!isTerminalTicketStatus(ticket.status) && (
          <div className="detail-actions__btns">
            <Button
              size="small" variant="outline" theme="default" icon={<NotificationIcon />}
              disabled={!canUrgeTicket(ticket.status) || acting === 'urge'}
              title={canUrgeTicket(ticket.status) ? undefined : '仅新建/待处理工单可催办'}
              onClick={() => openActionPopup('urge')}
            >催办</Button>
            <Button
              size="small" variant="outline" theme="default" icon={<UploadIcon />}
              disabled={!canReportTicket(ticket.status) || acting === 'report'}
              title={canReportTicket(ticket.status) ? undefined : '仅处理中工单可上报'}
              onClick={() => openActionPopup('report')}
            >上报</Button>
            <Button
              size="small" variant="outline" theme="default" icon={<RollbackIcon />}
              disabled={!canCancelTicket(ticket.status) || acting === 'cancel'}
              title={canCancelTicket(ticket.status) ? undefined : '仅新建/待处理工单可撤回'}
              onClick={handleCancel}
            >撤回</Button>
            {canEdit && (
              <Button
                size="small" variant="outline" theme="primary" icon={<EditIcon />}
                disabled={savingEdit}
                onClick={openEdit}
              >编辑</Button>
            )}
          </div>
        )}

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
            <Button block theme="primary" loading={!!acting} onClick={handleActionConfirm}>确定</Button>
          </div>
        </div>
      </Popup>

      {/* 编辑工单弹窗（标题/描述/优先级/类型/联系人；复用系统任务页 ticket-edit-form 样式） */}
      <Popup visible={showEdit} onClose={() => setShowEdit(false)} placement="bottom" showOverlay>
        <div className="ticket-edit-form">
          <div className="ticket-edit-form__header">
            <span className="ticket-edit-form__title">编辑工单</span>
            <span className="ticket-edit-form__close" onClick={() => setShowEdit(false)}>×</span>
          </div>
          <div className="ticket-edit-form__body">
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">标题</label>
              <Input
                value={editForm.title}
                onChange={(v) => setEditForm((p) => ({ ...p, title: String(v) }))}
                placeholder="请输入工单标题"
                clearable
              />
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">问题描述</label>
              <Textarea
                value={editForm.description}
                onChange={(v) => setEditForm((p) => ({ ...p, description: String(v) }))}
                placeholder="请详细描述问题..."
                autosize={{ minRows: 4, maxRows: 10 }}
                maxlength={2000}
              />
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">优先级</label>
              <div className="tasks-create-modal__radio-group">
                {(['低', '中', '高', '紧急'] as const).map((label) => (
                  <button
                    key={label}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${editForm.priority === PRIORITY_EN[label] ? 'is-active' : ''}`}
                    onClick={() => setEditForm((prev) => ({ ...prev, priority: PRIORITY_EN[label] }))}
                  >{label}</button>
                ))}
              </div>
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">类型</label>
              <div className="tasks-create-modal__radio-group">
                {Object.entries(TYPE_LABEL).map(([k, v]) => (
                  <button
                    key={k}
                    type="button"
                    className={`tasks-create-modal__radio-btn ${editForm.ticket_type === k ? 'is-active' : ''}`}
                    onClick={() => setEditForm((prev) => ({ ...prev, ticket_type: k }))}
                  >{v}</button>
                ))}
              </div>
            </div>
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">所属项目</label>
              <div className="ticket-edit-form__select" onClick={openProjectPicker}>
                <span className={editForm.project_name ? 'ticket-edit-form__select-value' : 'ticket-edit-form__select-value is-placeholder'}>
                  {editForm.project_name || '请选择所属项目'}
                </span>
                <span className="ticket-edit-form__select-arrow">▾</span>
              </div>
            </div>
          </div>
          <div className="ticket-edit-form__footer">
            <Button theme="default" block onClick={() => setShowEdit(false)}>取消</Button>
            <Button theme="primary" block loading={savingEdit} onClick={handleEditSave}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 所属项目选择弹层：当前用户名下项目 + 关键词模糊搜索 */}
      <Popup visible={showProjectPicker} placement="bottom" onClose={() => setShowProjectPicker(false)} showOverlay>
        <div className="project-picker">
          <div className="project-picker__header">
            <span className="project-picker__title">选择所属项目</span>
            <span className="project-picker__close" onClick={() => setShowProjectPicker(false)}>×</span>
          </div>
          <div className="project-picker__search">
            <Input
              value={projectKeyword}
              onChange={(v) => setProjectKeyword(String(v))}
              placeholder="搜索项目名称 / 编码"
              clearable
            />
          </div>
          <div className="project-picker__list">
            {projectLoading ? (
              <div className="project-picker__empty">加载中...</div>
            ) : filteredProjects.length === 0 ? (
              <div className="project-picker__empty">无匹配项目</div>
            ) : (
              filteredProjects.map((p) => (
                <div
                  key={p.project_code}
                  className={`project-picker__item ${editForm.project_id === p.project_code ? 'is-active' : ''}`}
                  onClick={() => selectProject(p)}
                >
                  <span className="project-picker__name">{p.name}</span>
                  <span className="project-picker__code">{p.project_code}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </Popup>

      {/* 附件预览：图片灯箱 / PDF 内联 / Markdown 渲染（与系统任务详情页共用统一组件）*/}
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
