import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog, Form, FormItem } from 'tdesign-mobile-react';
import AppButton from '@/shared/components/AppButton';
import { User, UserCheck, Folder, AlarmClock, Clock, RefreshCw, Building2, Store, Download, FileImage, FileText, FileSpreadsheet, FileCode, FileArchive, Paperclip, Bot } from 'lucide-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import ClearableInput from '@/shared/components/ClearableInput';
import TitleEllipsis from '@/shared/components/TitleEllipsis';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { createRequest, getToken } from '@/api/client';
import API_CONFIG from '@/config/api';
import SafeHtml from '@/shared/components/SafeHtml';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { uploadCommentAttachment, getOperationLogs, formatDuration, type OperationLog as TicketOperationLog } from '@/api/ticket';
import { TICKET_TYPE_DISPLAY_MAP, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP, canEditPriority } from '@/shared/constants/ticket';
import { isSameUser } from '@/shared/utils/userIdentity';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime, parseDeadlineString } from '@/shared/utils/deadline';
import { formatDateTime, formatRawDateTime, parseUtcDate } from '@/shared/utils/url';
import { fetchWithAuth } from '@/api/ai';
import { getProjectMembers } from '@/api/projects';
import type { ProjectMember } from '@/api/projects';
import { dedupeFileNames } from '@/shared/utils/uniqueFileNames';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { urlTransformAllowDataImage } from '@/shared/utils/markdown';

// 状态文字色（设计稿 statusText 蓝阶：新建 blue-3 / 处理中·进行中 blue-2 / 已解决 blue-1 / 关闭·取消 muted）
const STATUS_TEXT_COLOR_MAP: Record<string, string> = {
  new: 'var(--blue-3)',
  in_progress: 'var(--blue-2)',
  pending: 'var(--blue-2)',
  paused: 'var(--blue-2)',
  resolved: 'var(--blue-1)',
  closed: 'var(--muted-foreground)',
  canceled: 'var(--muted-foreground)',
  cancelled: 'var(--muted-foreground)',
};

const getStatusTextColor = (status: string): string => {
  const key = (status || '').toLowerCase();
  return STATUS_TEXT_COLOR_MAP[key] || 'var(--muted-foreground)';
};

const formatFileSize = (bytes?: number): string => {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
};

// 文件类型图标（lucide，与设计稿图标体系一致）
const FileTypeIcon = ({ filename, size = 22 }: { filename?: string; size?: number }) => {
  const ext = (filename || '').split('.').pop()?.toLowerCase() || '';
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg'];
  const docExts = ['pdf', 'doc', 'docx', 'txt', 'md'];
  const sheetExts = ['xls', 'xlsx', 'csv'];
  const codeExts = ['json', 'js', 'ts', 'py', 'html', 'css'];
  const archiveExts = ['zip', 'rar', '7z', 'tar', 'gz'];
  const props = { size, strokeWidth: 1.8 } as const;
  if (imageExts.includes(ext)) return <FileImage {...props} />;
  if (docExts.includes(ext)) return <FileText {...props} />;
  if (sheetExts.includes(ext)) return <FileSpreadsheet {...props} />;
  if (codeExts.includes(ext)) return <FileCode {...props} />;
  if (archiveExts.includes(ext)) return <FileArchive {...props} />;
  return <Paperclip {...props} />;
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
type OperationLog = TicketOperationLog;
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; attachments?: Array<string | { path?: string; filename?: string; size?: number }>; reply_to?: string | number; quoted?: { id: string | number; content: string; created_by_name?: string }; }
interface Ticket {
  id: string; title: string; description: string; status: string; priority: string;
  ticket_type: string; project_name?: string; project_id?: string;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  reporter_name?: string; assignee_name?: string;
  contact?: string; customer?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; metadata_info?: Record<string, unknown>; comments?: Comment[];
  // tasks 详情接口 GET /{id} 返回蛇形 deadline_at（见 TicketResponse）
  deadline_at?: string | null;
  // 二次派单感知增强（M3）：未派到指定人时的完整话术（详情页 redispatch.result.tip_detail）
  redispatch?: { result?: { tip_detail?: string | null } } | null;
  // 工单阶段性处理（协商节点）：当前节点 ID/名称/结束时间（naive UTC）
  curr_step_id?: number | null;
  curr_step_name?: string | null;
  curr_step_endtime?: string | null;
  // 回合制：最近一次改 step 的操作人侧标识（assigned/creator）与回合数
  step_last_updated_by?: 'assigned' | 'creator' | null;
  step_last_updated_at?: string | null;
  step_negotiation_round?: number;
  step_neg_max_rounds?: number;
  // 当前协商节点是否已协商一致：确认同意 → True；进入新节点 / 协商节点时间 → 重置 False
  curr_step_agreed?: boolean;
  // 升级上报次数：>0 表示已升级，协商回合重置为1且不再受限
  escalate_count?: number;
}

// 协商阶段模板步骤（GET /{task_id}/steps 返回）
interface StepTemplate {
  id: number;
  step_name: string;
  sequence: number;
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
  const { username, userId, name, hasPermission } = useAuthStore();

  const [detail, setDetail] = useState<Ticket | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  // 二次派单感知增强（M3）：未派到指定人时的完整情商话术（详情页 redispatch.result.tip_detail）
  const [redispatchTipDetail, setRedispatchTipDetail] = useState<string>('');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<{ title: string; description: string; priority: string; ticket_type: string; deadline_at?: string }>({ title: '', description: '', priority: 'medium', ticket_type: 'problem' });
  // 最晚解决时间区间：基准 = 工单创建时间（detail.created_at），而非用户操作时刻
  const editDeadlineRange = getDeadlineRange(editForm.priority, detail?.created_at);
  // 优先级仅在「尚未派单」（新建/待派单）可修改；已派单及后续状态禁止（置灰不可点）
  const priorityDisabled = !canEditPriority(detail?.status);
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
  // 创建人姓名编辑（仅处理人/管理员可点击设置）
  const [showCreatorNamePopup, setShowCreatorNamePopup] = useState(false);
  const [creatorNameInput, setCreatorNameInput] = useState('');
  const [submittingCreatorName, setSubmittingCreatorName] = useState(false);
  // 最晚解决时间直接编辑（拥有 backend:tasks:operate 权限的用户可点击信息项改期）
  // deadlineDraft: undefined=未改动（保存时不提交该字段）；ISO 字符串=新时间；null=清除
  const [showDeadlinePopup, setShowDeadlinePopup] = useState(false);
  const [deadlineDraft, setDeadlineDraft] = useState<string | null | undefined>(undefined);
  const [submittingDeadline, setSubmittingDeadline] = useState(false);
  const [submittingComment, setSubmittingComment] = useState(false);
  const [askingAI, setAskingAI] = useState(false);

  // 结束工单确认弹窗：问题 + AI 解决方式
  const [showResolutionPopup, setShowResolutionPopup] = useState(false);
  const [resolutionText, setResolutionText] = useState('');
  const [resolutionLoading, setResolutionLoading] = useState(false);
  const [resolutionFailed, setResolutionFailed] = useState(false);
  const [resolutionPolling, setResolutionPolling] = useState(false);
  // AI 判定当前无解决方案（仅占位提示，不填入输入框）
  const [resolutionNoSolution, setResolutionNoSolution] = useState(false);
  // 标记是否已"确认完成"成功（成功后关闭弹窗不应清除草稿；取消/遮罩关闭才清除）
  const resolveConfirmedRef = useRef(false);
  // 轮询停止标志：取消/关闭时置 true，让异步轮询循环及时退出（state 无法中断 while 循环）
  const resolvePollStopRef = useRef(false);
  // 确认完成提交中（防重复提交）
  const [resolutionSubmitting, setResolutionSubmitting] = useState(false);

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

  // 工单阶段性处理（协商节点）
  const [stepTemplate, setStepTemplate] = useState<StepTemplate[]>([]);
  const [responding, setResponding] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [showNegotiateStepPopup, setShowNegotiateStepPopup] = useState(false);
  const [negotiateStepId, setNegotiateStepId] = useState<number | null>(null);
  const [negotiateEndTime, setNegotiateEndTime] = useState<string | null>(null);
  const [negotiateReason, setNegotiateReason] = useState('');
  const [submittingNegotiate, setSubmittingNegotiate] = useState(false);
  // 当前阶段完成弹窗：处理人选择下一阶段节点 + 节点结束时间
  const [showCompleteStepPopup, setShowCompleteStepPopup] = useState(false);
  const [completeNextStepId, setCompleteNextStepId] = useState<number | null>(null);
  const [completeNextEndTime, setCompleteNextEndTime] = useState<string | null>(null);
  const [submittingComplete, setSubmittingComplete] = useState(false);
  // 设置节点时间弹窗（已升级工单，处理人一锤定音）
  const [showSetStepTimePopup, setShowSetStepTimePopup] = useState(false);
  const [setStepTimeValue, setSetStepTimeValue] = useState<string | null>(null);
  const [submittingSetStepTime, setSubmittingSetStepTime] = useState(false);

  // 项目成员（用于讨论区 @ 提及）
  const [projectMembers, setProjectMembers] = useState<ProjectMember[]>([]);
  // 全部在职用户（项目成员 + 项目外，@ 输入过滤字时可 @ 到项目外的人）
  const [allUsers, setAllUsers] = useState<ProjectMember[]>([]);

  useEffect(() => {
    if (!detailId) { setDetail(null); return; }
    setDetailLoading(true);
    request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true })
      .then((t) => {
        setDetail(t);
        // 二次派单感知增强（M3）：未派到指定人时的完整话术
        setRedispatchTipDetail(t.redispatch?.result?.tip_detail || '');
        // 摘要存 metadata_info.ai_summary（不混入讨论区）
        const meta = t.metadata_info || {};
        setAiSummary(typeof meta.ai_summary === 'string' ? meta.ai_summary as string : '');

        // 拉取协商阶段模板（按工单 task_type），用于「工单阶段性处理」当前节点描述
        request<{ code: number; data: { steps: StepTemplate[] } }>(`/${detailId}/steps`)
          .then((res) => setStepTemplate(res?.data?.steps || []))
          .catch(() => setStepTemplate([]));

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
        // 获取全部在职用户（@ 输入过滤字时扩展到项目外的人）
        getProjectMembers(detailId, true)
          .then((u) => setAllUsers(u))
          .catch(() => setAllUsers([]));
      })
      .catch((err) => Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' }))
      .finally(() => setDetailLoading(false));
  }, [detailId]);

  // 加载工单操作日志（用于工单动态区域滚动展示）
  useEffect(() => {
    if (!detailId) return;
    getOperationLogs(detailId)
      .then((data) => setOpLogs(data || []))
      .catch(() => setOpLogs([]));
  }, [detailId]);

  // 查看停留时长追踪：用户离开页面 / 切后台时回传累计可见秒数
  // 后端将累加到最近一条 VIEW 操作记录上（5 分钟去重窗口内同一条记录）
  useEffect(() => {
    if (!detailId) return;

    let accumulated = 0;                     // 累计可见秒数
    let lastActive: number | null = Date.now(); // 当前可见周期起始时间戳
    let sent = false;                        // 当前可见周期是否已回传

    const flush = () => {
      if (lastActive !== null) {
        accumulated += (Date.now() - lastActive) / 1000;
        lastActive = null;
      }
      const seconds = Math.floor(accumulated);
      if (seconds <= 0 || sent) return;
      sent = true;

      const token = getToken();
      if (!token) return;
      // keepalive 保证页面卸载时请求仍能发出
      fetch(`${API_CONFIG.TASKS.BASE_URL}/${detailId}/view-end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ duration_seconds: seconds }),
        keepalive: true,
      }).catch(() => {});
    };

    const onVisibilityChange = () => {
      if (document.hidden) {
        // 切后台 / 最小化：立即回传当前可见周期时长
        flush();
      } else {
        // 恢复可见：开启新的可见周期（后端累加，不覆盖之前已回传的时长）
        lastActive = Date.now();
        accumulated = 0;
        sent = false;
      }
    };

    const onPageHide = () => flush();

    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pagehide', onPageHide);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pagehide', onPageHide);
      // SPA 内部导航（组件卸载）：回传剩余时长
      flush();
    };
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

  const getCurrentUserRoles = () => {
    const currentName = name || username;

    const isAssignee = !!(
      isSameUser(detail?.assigned_to, userId, username) ||
      (detail?.assignee_name && (detail.assignee_name === username || detail.assignee_name === currentName)) ||
      (detail?.assigned_to_name && (detail.assigned_to_name === username || detail.assigned_to_name === currentName))
    );

    const isReporter = !!(
      isSameUser(detail?.created_by, userId, username) ||
      (detail?.reporter_name && (detail.reporter_name === username || detail.reporter_name === currentName)) ||
      (detail?.created_by_name && (detail.created_by_name === username || detail.created_by_name === currentName))
    );

    return { isAssignee, isReporter };
  };

  // 拥有 backend:tasks:operate 权限的用户可点击「创建人」设置其姓名
  const canEditCreatorName = !!detail && hasPermission('backend:tasks:operate');

  const getActionButtons = () => {
    const status = detail?.status?.toLowerCase();
    if (!status) return [];

    const isClosed = status === 'closed';
    const isCanceled = status === 'canceled' || status === 'cancelled';
    if (isClosed || isCanceled) return [];

    const { isAssignee, isReporter } = getCurrentUserRoles();

    // 拥有 backend:tasks:operate 权限的用户，对所有活跃状态工单均可见且可操作
    const canOperate = hasPermission('backend:tasks:operate');

    const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
    if (assigneeOnlyStatuses.includes(status) && !isAssignee && !canOperate) return [];

    if (status === 'resolved' && !isReporter && !canOperate) return [];

    // 顶部操作按钮配色（设计稿 05：主推进 bg-primary 白字胶囊 / 次操作 bg-secondary 深字胶囊）
    const BTN_PRIMARY = { backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)', borderRadius: '999px', border: 'none' };
    const BTN_SECONDARY = { backgroundColor: 'var(--secondary)', color: 'var(--foreground)', borderRadius: '999px', border: 'none' };
    const actions: Record<string, { label: string; nextStatus: string; theme: string; actionType?: string; customStyle?: Record<string, string> }[]> = {
      // new 状态由处理人首次响应（协商节点时间/确认同意）自动转为 in_progress，不再提供「开始处理」按钮
      new: [],
      in_progress: [
        { label: '暂停任务', nextStatus: 'pending', theme: 'warning', customStyle: BTN_SECONDARY },
        { label: '处理完成', nextStatus: 'resolved', theme: 'success', customStyle: BTN_PRIMARY },
      ],
      pending: [{ label: '继续处理', nextStatus: 'in_progress', theme: 'primary', actionType: 'resume', customStyle: BTN_PRIMARY }],
      resolved: [
        { label: '未解决', nextStatus: 'in_progress', theme: 'warning', customStyle: BTN_SECONDARY },
        { label: '确认关闭', nextStatus: 'closed', theme: 'default', customStyle: BTN_PRIMARY },
      ],
      canceled: [{ label: '重新打开', nextStatus: 'new', theme: 'primary', customStyle: BTN_PRIMARY }],
    };

    return actions[status] || [];
  };

  const handleStatusChange = async (action: { nextStatus: string }) => {
    if (!detail) return;
    
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
    }
  };

  // 结束工单：轮询读取 metadata_info.resolution_summary（worker 后台生成后回填）
  const pollResolutionSummary = async (force = false) => {
    if (!detailId) return;
    let attempts = 0;
    const maxAttempts = 20; // 约 20 * 1.5s ≈ 30s 上限
    setResolutionPolling(true);

    // 重试时先带 force 强制重新入队（清除"无内容"标记）
    if (force) {
      try {
        await request<{ status: string; resolution_summary?: string }>(`/${detailId}/resolution-summary`, {
          method: 'POST',
          body: JSON.stringify({ force: true }),
          skipCache: true,
        });
      } catch {
        setResolutionLoading(false);
        setResolutionPolling(false);
        setResolutionFailed(true);
        return;
      }
    }

    while (attempts < maxAttempts) {
      attempts += 1;
      await new Promise((r) => setTimeout(r, 1500));
      // 取消/关闭时立即停止轮询
      if (resolvePollStopRef.current) {
        setResolutionPolling(false);
        return;
      }
      try {
        const res = await request<{ status: string; resolution_summary?: string }>(`/${detailId}/resolution-summary`, {
          method: 'POST',
          skipCache: true,
        });
        console.log(`[resolution-summary] 轮询第${attempts}次:`, res);
        // 已取消 → 不再回填，直接退出
        if (resolvePollStopRef.current) {
          setResolutionPolling(false);
          return;
        }
        const text = (res?.resolution_summary || '').trim();
        // 已生成完成（有值、无内容、或 empty）→ 结束轮询
        if (res?.status === 'empty') {
          // AI 判定无解决方案
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(true);
          setResolutionPolling(false);
          if (text) setResolutionText(text);
          return;
        }
        if (res?.status === 'done' || res?.status === 'confirmed') {
          if (text) {
            setResolutionText(text);
          }
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(false);
          setResolutionPolling(false);
          return;
        }
        // status === 'pending' → 仍在生成中，继续轮询
      } catch {
        // 单次请求失败继续轮询
      }
    }
    // 轮询超时仍未完成 → 置失败，提示用户手动补充/重试
    setResolutionLoading(false);
    setResolutionPolling(false);
    setResolutionFailed(true);
  };

  const handleResolveClick = async () => {
    if (!detail) return;
    // 打开弹窗：默认显示空输入框，由接单人手动填写或点击"帮我生成"触发 AI
    console.log('[resolution-summary] handleResolveClick 触发: task_id=', detail.id);
    resolvePollStopRef.current = false; // 重置轮询停止标志
    resolveConfirmedRef.current = false; // 重置确认标志（新一次打开）
    setShowResolutionPopup(true);
    setResolutionText('');
    setResolutionLoading(false);
    setResolutionFailed(false);
    setResolutionPolling(false);
    setResolutionNoSolution(false);
  };

  // 手动触发 AI 生成解决方式（点"帮我生成"时调用）
  const handleGenerateResolution = async () => {
    if (!detail) return;
    resolvePollStopRef.current = false;
    setResolutionLoading(true);
    setResolutionFailed(false);
    setResolutionPolling(false);
    setResolutionNoSolution(false);
    try {
      console.log('[resolution-summary] 手动触发 POST /' + detail.id + '/resolution-summary (force)');
      const res = await request<{ status: string; resolution_summary?: string }>(`/${detail.id}/resolution-summary`, {
        method: 'POST',
        body: JSON.stringify({ force: true }),
        skipCache: true,
      });
      console.log('[resolution-summary] 接口返回:', res);
      if (res) {
        const text = (res.resolution_summary || '').trim();
        if (text) {
          // 已有解决方式（worker 已生成/已确认）→ 直接填入
          setResolutionText(text);
          setResolutionLoading(false);
          setResolutionFailed(false);
        } else if (res.status === 'pending') {
          // 仍在生成中 → 轮询回读 worker 生成的草稿
          pollResolutionSummary();
        } else if (res.status === 'empty') {
          // AI 判定当前无解决方案 → 停止 loading，显示"当前没有解决方案"占位
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(true);
        } else if (res.status === 'done') {
          // worker 已完成但无内容（无资料）→ 停止 loading，placeholder 兜底
          setResolutionLoading(false);
          setResolutionFailed(false);
          setResolutionNoSolution(false);
        } else {
          // 入队失败/异常 → 兜底：提示用户补充
          setResolutionLoading(false);
          setResolutionFailed(true);
        }
      } else {
        setResolutionLoading(false);
        setResolutionFailed(true);
      }
    } catch {
      setResolutionLoading(false);
      setResolutionFailed(true);
    }
  };

  const handleRetryResolution = () => {
    // 重试：重置停止标志，强制重新触发生成 + 轮询
    resolvePollStopRef.current = false;
    setResolutionFailed(false);
    setResolutionLoading(true);
    setResolutionText('');
    setResolutionNoSolution(false);
    pollResolutionSummary(true);
  };

  // 取消：停止轮询 + 清掉已保存的解决方式草稿，关闭弹窗（下次点击重新生成）
  const handleResolveCancel = async () => {
    // 先停止任何进行中的轮询
    resolvePollStopRef.current = true;
    setResolutionPolling(false);
    setResolutionLoading(false);
    if (resolveConfirmedRef.current) {
      // 已确认完成成功，关闭弹窗但不清除草稿
      setShowResolutionPopup(false);
      resolveConfirmedRef.current = false;
      return;
    }
    setShowResolutionPopup(false);
    try {
      if (detail?.id) {
        await request<{ status: string }>(`/${detail.id}/resolution-summary`, {
          method: 'POST',
          body: JSON.stringify({ clear: true }),
          skipCache: true,
        });
      }
    } catch {
      // 清除失败不影响：下次点击仍会重新生成
    }
  };

  const handleConfirmResolve = async () => {
    if (!detail) return;
    const finalText = resolutionText.trim();
    if (!finalText) {
      Toast({ message: '请填写解决方式', theme: 'warning' });
      return;
    }
    if (resolutionSubmitting) return;
    setResolutionSubmitting(true);
    try {
      await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'resolved', resolution_summary: finalText }),
      });
      refreshTasks();
      await refreshDetail();
      resolveConfirmedRef.current = true; // 标记已确认，关闭时不清除草稿
      setShowResolutionPopup(false);
      setResolutionText('');
      Toast({ message: '工单已处理完成', theme: 'success' });
    } catch (err) {
      Toast({ message: `处理完成失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setResolutionSubmitting(false);
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
          body: JSON.stringify({ assigned_to: resumeUser.id || resumeUser.username, operation_type: 'reassign' }),
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
          assigned_to: escalateUser.id || escalateUser.username,
          operation_type: 'escalate',
        }),
      });

      // 将升级原因记录为评论（系统评论只记录操作本身，不包含原因）
      try {
        await request(`/${t.id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: `升级原因：${escalateReason.trim()}`, is_public: true }),
        });
      } catch {
        // 评论写入失败不阻断主流程，工单状态已变更
      }

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

      // 将退回原因记录为评论（系统评论只记录操作本身，不包含原因）
      try {
        await request(`/${detail.id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: `退回原因：${returnReason.trim()}`, is_public: true }),
        });
      } catch {
        // 评论写入失败不阻断主流程，工单状态已变更
      }

      await refreshDetail();
      Toast({ message: `已退回工单，处理人变更为 ${returnTo}`, theme: 'success' });
      setReturnReason('');
      setShowReturnConfirmPopup(false);
    } catch (err) {
      Toast({ message: `退回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // 首次响应（确认同意）：确认当前协商节点，工单 new → in_progress
  const handleRespond = async () => {
    if (!detail) return;
    setResponding(true);
    try {
      await request(`/${detail.id}/respond`, {
        method: 'POST',
        body: JSON.stringify({ curr_step_id: detail.curr_step_id ?? null }),
      });
      await refreshDetail();
      Toast({ message: '已确认协商节点，开始处理', theme: 'success' });
    } catch (err) {
      Toast({ message: `响应失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setResponding(false);
    }
  };

  // 当前阶段完成：处理人选择下一阶段节点 + 节点结束时间，提交后回合交给创建人
  const handleStepComplete = async () => {
    if (!detail) return;
    if (!completeNextStepId) {
      Toast({ message: '请选择下一阶段', theme: 'warning' });
      return;
    }
    if (!completeNextEndTime) {
      Toast({ message: '请选择下一阶段结束时间', theme: 'warning' });
      return;
    }
    setSubmittingComplete(true);
    setCompleting(true);
    try {
      await request(`/${detail.id}/complete-step`, {
        method: 'POST',
        body: JSON.stringify({
          next_step_id: completeNextStepId,
          curr_step_endtime: completeNextEndTime,
        }),
      });
      await refreshDetail();
      Toast({ message: '已推进到下一阶段，等待创建人确认', theme: 'success' });
      setShowCompleteStepPopup(false);
      setCompleteNextStepId(null);
      setCompleteNextEndTime(null);
    } catch (err) {
      Toast({ message: `操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingComplete(false);
      setCompleting(false);
    }
  };

  // 设置节点时间（已升级工单，处理人一锤定音）
  const handleSetStepTime = async () => {
    if (!detail) return;
    if (!setStepTimeValue) {
      Toast({ message: '请选择节点时间', theme: 'warning' });
      return;
    }
    setSubmittingSetStepTime(true);
    try {
      await request(`/${detail.id}/set-step-time`, {
        method: 'POST',
        body: JSON.stringify({ curr_step_endtime: setStepTimeValue }),
        headers: { 'Content-Type': 'application/json' },
      });
      Toast({ message: '已设置节点时间', theme: 'success' });
      setShowSetStepTimePopup(false);
      setSetStepTimeValue(null);
      await refreshDetail();
    } catch (e: any) {
      Toast({ message: e?.message || '设置失败', theme: 'error' });
    } finally {
      setSubmittingSetStepTime(false);
    }
  };

  // 协商节点：可调整节点（前/后均可）+ 设置节点结束时间，理由必填
  const handleNegotiateStep = async () => {
    if (!detail) return;
    if (!negotiateEndTime) {
      Toast({ message: '请选择协商节点时间', theme: 'warning' });
      return;
    }
    if (!negotiateReason.trim()) {
      Toast({ message: '请填写协商理由', theme: 'warning' });
      return;
    }
    setSubmittingNegotiate(true);
    try {
      await request(`/${detail.id}/negotiate-step`, {
        method: 'POST',
        body: JSON.stringify({
          curr_step_endtime: negotiateEndTime,
          curr_step_id: negotiateStepId,
          reason: negotiateReason.trim(),
        }),
      });
      await refreshDetail();
      Toast({ message: '协商节点已更新', theme: 'success' });
      setNegotiateReason('');
      setNegotiateEndTime(null);
      setNegotiateStepId(null);
      setShowNegotiateStepPopup(false);
    } catch (err) {
      Toast({ message: `设置失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingNegotiate(false);
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
        body: JSON.stringify({ assigned_to: reassignUser.id || reassignUser.username, operation_type: 'reassign' }),
      });

      // 将重新指派原因记录为评论（系统评论只记录操作本身，不包含原因）
      try {
        await request(`/${detail.id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: `重新指派原因：${reassignReason.trim()}`, is_public: true }),
        });
      } catch {
        // 评论写入失败不阻断主流程，工单状态已变更
      }

      await refreshDetail();
      Toast({ message: `已重新指派给 ${target}`, theme: 'success' });
      setReassignUser(null);
      setReassignReason('');
      setShowReassignPopup(false);
    } catch (err) {
      Toast({ message: `重新指派失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  // 设置创建人姓名：通过工单 created_by 反查用户表更新 user.name（不改变 created_by）
  const handleUpdateCreatorName = async () => {
    if (!detail) return;
    const newName = creatorNameInput.trim();
    if (!newName) {
      Toast({ message: '姓名不能为空', theme: 'warning' });
      return;
    }
    setSubmittingCreatorName(true);
    try {
      await request(`/${detail.id}/creator-name`, {
        method: 'PATCH',
        body: JSON.stringify({ name: newName }),
        skipCache: true,
      });
      await refreshDetail();
      Toast({ message: '创建人姓名已更新', theme: 'success' });
      setShowCreatorNamePopup(false);
      setCreatorNameInput('');
    } catch (err) {
      Toast({ message: `更新失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingCreatorName(false);
    }
  };

  // 修改最晚解决时间：拥有 backend:tasks:operate 权限的用户直接改期（PUT /{id}）
  // deadlineDraft 为 undefined（用户未改动）时不提交 deadline_at 字段，避免误清除
  const handleUpdateDeadline = async () => {
    if (!detail) return;
    setSubmittingDeadline(true);
    try {
      const body: Record<string, unknown> = { operation_type: 'update' };
      if (deadlineDraft !== undefined) body.deadline_at = deadlineDraft;
      await request<Ticket>(`/${detail.id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
        skipCache: true,
      });
      await refreshDetail();
      Toast({ message: '最晚解决时间已更新', theme: 'success' });
      setShowDeadlinePopup(false);
    } catch (err) {
      Toast({ message: `更新失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingDeadline(false);
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
    // 必须拼成绝对 URL：微信内 window.open(相对URL) 打开的是微信内置 WebView，无法下载；
    // 用户「在浏览器打开」后相对路径在外部浏览器解析失败会落到 SPA 404 → 未登录重定向微信 OAuth
    // （表现为「提示跳转到微信客户端」）。绝对 URL 携带 token，在外部浏览器可直接下载。
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;
  };

  const handleAttachmentDownload = async (att: Attachment, idx: number) => {
    const downloadUrl = buildAttachmentDownloadUrl(att);
    if (!downloadUrl) { Toast({ message: '附件路径无效', theme: 'error' }); return; }

    setDownloadingIdx(idx);
    try {
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
    setViewer({
      filename: att.filename || '未命名文件',
      size: att.size,
      previewUrl,
      downloadUrl: buildAttachmentDownloadUrl(att) || previewUrl,
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

  // ── 最晚解决时间（截止时间）：编辑弹窗用 antd DatePicker 下拉选择（双端可用）──
  // 浮层 z-index 通过 styles.popup.root 提到高于 tdesign 编辑弹窗（z-index 11500），避免被遮挡



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
      // 上传附件（同名文件自动改名，避免后端对象名重复覆盖）
      const tempId = generateTempId();
      const uploads = dedupeFileNames(files);
      for (const f of uploads) {
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
      // 上传附件（同名文件自动改名，避免后端对象名重复覆盖）
      const tempId = generateTempId();
      const uploads = dedupeFileNames(files);
      for (const f of uploads) {
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
          // 去掉文本中任意位置的 @U老师 标记（可能有空格/重复），保留整段话作为 query，
          // 兼容"先说话、句尾@U老师"的场景（否则 @U老师 在尾部时 query 会带残留或丢失）
          query: userMsg.replace(/\s*@U老师\s*/g, ' ').trim(),
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

  // ── onSend：检测是否 @U老师（任意位置，前缀或句尾均触发）决定走普通评论还是 AI 讨论 ──
  const handleSendComment = async (text: string, files: File[], options?: { replyTo?: string | number }): Promise<boolean> => {
    // 只要文本里含 @U老师（@ 在开头/中间/结尾都算）就走 AI 讨论；
    // 兼容"说完话后句尾手动@U老师"（否则会被当成普通评论发出、AI 不回复）
    if (text.includes('@U老师')) {
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
        // 二次派单感知增强（M3）：未派到指定人时的完整话术（与「我要摇人」历史详情同口径）
        setRedispatchTipDetail(t.redispatch?.result?.tip_detail || '');
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
              {/* 状态胶囊（设计稿 statusText：bg-secondary + 蓝阶文字） */}
              <Tag
                theme="default"
                style={{
                  background: 'var(--secondary)',
                  color: getStatusTextColor(detail.status),
                  border: 'none',
                  fontWeight: 600,
                  fontSize: 11.5,
                  borderRadius: 999,
                }}
              >
                {STATUS_DISPLAY_MAP[detail.status?.toLowerCase()] || detail.status}
              </Tag>
              {/* 类型胶囊（设计稿：bg-secondary muted） */}
              <Tag
                theme="default"
                style={{
                  background: 'var(--secondary)',
                  color: 'var(--muted-foreground)',
                  border: 'none',
                  fontWeight: 500,
                  fontSize: 11.5,
                  borderRadius: 999,
                }}
              >
                {TICKET_TYPE_DISPLAY_MAP[detail.ticket_type] || detail.ticket_type || '其他'}
              </Tag>
              <span className="detail-card__id" onClick={() => copyId(detail.id)}>#{detail.id}</span>
            </div>
            <div className="detail-card__action-btns">
              {getActionButtons().map((action, index) => (
                <AppButton
                  key={index}
                  size="small"
                  theme={action.theme as 'primary' | 'default' | 'danger' | 'light'}
                  onClick={() => {
                    if (action.actionType === 'resume') {
                      setShowResumePopup(true);
                    } else if (action.nextStatus === 'resolved') {
                      // 结束工单（→ resolved）→ 打开 "问题 + AI 解决方式" 确认弹窗
                      handleResolveClick();
                    } else {
                      handleStatusChange(action);
                    }
                  }}
                  className="detail-card__action-btn"
                  style={action.customStyle}
                >
                  {action.label}
                </AppButton>
              ))}
            </div>
          </div>
          <h2 className="detail-card__title">
            <TitleEllipsis text={detail.title} lines={3} titleClassName="detail-card__title-inner" as="span" fontSize={19} lineHeight={1.3} />
          </h2>
          <div className="detail-card__info-grid">
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><User size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">创建人</span>
                <span
                  className="detail-info-item__value"
                  style={canEditCreatorName ? { cursor: 'pointer', color: 'var(--blue-2)', textDecoration: 'underline' } : undefined}
                  onClick={canEditCreatorName ? () => {
                    setCreatorNameInput(detail.created_by_name || detail.reporter_name || detail.created_by || '');
                    setShowCreatorNamePopup(true);
                  } : undefined}
                >
                  {detail.created_by_name || detail.reporter_name || detail.created_by || '-'}
                </span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><UserCheck size={14} strokeWidth={2} /></span>
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
              <span className="detail-info-item__icon"><Folder size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">所属项目</span>
                <span className="detail-info-item__value">{detail.project_name || '-'}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><AlarmClock size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">当前阶段截止时间</span>
                <span className="detail-info-item__value">
                  {detail.deadline_at  ? formatRawDateTime(detail.deadline_at ) : '未设置'}
                </span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><Clock size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">创建时间</span>
                <span className="detail-info-item__value">{formatDateTime(detail.created_at)}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><RefreshCw size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">更新时间</span>
                <span className="detail-info-item__value">{formatDateTime(detail.updated_at)}</span>
              </div>
            </div>
          </div>

          {/* 二次派单感知增强（M3）：未派到指定人时的完整话术（与「我要摇人」历史详情同口径） */}
          {redispatchTipDetail && (
            <div className="redispatch-tip-detail">派单说明：{redispatchTipDetail}</div>
          )}
        </div>

        <div className="detail-card">
          <h4 className="detail-card__h">问题描述</h4>
          <SafeHtml html={detail.description || '<p style="color:var(--muted-foreground)">无描述</p>'} />
        </div>

        {/* 工单阶段性处理（协商节点）：当前节点描述 + 回合胶囊 + 操作按钮 */}
        {(() => {
          const status = (detail.status || '').toLowerCase();
          if (['resolved', 'closed', 'canceled', 'cancelled'].includes(status)) return null;
          const total = stepTemplate.length;
          const currIdx = stepTemplate.findIndex((s) => s.id === detail.curr_step_id);
          const stepName = detail.curr_step_name || (currIdx >= 0 ? stepTemplate[currIdx].step_name : '');
          const desc = stepName
            ? `当前节点：${stepName}${total > 0 ? `（${currIdx >= 0 ? currIdx + 1 : '-'} / ${total}）` : ''}`
            : '当前节点：尚未设置协商节点';
          const endtimeText = detail.curr_step_endtime ? formatRawDateTime(detail.curr_step_endtime) : '';
          const isProcessing = status === 'in_progress';
          const stepAgreed = !!detail.curr_step_agreed;  // 当前协商节点是否已协商一致
          const canRespond = !!detail.curr_step_id && (status === 'new' || (status === 'in_progress' && !stepAgreed));
          const canNegotiate = !!detail.curr_step_id;
          const currSeq = currIdx >= 0 ? stepTemplate[currIdx].sequence : null;
          const hasNext = currSeq === null ? true : stepTemplate.some((s) => s.sequence > currSeq);
          const openNegotiate = () => {
            setNegotiateStepId(detail.curr_step_id ?? null);
            setNegotiateEndTime(detail.curr_step_endtime ?? null);
            setNegotiateReason('');
            setShowNegotiateStepPopup(true);
          };
          const openCompleteStep = () => {
            // 默认选中紧邻的下一阶段（sequence > 当前节点 取第一个）
            const currSeqLocal = currIdx >= 0 ? stepTemplate[currIdx].sequence : null;
            const nextStep = currSeqLocal === null
              ? stepTemplate[0]
              : stepTemplate.find((s) => s.sequence > currSeqLocal);
            setCompleteNextStepId(nextStep ? nextStep.id : null);
            setCompleteNextEndTime(null);
            setShowCompleteStepPopup(true);
          };
          // 回合展示
          const round = detail.step_negotiation_round ?? 1;
          const maxRound = detail.step_neg_max_rounds ?? 5;
          const isEscalated = (detail.escalate_count ?? 0) > 0;
          const escalateCount = detail.escalate_count ?? 0;
          // 已升级上报后不再受回合上限限制
          const reachedMax = !isEscalated && round >= maxRound;
          const lastStepBy = detail.step_last_updated_by;
          // 轮到当前用户：assigned = 接单人回合；creator = 提单人回合；无值 = 默认接单人回合
          const { isAssignee, isReporter } = getCurrentUserRoles();
          // 操作按钮仅对工单创建人（isReporter）和处理人（isAssignee）可见
          const canOperate = isAssignee || isReporter;
          const myTurn = (!lastStepBy && isAssignee)
            || (lastStepBy === 'assigned' && isReporter)
            || (lastStepBy === 'creator' && isAssignee);
          // 回合胶囊样式
          let pillBg = 'rgba(100,116,139,0.15)';
          let pillColor = 'var(--muted-foreground)';
          if (round >= maxRound) { pillBg = 'rgba(220,38,38,0.15)'; pillColor = '#b91c1c'; }
          else if (round === maxRound - 1) { pillBg = 'rgba(234,179,8,0.2)'; pillColor = '#8a6400'; }
          else if (myTurn) { pillBg = 'rgba(37,99,235,0.15)'; pillColor = 'var(--blue-2)'; }
          const openEscalate = () => {
            setEscalateUser(null);
            setEscalateReason(`已达最大协商回合（${round}/${maxRound}），申请升级介入处理。`);
            setShowEscalatePopup(true);
          };
          const respondBtnDisabled = !canRespond || reachedMax;
          const negotiateDisabled = !canNegotiate || reachedMax;
          const completeDisabled = !hasNext || reachedMax;
          return (
            <div className="detail-card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  <h4 className="detail-card__h" style={{ marginBottom: 0 }}>工单阶段性处理</h4>
                  {myTurn && !reachedMax && !stepAgreed && (
                    <span style={{ fontSize: 12, color: 'var(--blue-2)', fontWeight: 500 }}>
                      ● 轮到你确认/答复
                    </span>
                  )}
                  {reachedMax && (
                    <span style={{ fontSize: 12, color: '#b91c1c', fontWeight: 500 }}>
                      ● 已达最大回合，请使用升级上报
                    </span>
                  )}
                  {isEscalated && (
                    <span style={{ fontSize: 12, color: '#92400e', fontWeight: 500 }}>
                      ● 已升级上报（第{escalateCount}次），协商不受回合限制
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                  <span
                    title={isEscalated ? `已升级上报（第${escalateCount}次），协商不受回合限制` : "协商回合：接单人↔提单人来回应答计数"}
                    style={{
                      display: 'inline-block', padding: '3px 10px', borderRadius: 999,
                      background: isEscalated ? '#fef3c7' : pillBg, color: isEscalated ? '#92400e' : pillColor, fontSize: 12, fontWeight: 500, lineHeight: 1.4,
                    }}
                  >
                    {isEscalated ? `已升级×${escalateCount} · 回合 ${round}` : `交涉回合 ${round} / ${maxRound}`}
                  </span>
                </div>
              </div>
              <div style={{ fontSize: 13, color: 'var(--foreground)', marginBottom: 12, lineHeight: 1.6 }}>
                当前节点描述：{desc}
                {endtimeText && (
                  <span style={{ color: 'var(--muted-foreground)', marginLeft: 8 }}>
                    节点时间：{endtimeText}
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {canOperate ? (
                  reachedMax ? (
                    // 最后一轮：直接显示升级上报按钮（替代管理员介入）
                    <Button block size="small" theme="danger" onClick={openEscalate}>升级上报</Button>
                  ) : stepAgreed ? (
                    // 协商一致阶段（curr_step_agreed=true）：仅处理人可完成当前阶段，创建人无操作按钮
                    isAssignee ? (
                      <Button
                        block
                        size="small"
                        theme="primary"
                        loading={completing}
                        disabled={completeDisabled}
                        onClick={openCompleteStep}
                      >
                        当前阶段完成
                      </Button>
                    ) : null
                  ) : (
                    // 未一致阶段（curr_step_agreed=false）：仅当前回合操作方可见按钮
                    myTurn ? (
                      <>
                        {isAssignee && (
                          <Button
                            block
                            size="small"
                            theme="default"
                            onClick={() => { setReassignUser(null); setReassignReason(''); setShowReassignPopup(true); }}
                          >
                            重新指派
                          </Button>
                        )}
                        {isAssignee && isEscalated && (
                          <Button
                            block
                            size="small"
                            theme="primary"
                            onClick={() => { setSetStepTimeValue(null); setShowSetStepTimePopup(true); }}
                          >
                            设置节点时间
                          </Button>
                        )}
                        <Button
                          block
                          size="small"
                          theme="default"
                          disabled={negotiateDisabled}
                          onClick={openNegotiate}
                        >
                          协商节点时间
                        </Button>
                        <Button
                          block
                          size="small"
                          theme="primary"
                          loading={responding}
                          disabled={respondBtnDisabled}
                          onClick={handleRespond}
                        >
                          确认同意
                        </Button>
                      </>
                    ) : null
                  )
                ) : (
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
                    仅工单创建人和处理人可执行操作
                  </span>
                )}
              </div>
            </div>
          );
        })()}

        {/* 公司/部门审核入口：仅管理员可见，工单 metadata_info 含 approval_type 时展示 */}
        {approvalInfo && username === 'admin' && (
          <div className="detail-card" style={{ border: '1.5px solid var(--blue-4)' }}>
            <h4 className="detail-card__h" style={{ color: 'var(--blue-2)', display: 'flex', alignItems: 'center', gap: 6 }}>
              {approvalInfo.type === 'new_company' ? <Building2 size={15} strokeWidth={2} /> : <Store size={15} strokeWidth={2} />}
              {approvalInfo.type === 'new_company' ? '新公司录入审核' : '新部门录入审核'}
            </h4>
            <div style={{ fontSize: 13, color: 'var(--foreground)', lineHeight: 1.8, marginBottom: 16 }}>
              <div>
                <strong>{approvalInfo.type === 'new_company' ? '公司名称' : '部门名称'}：</strong>
                {approvalInfo.targetName}
              </div>
              {approvalInfo.companyName && (
                <div><strong>所属公司：</strong>{approvalInfo.companyName}</div>
              )}
              <div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}>
                请审核以下信息，通过后该{approvalInfo.type === 'new_company' ? '公司' : '部门'}将对所有用户可见
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button
                block
                theme="primary"
                loading={approving}
                onClick={handleApprove}
                style={{ borderRadius: '999px', backgroundColor: 'var(--blue-3)', color: '#fff', border: 'none' }}
              >
                通过
              </Button>
              <Button
                block
                theme="default"
                variant="outline"
                disabled={approving}
                onClick={() => { setAdjustName(approvalInfo.targetName); setShowAdjustPopup(true); }}
                style={{ borderRadius: '999px' }}
              >
                调整名称
              </Button>
              <Button
                block
                theme="danger"
                variant="outline"
                disabled={approving}
                onClick={() => setShowRejectPopup(true)}
                style={{ borderRadius: '999px' }}
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
              return <p style={{ color: 'var(--muted-foreground)', fontSize: 12.5 }}>暂无动态</p>;
            }
            const formatTime = (ts: string) => {
              // 后端返回 naive datetime（UTC），需经 parseUtcDate 标记为 UTC 后由浏览器按本地时区自动 +8
              const d = parseUtcDate(ts);
              if (!d) return '';
              return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
            };
            const items = opLogs.map((l) => {
              // 查看记录追加停留时长
              const dur = l.operation_type === 'view' ? formatDuration(l.duration_seconds) : '';
              const suffix = dur ? `（停留 ${dur}）` : '';
              return {
                key: String(l.id),
                text: `${formatTime(l.created_at)} · ${l.description || l.operation_type}${suffix}`,
              };
            });
            // 复制一份用于无缝循环滚动
            const loopItems = [...items, ...items];
            const scrollStyle = { '--count': items.length } as React.CSSProperties;
            const scrollAttrs = items.length <= 3 ? { 'data-count-lte': '3' } : {};
            return (
              <div className="ticket-dynamics-scroll" style={scrollStyle} {...scrollAttrs}>
                <div className="ticket-dynamics-scroll__track">
                  {loopItems.map((it, i) => (
                    <div className="ticket-dynamics-scroll__item" key={`${it.key}-${i}`}>{it.text}</div>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>

        {(() => {
          const meta = detail.metadata_info || {};
          const rs = typeof meta.resolution_summary === 'string' ? meta.resolution_summary as string : '';
          const isTerminal = ['resolved', 'closed'].includes((detail.status || '').toLowerCase());
          if (!isTerminal || !rs.trim()) return null;
          return (
            <div className="detail-card">
              <h4 className="detail-card__h">解决方式</h4>
              <div style={{ color: 'var(--foreground)', fontSize: '12.5px', lineHeight: '24px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {rs}
              </div>
            </div>
          );
        })()}

        <div className="detail-card">
          <h4 className="detail-card__h">讨论摘要</h4>
          {aiSummary ? (
            <SafeHtml html={aiSummary} />
          ) : (
            <p style={{ color: 'var(--muted-foreground)', fontSize: 12.5, lineHeight: '24px' }}>暂无摘要，U老师 将自动总结讨论进展</p>
          )}
        </div>

        {detail.attachments && detail.attachments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">附件 ({detail.attachments.length})</h4>
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
                  {/* 非图片文件卡片（lucide 图标 + 文件名 + 下载，保留下载进度态） */}
                  {fileItems.length > 0 && (
                    <div className="detail-attachment-files">
                      {fileItems.map(({ att, origIdx }) => {
                        const filename = att.filename || '未命名文件';
                        const size = att.size ?? 0;
                        const sizeLabel = formatFileSize(size);
                        const isDownloading = downloadingIdx === origIdx;
                        return (
                          <div
                            key={`file-${origIdx}`}
                            role="button"
                            tabIndex={0}
                            className="detail-attachment-file"
                            style={{ opacity: isDownloading ? 0.6 : 1 }}
                            onClick={() => openAttachmentViewer(att)}
                            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') openAttachmentViewer(att); }}
                          >
                            <span className="detail-attachment-file__icon"><FileTypeIcon filename={filename} /></span>
                            <div className="detail-attachment-file__body">
                              <div className="detail-attachment-file__name">{filename}</div>
                              <div className="detail-attachment-file__size">{sizeLabel || '未知大小'}</div>
                            </div>
                            <span
                              role="button"
                              aria-label="下载附件"
                              title="下载"
                              className="detail-attachment-file__download"
                              onClick={(e) => { e.stopPropagation(); handleAttachmentDownload(att, origIdx); }}
                            >
                              {isDownloading ? <span className="detail-attachment-file__spinner" /> : <Download size={16} strokeWidth={2} />}
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
          optimisticAi={diagnosing || askingAI}
          enableAI
          enableAttach
          mentionUsers={projectMembers}
          mentionAllUsers={allUsers}
          taskId={detail?.id}
          onTaskUpdated={handleWsTaskUpdated}
          onMessagesClick={handleOpenReport}
          headerRight={
            <Button size="small" theme="primary" onClick={handleDiagnose} loading={diagnosing} icon={<Bot size={14} strokeWidth={2} />}>
              帮我分析
            </Button>
          }
        />

        {(() => {
          const status = detail.status?.toLowerCase();
          const isClosedOrCanceled = status === 'closed' || status === 'canceled' || status === 'cancelled';
          if (isClosedOrCanceled) return null;

          const { isAssignee, isReporter } = getCurrentUserRoles();
          const canOperate = hasPermission('backend:tasks:operate');
          const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
          const showRoleActions = canOperate || (assigneeOnlyStatuses.includes(status) ? isAssignee : (status === 'resolved' ? isReporter : false));
          // 达到最大回合：升级上报强制可见（提单人/接单人任一），替代管理员介入
          const round = detail.step_negotiation_round ?? 1;
          const maxRound = detail.step_neg_max_rounds ?? 5;
          const isEscalated = (detail.escalate_count ?? 0) > 0;
          const escalateCount = detail.escalate_count ?? 0;
          // 已升级上报后不再受回合上限限制
          const reachedMax = !isEscalated && round >= maxRound;
          const showEscalateAlone = reachedMax && (isAssignee || isReporter) && !showRoleActions;
          return (
            <div className="detail-actions">
              <div className="detail-actions__btns">
                {showRoleActions && (
                  <>
                    <Button size="small" theme="default" onClick={startEdit}>修改工单</Button>
                    <Button size="small" theme="default" onClick={() => { setReturnReason(''); setShowReturnConfirmPopup(true); }}>退回工单</Button>
                    <Button size="small" theme="default" onClick={() => { setReassignUser(null); setReassignReason(''); setShowReassignPopup(true); }}>重新指派</Button>
                    <Button
                      size="small"
                      theme={reachedMax ? 'danger' : 'default'}
                      onClick={() => {
                        setEscalateUser(null);
                        setEscalateReason(reachedMax ? `已达最大协商回合（${round}/${maxRound}），申请升级介入。` : '');
                        setShowEscalatePopup(true);
                      }}
                    >
                      {reachedMax ? '升级上报（回合超限）' : '升级上报'}
                    </Button>
                  </>
                )}
                {showEscalateAlone && (
                  <Button
                    size="small"
                    theme="danger"
                    onClick={() => {
                      setEscalateUser(null);
                      setEscalateReason(`已达最大协商回合（${round}/${maxRound}），申请升级介入。`);
                      setShowEscalatePopup(true);
                    }}
                  >
                    升级上报（回合超限）
                  </Button>
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
                    disabled={priorityDisabled}
                    title={priorityDisabled ? '仅新建工单可修改优先级' : undefined}
                    aria-label={priorityDisabled ? `优先级${label}（仅新建工单可修改优先级）` : `优先级${label}`}
                    className={`tasks-create-modal__radio-btn ${editForm.priority === value ? 'is-active' : ''} ${priorityDisabled ? 'is-disabled' : ''}`}
                    onClick={() => {
                      const r = getDeadlineRange(value, detail?.created_at);
                      setEditForm((p) => ({ ...p, priority: value, ...(r ? { deadline_at: r.max.toISOString() } : {}) }));
                    }}
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
            {/* 最晚解决时间：antd DatePicker 下拉选择（双端可用），浮层 z-index 高于编辑弹窗避免被遮挡 */}
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">最晚解决时间</label>
              <DatePicker
                style={{ width: '100%' }}
                placeholder="点击选择"
                format="YYYY-MM-DD HH:00"
                showTime={{ defaultValue: editDeadlineRange?.max ?? dayjs().hour(9).minute(0), format: 'HH:00', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={(trigger) => trigger.parentElement || document.body}
                value={editForm.deadline_at ? parseDeadlineString(editForm.deadline_at) : null}
                disabledDate={editDeadlineRange ? makeDisabledDate(editDeadlineRange.min) : undefined}
                disabledTime={editDeadlineRange ? makeDisabledTime(editDeadlineRange.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setEditForm((p) => ({
                    ...p,
                    deadline_at: d ? d.minute(0).second(0).millisecond(0).toISOString() : undefined,
                  }))
                }
                allowClear
                styles={{ popup: { root: { zIndex: 12000 } } }}
              />
            </div>
          </div>
          <div className="ticket-edit-form__footer">
            <Button theme="default" block onClick={() => setEditing(false)}>取消</Button>
            <Button theme="primary" block onClick={saveEdit}>保存</Button>
          </div>
        </div>
      </Popup>

      <Popup visible={showEscalatePopup} onClose={() => { setShowEscalatePopup(false); setEscalateReason(''); }} placement="bottom" showOverlay destroyOnClose>
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

      {/* 结束工单确认弹窗：问题 + 工单解决方式 */}
      <Popup visible={showResolutionPopup} onClose={handleResolveCancel} placement="bottom" showOverlay>
        <div className="ticket-edit-form">
          <div className="ticket-edit-form__header">
            <span className="ticket-edit-form__title">确认完成工单</span>
            <span className="ticket-edit-form__close" onClick={handleResolveCancel}>×</span>
          </div>
          <div className="ticket-edit-form__body">
            {/* 工单问题 */}
            <div className="ticket-edit-form__field">
              <span className="ticket-edit-form__label">📌 工单问题</span>
              <div style={{ fontSize: '14px', fontWeight: 600, color: '#1a1a1a', marginBottom: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail?.title}</div>
              <div style={{
                fontSize: '13px', color: '#888', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
              }}>
                {detail?.description || <span style={{ color: '#bbb' }}>（无描述）</span>}
              </div>
            </div>

            {/* 工单解决方式 */}
            <div className="ticket-edit-form__field">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span className="ticket-edit-form__label" style={{ marginBottom: 0 }}>✅ 工单解决方式</span>
                {resolutionFailed && (
                  <span style={{ color: '#faad14', fontSize: '12px' }}>自动总结出错，请手动补充</span>
                )}
              </div>
              {resolutionLoading || resolutionPolling ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '20px 0', color: '#666', fontSize: '13px', justifyContent: 'center' }}>
                  <Loading size="20px" /> 正在生成解决方式…
                </div>
              ) : (
                <Textarea
                  value={resolutionText}
                  onChange={(v) => setResolutionText(String(v))}
                  placeholder={
                    resolutionNoSolution
                      ? '当前没有解决方案，请在此补充实际解决方式'
                      : resolutionFailed
                        ? 'U老师自动总结出错了，请补充解决方法'
                        : '请填写该工单的解决方式'
                  }
                  autosize={{ minRows: 4, maxRows: 8 }}
                  maxlength={1000}
                />
              )}
            </div>
          </div>
          <div className="ticket-edit-form__footer">
            <Button theme="default" onClick={handleResolveCancel}>取消</Button>
            <Button
              theme={resolutionFailed ? 'danger' : 'default'}
              onClick={resolutionFailed ? handleRetryResolution : handleGenerateResolution}
              loading={resolutionLoading || resolutionPolling}
              disabled={resolutionSubmitting || resolutionLoading || resolutionPolling}
            >
              {resolutionFailed ? '重试' : '帮我生成'}
            </Button>
            <Button
              theme="primary"
              onClick={handleConfirmResolve}
              disabled={resolutionSubmitting || resolutionLoading || resolutionPolling || !resolutionText.trim()}
            >
              确认完成
            </Button>
          </div>
        </div>
      </Popup>


      <Popup visible={showReassignPopup} onClose={() => { setShowReassignPopup(false); setReassignUser(null); setReassignReason(''); }} placement="bottom" showOverlay destroyOnClose>
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

      <Popup
        visible={showCreatorNamePopup}
        onClose={() => { if (!submittingCreatorName) { setShowCreatorNamePopup(false); setCreatorNameInput(''); } }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">设置创建人姓名</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            修改创建人（{(detail?.created_by_name || detail?.reporter_name || detail?.created_by || '-')}）的姓名，保存后工单创建人将显示新姓名。
          </p>
          <Form initialData={{}}>
            <FormItem label="姓名" name="creatorName" labelAlign="top" requiredMark>
              <ClearableInput
                value={creatorNameInput}
                onChange={(v) => setCreatorNameInput(String(v))}
                placeholder="请输入创建人姓名"
                maxlength={64}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={submittingCreatorName} onClick={() => { setShowCreatorNamePopup(false); setCreatorNameInput(''); }}>取消</Button>
            <Button theme="primary" loading={submittingCreatorName} onClick={handleUpdateCreatorName} disabled={!creatorNameInput.trim()}>保存</Button>
          </div>
        </div>
      </Popup>

      <Popup
        visible={showDeadlinePopup}
        onClose={() => { if (!submittingDeadline) setShowDeadlinePopup(false); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">修改最晚解决时间</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            仅调整本工单的最晚解决时间，不影响其他字段；清空后表示不设置截止时间。
          </p>
          {(() => {
            // 选择下限 = 创建时间（取整到整点），不限制未来上限
            const range = getDeadlineRange(detail?.priority, detail?.created_at);
            return (
              <DatePicker
                style={{ width: '100%' }}
                placeholder="点击选择（可清空）"
                format="YYYY-MM-DD HH:00"
                showTime={{ defaultValue: dayjs().hour(9).minute(0), format: 'HH:00', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={(trigger) => trigger.parentElement || document.body}
                value={deadlineDraft ? parseDeadlineString(deadlineDraft) : null}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setDeadlineDraft(d ? d.minute(0).second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 12000 } } }}
              />
            );
          })()}
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={submittingDeadline} onClick={() => setShowDeadlinePopup(false)}>取消</Button>
            <Button theme="primary" loading={submittingDeadline} onClick={handleUpdateDeadline}>保存</Button>
          </div>
        </div>
      </Popup>

      <Popup visible={showReturnConfirmPopup} onClose={() => { setShowReturnConfirmPopup(false); setReturnReason(''); }} placement="bottom" showOverlay destroyOnClose>
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

      {/* 协商节点时间弹窗：时间选择器 + 文字理由（截图暂不实现） */}
      <Popup
        visible={showNegotiateStepPopup}
        onClose={() => { if (!submittingNegotiate) setShowNegotiateStepPopup(false); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">协商节点时间</h4>
          <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            可将节点调整为当前或之后的任一节点，并设置节点结束时间（SLA），协商理由必填。
          </p>
          <Form initialData={{}}>
            <FormItem label="协商节点" name="negotiateStepId" labelAlign="top" requiredMark>
              <select
                value={negotiateStepId ?? ''}
                onChange={(e) => setNegotiateStepId(e.target.value ? Number(e.target.value) : null)}
                style={{
                  width: '100%', padding: '8px 10px', fontSize: 14,
                  border: '1px solid var(--component-border, #dcdcdc)', borderRadius: 6,
                  background: '#fff', marginBottom: 12,
                }}
              >
                {(() => {
                  // 仅展示 sequence >= 当前节点的可选节点（当前及之后）
                  const curSeqForNegotiate = stepTemplate.find((s) => s.id === detail?.curr_step_id)?.sequence ?? -1;
                  const negotiableSteps = (stepTemplate.length > 0 ? stepTemplate : [])
                    .filter((s) => s.sequence >= curSeqForNegotiate)
                    .sort((a, b) => a.sequence - b.sequence);
                  const fallback = detail?.curr_step_id
                    ? [{ id: detail.curr_step_id, step_name: detail.curr_step_name || '当前节点', sequence: 0 }]
                    : [];
                  return (negotiableSteps.length > 0 ? negotiableSteps : fallback);
                })().map((s) => (
                  <option key={s.id} value={s.id}>{`第${s.sequence + 1}步 · ${s.step_name}`}</option>
                ))}
              </select>
            </FormItem>
          </Form>
          {(() => {
            // 选择下限 = 工单创建时间，与最晚解决时间编辑口径一致
            const range = getDeadlineRange(detail?.priority, detail?.created_at);
            return (
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择节点结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                // 面板渲染到 body 并向上弹出，避免遮挡「协商理由」与底部按钮
                getPopupContainer={() => document.body}
                value={negotiateEndTime ? parseDeadlineString(negotiateEndTime) : null}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setNegotiateEndTime(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
            );
          })()}
          <Form initialData={{}}>
            <FormItem label="协商理由" name="negotiateReason" labelAlign="top" requiredMark>
              <Textarea
                value={negotiateReason}
                onChange={(v) => setNegotiateReason(String(v))}
                placeholder="请输入协商理由（必填）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={submittingNegotiate} onClick={() => setShowNegotiateStepPopup(false)}>取消</Button>
            <Button theme="primary" loading={submittingNegotiate} onClick={handleNegotiateStep} disabled={!negotiateEndTime || !negotiateStepId || !negotiateReason.trim()}>保存</Button>
          </div>
        </div>
      </Popup>

      {/* 当前阶段完成弹窗：选择下一阶段 + 节点结束时间 */}
      {detail && (() => {
        const currIdxLocal = stepTemplate.findIndex((s) => s.id === detail.curr_step_id);
        const currSeqLocal = currIdxLocal >= 0 ? stepTemplate[currIdxLocal].sequence : null;
        // 仅展示 sequence > 当前的可选下一阶段
        const nextStepOptions = (stepTemplate.length > 0 ? stepTemplate : [])
          .filter((s) => currSeqLocal === null || s.sequence > currSeqLocal)
          .sort((a, b) => a.sequence - b.sequence);
        const range = getDeadlineRange(detail.priority, detail.created_at);
        return (
          <Popup
            visible={showCompleteStepPopup}
            onClose={() => { if (!submittingComplete) setShowCompleteStepPopup(false); }}
            placement="bottom"
            showOverlay
            destroyOnClose
          >
            <div className="ticket-edit">
              <h4 className="ticket-edit__title">请选择下一阶段</h4>
              <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
                完成当前阶段后，工单将进入"未一致"状态，回合交给创建人确认。
              </p>
              <Form initialData={{}}>
                <FormItem label="下一阶段" name="completeNextStepId" labelAlign="top" requiredMark>
                  <select
                    value={completeNextStepId ?? ''}
                    onChange={(e) => setCompleteNextStepId(e.target.value ? Number(e.target.value) : null)}
                    style={{
                      width: '100%', padding: '8px 10px', fontSize: 14,
                      border: '1px solid var(--component-border, #dcdcdc)', borderRadius: 6,
                      background: '#fff', marginBottom: 12,
                    }}
                  >
                    {nextStepOptions.length === 0 && (
                      <option value="" disabled>无可选下一阶段</option>
                    )}
                    {nextStepOptions.map((s) => (
                      <option key={s.id} value={s.id}>{`第${s.sequence + 1}步 · ${s.step_name}`}</option>
                    ))}
                  </select>
                </FormItem>
              </Form>
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择下一阶段结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={completeNextEndTime ? parseDeadlineString(completeNextEndTime) : null}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setCompleteNextEndTime(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
              <div className="ticket-edit__btns">
                <Button theme="default" disabled={submittingComplete} onClick={() => setShowCompleteStepPopup(false)}>取消</Button>
                <Button theme="primary" loading={submittingComplete} onClick={handleStepComplete} disabled={!completeNextStepId || !completeNextEndTime}>确认</Button>
              </div>
            </div>
          </Popup>
        );
      })()}

      {/* 设置节点时间弹窗（已升级工单，处理人一锤定音） */}
      {detail && (() => {
        const range = getDeadlineRange(detail.priority, detail.created_at);
        return (
          <Popup
            visible={showSetStepTimePopup}
            onClose={() => { if (!submittingSetStepTime) setShowSetStepTimePopup(false); }}
            placement="bottom"
            showOverlay
            destroyOnClose
          >
            <div className="ticket-edit">
              <h4 className="ticket-edit__title">设置节点时间</h4>
              <p style={{ color: '#666', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
                升级上报后的工单，处理人可直接设置节点时间，无需协商（一锤定音）。
              </p>
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择节点结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={setStepTimeValue ? parseDeadlineString(setStepTimeValue) : null}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setSetStepTimeValue(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
              <div className="ticket-edit__btns">
                <Button theme="default" disabled={submittingSetStepTime} onClick={() => setShowSetStepTimePopup(false)}>取消</Button>
                <Button theme="primary" loading={submittingSetStepTime} onClick={handleSetStepTime} disabled={!setStepTimeValue}>确认</Button>
              </div>
            </div>
          </Popup>
        );
      })()}

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
        className="diagnosis-report-dialog"
        visible={reportVisible}
        title="U老师 诊断报告"
        confirmBtn="关闭"
        onConfirm={() => setReportVisible(false)}
      >
        <div className="markdown-body" style={{ maxHeight: '60vh', overflowY: 'auto', textAlign: 'left', fontSize: 14, lineHeight: 1.8 }}>
          {diagnosisReport ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              urlTransform={urlTransformAllowDataImage}
            >
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