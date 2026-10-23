import { Fragment, useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useParams, useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { Navbar, Button, Textarea, Toast, Loading, Tag, Popup, Dialog, Form, FormItem } from 'tdesign-mobile-react';
import AppButton from '@/shared/components/AppButton';
import { User, UserCheck, Folder, FolderKanban, AlarmClock, Clock, RefreshCw, Building2, Store, Download, FileImage, FileText, FileSpreadsheet, FileCode, FileArchive, Paperclip, Bot, ClipboardList } from 'lucide-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import ClearableInput from '@/shared/components/ClearableInput';
import TitleEllipsis from '@/shared/components/TitleEllipsis';
import { setupWechatShare, isPcWechat } from '@/shared/utils/wechatJsSdk';
import { completeArchiveReportTemplate } from '@/shared/utils/archiveReport';
import { WECHAT_CONFIG } from '@/config/wechat';
import { createRequest, getToken, ApiError } from '@/api/client';
import API_CONFIG from '@/config/api';
import { readStored } from '@/stores/authStorage';
import SafeHtml from '@/shared/components/SafeHtml';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import TicketDynamicsCard from '@/shared/components/TicketDynamicsCard';
import StepNegotiationCard from '@/shared/components/StepNegotiationCard';
import SpecDocCard from '@/shared/components/SpecDocCard';
import { useStepNegotiation } from '@/shared/hooks/useStepNegotiation';
import { useResolveTicket } from '@/shared/hooks/useResolveTicket';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';
import DispatchFold from '@/shared/components/DispatchFold';
import RelationBlock from '@/shared/components/RelationBlock';
import type { BlockedErrorDetail } from '@/api/ticket';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { useWorkbenchStore } from '@/stores/workbench';
import { useAuthStore } from '@/stores/auth';
import { uploadCommentAttachment, getProxyRelations, type ProxyRelation } from '@/api/ticket';
import ProxyRelationBanner from '@/shared/components/ProxyRelationBanner';
import { TICKET_TYPE_DISPLAY_MAP, STATUS_DISPLAY_MAP, PRIORITY_DISPLAY_MAP, canEditPriority } from '@/shared/constants/ticket';
import { isSameUser } from '@/shared/utils/userIdentity';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime, parseDeadlineString } from '@/shared/utils/deadline';
import { formatDateTime, formatRawDateTime } from '@/shared/utils/url';
import MDEditor from '@uiw/react-md-editor';
import '@uiw/react-md-editor/markdown-editor.css';
import { archiveGenerateStream, fetchWithAuth, taskDiscussStream } from '@/api/ai';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { getProjectMembers } from '@/api/projects';
import type { ProjectMember } from '@/api/projects';
import { dedupeFileNames } from '@/shared/utils/uniqueFileNames';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { appUrlTransform } from '@/shared/utils/markdown';

// 状态文字色（设计稿 statusText 蓝阶：待处理 blue-3 / 处理中·进行中 blue-2 / 已解决 blue-1 / 关闭·取消 muted）
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

// ORS-1010 归档相似工单提示：归档编辑器打开时后台拉取，供归档人对照已沉淀的
// 相似工单。score 是 RRF 融合分非余弦（后端文档明确警告），前端不展示分值。
interface ArchiveSimilarItem {
  task_id: number;
  title: string;
  status?: string;
  project_name?: string;
  archived?: boolean;
  score?: number;
  verified?: 'confirmed' | 'rejected' | 'recurred' | 'unknown' | string;
  /** 沉淀卡正文（问题/根因/解决三段式）——条目展开显示，替代跳工单详情 */
  summary?: string;
  /** ORS-1035：相似单评论区图片（≤3 张 object_path），展开区缩略图对照 */
  image_paths?: string[];
  report?: {
    content: string;
    version: number;
    reviewer?: string;
    reviewer_name?: string;
    reviewed_at?: string;
  } | null;
}

interface MinioPathInfo { bucket: string; objectKey: string; }


// ORS-1035 相似工单缩略图：object_path → 带鉴权 token 的附件代理 URL（<img> 直连）
const similarThumbUrl = (objectPath: string): string => {
  const authToken = readStored('AUTH_TOKEN') || '';
  return `${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(objectPath)}&token=${encodeURIComponent(authToken)}`;
};

const SimilarThumb = ({ objectPath }: { objectPath: string }) => (
  <img
    src={similarThumbUrl(objectPath)}
    alt=""
    loading="lazy"
    onClick={(e) => {
      e.stopPropagation(); // 不触发条目的展开/收起
      window.open(similarThumbUrl(objectPath), '_blank');
    }}
    style={{ width: 72, height: 72, objectFit: 'cover', borderRadius: 6, border: '1px solid var(--border, #e8eaee)', cursor: 'zoom-in', flexShrink: 0 }}
  />
);

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
  // 临时：重新指派选人置顶用
  project_contact_person_id?: string | null;
  project_contact_person_name?: string | null;
  created_by?: string; created_by_name?: string;
  assigned_to?: string; assigned_to_name?: string;
  reporter_name?: string; assignee_name?: string;
  contact?: string; customer?: string; created_at: string; updated_at: string;
  attachments?: Attachment[]; metadata_info?: Record<string, unknown>; comments?: Comment[];
  // tasks 详情接口 GET /{id} 返回蛇形 deadline_at（见 TicketResponse）
  deadline_at?: string | null;
  // 二次派单感知增强（M3）：未派到指定人时的完整话术（详情页 redispatch.result.tip_detail）
  // 二次派单感知增强：派单理由（为什么派给接单人；接单人/提单人/管理员可见 → redispatch.result.reasoning）
  redispatch?: {
    dispatch_round?: number;
    reassign_reason?: string | null;
    result?: { tip_detail?: string | null; reasoning?: string | null };
  } | null;
  // 工单阶段性处理（协商节点）：当前节点 ID/名称/结束时间（naive UTC）
  curr_step_id?: number | null;
  curr_step_name?: string | null;
  curr_step_endtime?: string | null;
  // 回合制：最近一次改 step 的操作人侧标识（assigned/creator）与回合数
  step_last_updated_by?: 'assigned' | 'creator' | null;
  step_last_updated_at?: string | null;
  step_negotiation_round?: number;
  step_phase_round?: number;
  step_neg_max_rounds?: number;
  // 当前协商节点是否已协商一致：确认同意 → True；进入新节点 / 协商节点时间 → 重置 False
  curr_step_agreed?: boolean;
  // 升级上报次数：>0 表示已升级，协商回合重置为1且不再受限
  escalate_count?: number;
  // ── 代他人提单（代理提单）：后端下发的关系与视角标记（非参与人为空/False）──
  proxy_relation_status?: 'pending' | 'acknowledged' | 'declined' | null;
  proxy_agent_name?: string | null;
  proxy_principal_name?: string | null;
  is_proxy_agent?: boolean;
  is_principal?: boolean;
  // ── 归档流程字段 ──
  archived_by?: string | null;
  archived_at?: string | null;
  current_archive_report?: {
    id: number;
    task_id: number;
    content: string;
    version: number;
    created_by: string;
    created_by_name?: string | null;
    updated_by?: string | null;
    updated_by_name?: string | null;
    created_at: string;
    updated_at: string;
    submit_status: 'draft' | 'submitted' | 'approved' | 'rejected';
    reviewer?: string | null;
    reviewer_name?: string | null;
    can_review?: boolean;
    reviewed_at?: string | null;
    review_comment?: string | null;
    revision: number;
  } | null;
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
  const location = useLocation();
  // 从列表卡片跳进来时的讨论区定位参数（点参与人头像 → focus=discussion[&author=xxx|&commentId=xxx]）
  const [searchParams] = useSearchParams();
  const focusDiscussion = searchParams.get('focus') === 'discussion';
  const focusCommentId = searchParams.get('commentId');
  const focusAuthor = searchParams.get('author');
  // SPA 从列表点进时带上的卡片预览，先出壳再拉完整详情（PC 微信整页跳转带不走 state）
  const listPreview = (location.state as { listPreview?: Ticket } | null)?.listPreview;
  const previewMatches = !!(listPreview && detailId && String(listPreview.id) === String(detailId));
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '管理服务');

  const { refreshTasks } = useWorkbenchStore();
  const { username, userId, name, hasPermission, isAdmin } = useAuthStore();

  const [detail, setDetail] = useState<Ticket | null>(() => (previewMatches ? listPreview! : null));
  const [detailLoading, setDetailLoading] = useState(() => !previewMatches);
  // 二次派单感知增强（M3）：未派到指定人时的完整情商话术（详情页 redispatch.result.tip_detail）
  const [redispatchTipDetail, setRedispatchTipDetail] = useState<string>('');
  // 二次派单感知增强：派单理由（为什么派给接单人；接单人/提单人/管理员可看到）
  const [dispatchReason, setDispatchReason] = useState<string>('');
  // 最近一次人工转派的原因（展示用）——和派单原因放一起展示；后端已按角色过滤。
  // 注意别和下面 `reassignReason`（转派弹窗输入框）撞名。
  const [lastReassignReason, setLastReassignReason] = useState<string>('');
  const [editing, setEditing] = useState(false);
  const [editForm, setEditForm] = useState<{ title: string; description: string; priority: string; ticket_type: string; curr_step_endtime?: string }>({ title: '', description: '', priority: 'medium', ticket_type: 'problem' });
  // 当前阶段截止时间区间：基准 = 工单创建时间（detail.created_at），而非用户操作时刻
  const editDeadlineRange = getDeadlineRange(editForm.priority, detail?.created_at);
  // 优先级仅在「尚未派单」（待处理/待派单）可修改；已派单及后续状态禁止（置灰不可点）
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
  const [reassignKind, setReassignKind] = useState<'misassign' | 'stage' | 'other' | ''>('');
  // 创建人姓名编辑（仅处理人/管理员可点击设置）
  const [showCreatorNamePopup, setShowCreatorNamePopup] = useState(false);
  const [creatorNameInput, setCreatorNameInput] = useState('');
  const [submittingCreatorName, setSubmittingCreatorName] = useState(false);
  // 当前阶段截止时间直接编辑（拥有 backend:tasks:operate 权限的用户可点击信息项改期）
  // deadlineDraft: undefined=未改动（保存时不提交该字段）；ISO 字符串=新时间；null=清除
  const [showDeadlinePopup, setShowDeadlinePopup] = useState(false);
  const [deadlineDraft, setDeadlineDraft] = useState<string | null | undefined>(undefined);
  // 关联工单阻塞提示（状态变更 422 时传入 RelationBlock 展示）
  const [blockedError, setBlockedError] = useState<BlockedErrorDetail | null>(null);
  const [submittingDeadline, setSubmittingDeadline] = useState(false);
  const [submittingComment, setSubmittingComment] = useState(false);
  const [askingAI, setAskingAI] = useState(false);
  const [aiStreamReply, setAiStreamReply] = useState('');
  const aiAbortRef = useRef<AbortController | null>(null);
  const [aiEpoch, setAiEpoch] = useState(0);
  const detailRef = useRef(detail);
  detailRef.current = detail;
  const diagnosingRef = useRef(false);
  const aiBusyRef = useRef(false);
  const discussQueueRef = useRef<Array<{ text: string; files: File[]; options?: { replyTo?: string | number; uspEnvId?: number } }>>([]);
  const [aiQueueItems, setAiQueueItems] = useState<string[]>([]);
  const MAX_AI_DISCUSS_QUEUE = 3;

  const bumpAiQueue = () => setAiQueueItems(discussQueueRef.current.map((j) => j.text));

  const abortAi = () => {
    // 只停当前 HTTP；finally 负责收尾并接着跑队列，这里不清队列、不提前改 busy。
    aiAbortRef.current?.abort();
  };

  // 结束工单确认弹窗：问题 + AI 解决方式
  const [showResolutionPopup, setShowResolutionPopup] = useState(false);
  const [resolutionText, setResolutionText] = useState('');
  const [resolutionLoading, setResolutionLoading] = useState(false);
  const [resolutionFailed, setResolutionFailed] = useState(false);
  const [resolutionPolling, setResolutionPolling] = useState(false);
  // AI 判定当前无解决方案（仅占位提示，不填入输入框）
  const [resolutionNoSolution, setResolutionNoSolution] = useState(false);

  // 标记无法解决弹窗
  const [showUnsolvablePopup, setShowUnsolvablePopup] = useState(false);
  const [unsolvableReason, setUnsolvableReason] = useState('');
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
  const [adjustCode, setAdjustCode] = useState('');
  const [showRejectPopup, setShowRejectPopup] = useState(false);
  const [rejectReason, setRejectReason] = useState('');

  // ── 归档流程 state ──
  const [archiveEditorVisible, setArchiveEditorVisible] = useState(false);
  const [archiveReportContent, setArchiveReportContent] = useState('');
  const [archiveReportRevision, setArchiveReportRevision] = useState<number>(1);

  const [archiveEditorMode, setArchiveEditorMode] = useState<'edit' | 'review'>('edit');
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [archiveRejectReason, setArchiveRejectReason] = useState('');
  const [archiveGenerating, setArchiveGenerating] = useState(false);
  const [archiveGenStatus, setArchiveGenStatus] = useState('');
  const [showArchiveRejectPopup, setShowArchiveRejectPopup] = useState(false);
  // ORS-1010 归档相似工单提示：归档编辑器打开时后台拉取（与 AI 初稿生成并行），
  // 展示已沉淀的相似工单供归档人对照；不展示相似度分值（RRF 融合分非余弦，
  // 展示百分比会误导），只有"有没有、是哪几张"。
  const [archiveSimilar, setArchiveSimilar] = useState<ArchiveSimilarItem[]>([]);
  const [archiveSimilarLoading, setArchiveSimilarLoading] = useState(false);
  // ORS-1010：报告预览/编辑切换（生成完默认预览——用户先看到渲染结果含图片）
  const [archiveReportPreview, setArchiveReportPreview] = useState(false);
  // ORS-1010：报告插图上传（走评论附件接口拿 object_path，正文插 markdown）
  const [uploadingReportImage, setUploadingReportImage] = useState(false);
  const reportImageInputRef = useRef<HTMLInputElement | null>(null);
  // ORS-1010：展开查看卡正文（问题/根因/解决）的条目
  const [expandedSimilar, setExpandedSimilar] = useState<Set<number>>(new Set());
  // 检索成功完成（retrieval_status=ok）：0 条也显示"未发现"提示，让功能存在感在；
  // 检索挂了保持静默（不假装"没有"）
  const [archiveSimilarReady, setArchiveSimilarReady] = useState(false);

  // 工单阶段性处理（协商节点）
  const [stepTemplate, setStepTemplate] = useState<StepTemplate[]>([]);
  const [responding, setResponding] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [showNegotiateStepPopup, setShowNegotiateStepPopup] = useState(false);
  const [negotiateStepId, setNegotiateStepId] = useState<number | null>(null);
  const [negotiateEndTime, setNegotiateEndTime] = useState<string | null>(null);
  const [negotiateReason, setNegotiateReason] = useState('');
  const [submittingNegotiate, setSubmittingNegotiate] = useState(false);
  // 未解决打回弹窗：提单人选择重新开始的阶段 + 节点结束时间
  const [showReopenPopup, setShowReopenPopup] = useState(false);
  const [reopenStepId, setReopenStepId] = useState<number | null>(null);
  const [reopenEndTime, setReopenEndTime] = useState<string | null>(null);
  const [submittingReopen, setSubmittingReopen] = useState(false);
  // 当前阶段完成弹窗：处理人选择下一阶段节点 + 节点结束时间
  const [showCompleteStepPopup, setShowCompleteStepPopup] = useState(false);
  const [completeNextStepId, setCompleteNextStepId] = useState<number | null>(null);
  const [completeNextEndTime, setCompleteNextEndTime] = useState<string | null>(null);
  const [submittingComplete, setSubmittingComplete] = useState(false);
  // 设置节点时间弹窗（已升级工单，处理人一锤定音）
  const [showSetStepTimePopup, setShowSetStepTimePopup] = useState(false);
  const [setStepTimeValue, setSetStepTimeValue] = useState<string | null>(null);
  const [submittingSetStepTime, setSubmittingSetStepTime] = useState(false);

  // 代他人提单（代理提单）关系：详情页顶部横幅数据源。
  // 后端对非参与人返回空数组（脱敏），故此处拿不到即视为无关系，无需前端再判权限。
  const [proxyRelation, setProxyRelation] = useState<ProxyRelation | null>(null);

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
        // 二次派单感知增强：派单理由（后端对接单人/提单人/管理员返回 reasoning，非空即展示）
        setDispatchReason(t.redispatch?.result?.reasoning || '');
        setLastReassignReason(t.redispatch?.reassign_reason || '');
        // 代理关系（代他人提单）：独立接口，失败不阻断详情渲染（横幅缺失而已）
        getProxyRelations(detailId)
          .then((list) => setProxyRelation(list?.[0] || null))
          .catch(() => setProxyRelation(null));
        // 摘要存 metadata_info.ai_summary（不混入讨论区）
        const meta = t.metadata_info || {};
        setAiSummary(typeof meta.ai_summary === 'string' ? meta.ai_summary as string : '');

        // 拉取协商阶段模板（按工单 task_type），用于「工单阶段性处理」当前节点描述
        request<{ code: number; data: { steps: StepTemplate[] } }>(`/${detailId}/steps`)
          .then((res) => setStepTemplate(res?.data?.steps || []))
          .catch(() => setStepTemplate([]));

        // 获取项目成员用于 @ 提及（无项目时也能拉到提单人、处理人和代提单人）
        getProjectMembers(detailId)
          .then((members) => {
            const pinRoles = ['提单人', '处理人', '代提单人'];
            const sorted = [...members].sort((a, b) => {
              const ai = pinRoles.indexOf(a.role_name || '');
              const bi = pinRoles.indexOf(b.role_name || '');
              return (ai === -1 ? pinRoles.length : ai) - (bi === -1 ? pinRoles.length : bi);
            });
            setProjectMembers(sorted);
          })
          .catch(() => setProjectMembers([]));
        // 获取全部在职用户（@ 输入过滤字时扩展到项目外的人）
        getProjectMembers(detailId, true)
          .then((u) => setAllUsers(u))
          .catch(() => setAllUsers([]));
      })
      .catch((err) => {
        const body = err instanceof ApiError ? err.errorBody as { detail?: { code?: string; report_id?: number } } : undefined;
        if (body?.detail?.code === 'BLIND_REVIEW_REQUIRED' && body.detail.report_id) {
          navigate(`/admin/knowledge/reviews/${body.detail.report_id}`, { replace: true });
          return;
        }
        // 列表预览先出壳时：404/无权限应清掉预览，避免一直停在残缺卡片上可点操作
        const status = err instanceof ApiError ? err.statusCode : 0;
        if (status === 404 || status === 403) setDetail(null);
        Toast({ message: `详情加载失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      })
      .finally(() => setDetailLoading(false));
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

  // PC 微信专用：SPA(pushState) 跳转不会更新微信内部记录的「分享页 URL」，
  // 导致分享链接停留在首次加载的工单。检测当前工单是否经 SPA 跳入，是则用整页刷新修正。
  // 仅 PC 微信生效；手机端 / 普通浏览器走 SPA，不受影响。
  useEffect(() => {
    if (!detail?.id) return;
    if (!isPcWechat()) return;
    const current = window.location.href.split('#')[0];
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    const loadUrl = nav ? String(nav.name).split('#')[0] : '';
    if (loadUrl !== current) {
      window.location.href = current;
    }
  }, [detail?.id]);

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

    // 代他人提单（代理提单）视角：**优先后端下发的角色标记**，不倒推身份。
    // 后端已按 token 判定 is_agent / is_principal（见 ProxyRelationResponse），
    // 前端自拼判定会在「姓名重名 / username 与 id 混用」时判错（历史踩过的坑）。
    const isProxyAgent = proxyRelation?.is_agent ?? Boolean(detail?.is_proxy_agent);
    const isPrincipal = proxyRelation?.is_principal ?? Boolean(detail?.is_principal);

    return { isAssignee, isReporter, isProxyAgent, isPrincipal };
  };

  // 拥有 backend:tasks:operate 权限的用户可点击「创建人」设置其姓名
  const canEditCreatorName = !!detail && hasPermission('backend:tasks:operate');

  const getActionButtons = () => {
    const status = detail?.status?.toLowerCase();
    if (!status) return [];

    const isClosed = status === 'closed';
    const isArchiving = status === 'archiving';
    const isArchived = status === 'archived';
    const isCanceled = status === 'canceled' || status === 'cancelled';
    if (isArchived || isCanceled) return [];
    if (isClosed) {
      // closed 状态：仅处理人（assignee）或有 operate 权限的人可见「开始归档」按钮
      const { isAssignee: isAssigneeForArchive } = getCurrentUserRoles();
      const canOperateArchive = hasPermission('backend:tasks:operate');
      if (!isAssigneeForArchive && !canOperateArchive) return [];
      const BTN_PRIMARY_ARCHIVE = { backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)', borderRadius: '999px', border: 'none' };
      return [
        { label: '开始归档', nextStatus: 'archiving', theme: 'primary', actionType: 'archive', customStyle: BTN_PRIMARY_ARCHIVE },
      ];
    }

    const { isAssignee, isReporter, isPrincipal } = getCurrentUserRoles();

    // 拥有 backend:tasks:operate 权限的用户，对所有活跃状态工单均可见且可操作
    const canOperate = hasPermission('backend:tasks:operate');

    // pending_requested：处理人已请求暂停，等待提单人确认——此时只有提单人侧（isReporter / isPrincipal）能操作
    // 处理人和普通登录用户看不到按钮
    if (status === 'pending_requested') {
      if (!isReporter && !isPrincipal && !canOperate) return [];
      // 管理员可以代替任何一方操作（运维兜底），但走和提单人侧一样的确认/驳回按钮
    }

    // unsolvable_requested：处理人标记无法解决，等待提单人确认——同暂停请求，仅提单人侧可操作
    if (status === 'unsolvable_requested') {
      if (!isReporter && !isPrincipal && !canOperate) return [];
    }

    const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused', 'unsolvable'];
    if (assigneeOnlyStatuses.includes(status) && !isAssignee && !canOperate) return [];

    // 已解决：与后端口径统一（决策 6）—— 提单人(created_by，即代理人) / 已确认跟进的被代理人 / 管理员
    if (status === 'resolved' && !isReporter && !isPrincipal && !canOperate) return [];

    // 顶部操作按钮配色（设计稿 05：主推进 bg-primary 白字胶囊 / 次操作 bg-secondary 深字胶囊）
    const BTN_PRIMARY = { backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)', borderRadius: '999px', border: 'none' };
    const BTN_SECONDARY = { backgroundColor: 'var(--secondary)', color: 'var(--foreground)', borderRadius: '999px', border: 'none' };
    const actions: Record<string, { label: string; nextStatus: string; theme: string; actionType?: string; customStyle?: Record<string, string> }[]> = {
      // new 状态由处理人首次响应（协商节点时间/确认同意）自动转为 in_progress，不再提供「开始处理」按钮
      new: [],
      // 有/无步骤模板的工单：处理人均可从顶部一键触发"处理完成"（走解决方式弹窗）
      // "暂停工单"仅管理员可见（直达已挂起 pending，跳过两阶段请求暂停）
      in_progress: [
        ...(canOperate ? [{ label: '暂停工单', nextStatus: 'pending', theme: 'warning' as const, customStyle: BTN_SECONDARY }] : []),
        { label: '处理完成', nextStatus: 'resolved', theme: 'success', customStyle: BTN_PRIMARY },
        { label: '无法解决', nextStatus: 'unsolvable_requested', theme: 'warning', customStyle: BTN_SECONDARY },
      ],
      // 暂停请求中：两阶段路径仅兼容历史数据，新流程不再产生；确认/驳回按钮已移至阶段性处理卡内
      pending_requested: [],
      // 无法解决确认中：确认/驳回按钮已移至「工单阶段性处理」卡内，顶部不再重复
      unsolvable_requested: [],
      unsolvable: [
        // 已被提单人确认无法解决的终态：处理人可点"继续处理"回到 in_progress
        { label: '继续处理', nextStatus: 'in_progress', theme: 'primary', customStyle: BTN_PRIMARY },
      ],
      pending: (isAssignee && isReporter)
        ? [
            // 工单退回发起人后：发起人可重新发起（继续处理）或关闭工单
            { label: '重新发起', nextStatus: 'in_progress', theme: 'primary', actionType: 'resume', customStyle: BTN_PRIMARY },
            { label: '关闭工单', nextStatus: 'closed', theme: 'default', customStyle: BTN_SECONDARY },
          ]
        : [{ label: '继续处理', nextStatus: 'in_progress', theme: 'primary', actionType: 'resume', customStyle: BTN_PRIMARY }],
      resolved: [
        { label: '未解决', nextStatus: 'in_progress', theme: 'warning', actionType: 'reopen', customStyle: BTN_SECONDARY },
        { label: '确认关闭', nextStatus: 'closed', theme: 'default', customStyle: BTN_PRIMARY },
      ],
      archiving: (() => {
        const report = detail?.current_archive_report;
        const canOperate2 = hasPermission('backend:tasks:operate');
        const { isAssignee: isAssignee2 } = getCurrentUserRoles();
        const username = localStorage.getItem('username') || '';
        const reviewerMatch = report?.reviewer && (report.reviewer === username || report.reviewer === localStorage.getItem('user_id'));
        const isReviewer = Boolean(report?.can_review || reviewerMatch);

        // 审核人视角：submitted 状态下显示通过/驳回
        if (report?.submit_status === 'submitted' && isReviewer) {
          const BTN_PRIMARY2 = { backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)', borderRadius: '999px', border: 'none' };
          const BTN_DANGER2 = { backgroundColor: 'var(--destructive)', color: 'var(--destructive-foreground)', borderRadius: '999px', border: 'none' };
          return [
            { label: '审核并提交财务', nextStatus: 'archiving', theme: 'primary', actionType: 'archive-approve', customStyle: BTN_PRIMARY2 },
            { label: '驳回归档', nextStatus: 'archiving', theme: 'danger', actionType: 'archive-reject', customStyle: BTN_DANGER2 },
          ];
        }

        // 处理人视角：draft / rejected 状态显示编辑入口
        if (report && (report.submit_status === 'draft' || report.submit_status === 'rejected') && (isAssignee2 || canOperate2)) {
          const BTN_PRIMARY3 = { backgroundColor: 'var(--primary)', color: 'var(--primary-foreground)', borderRadius: '999px', border: 'none' };
          return [
            { label: report.submit_status === 'rejected' ? '修改归档报告并重新提交' : '编辑归档报告', nextStatus: 'archiving', theme: 'primary', actionType: 'archive-edit', customStyle: BTN_PRIMARY3 },
          ];
        }

        // submitted 状态（处理人视角）：无按钮，等审核
        return [];
      })(),
      canceled: [{ label: '重新打开', nextStatus: 'new', theme: 'primary', customStyle: BTN_PRIMARY }],
    };

    return actions[status] || [];
  };

  const handleStatusChange = async (action: { nextStatus: string; pauseReason?: string; rejectReason?: string; unsolvableReason?: string }) => {
    if (!detail) return;
    // 清空前次阻塞提示
    setBlockedError(null);

    try {
      const bodyObj: Record<string, string> = { status: action.nextStatus };
      if (action.pauseReason) bodyObj.pause_reason = action.pauseReason;
      if (action.rejectReason) bodyObj.reject_reason = action.rejectReason;
      if (action.unsolvableReason) bodyObj.unsolvable_reason = action.unsolvableReason;
      await request<Ticket>(`/${detail.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify(bodyObj),
      });
      refreshTasks();
      const statusLabel = STATUS_DISPLAY_MAP[action.nextStatus] || action.nextStatus;
      await refreshDetail();
      Toast({ message: `状态已更新为${statusLabel}`, theme: 'success' });
    } catch (err) {
      // 422 阻塞校验失败：提取 blocked 详情展示
      if (err instanceof ApiError && err.statusCode === 422) {
        const body = (err.errorBody as { detail?: BlockedErrorDetail })?.detail;
        if (body?.code === 'blocked_by_related_tasks') {
          setBlockedError(body);
          Toast({ message: `被 ${body.blocked.length} 个工单阻塞`, theme: 'error' });
          return;
        }
      }
      // 标记无法解决理由必填（400）
      if (err instanceof ApiError && err.statusCode === 400 && action.nextStatus === 'unsolvable_requested') {
        Toast({ message: err.message || '请填写无法解决理由', theme: 'error' });
        return;
      }
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

  // ── ORS-1010：归档相似工单拉取（后台并行，不阻塞 AI 初稿生成）──
  // min_score=0.015（RRF k=60 语义：至少一路检索排进前 6；rejected ×0.7 加权后
  // 天然过不了线，被推翻的结论自动出局——生产实测有效匹配落在 0.0156~0.033）。
  // 用默认 remind scope：review scope 要求该单正在审核中且有审核权限（会 403），
  // 归档人写报告阶段用不到 report 内容，remind 字段（score/verified/status）已够。
  // verified=rejected 再前端兜底过滤。
  const fetchArchiveSimilar = useCallback(async (taskId: number) => {
    setArchiveSimilarLoading(true);
    setArchiveSimilarReady(false);
    setExpandedSimilar(new Set()); // 重新拉取即收起所有展开（弹窗重开也从收起态开始）
    try {
      const res = await request<{ task_id: number; retrieval_status?: string; similar: ArchiveSimilarItem[] }>(
        `/${taskId}/similar?limit=5&min_score=0.015`,
      );
      const list = (res?.similar || []).filter((s) => s.verified !== 'rejected');
      setArchiveSimilar(list.slice(0, 5));
      setArchiveSimilarReady(res?.retrieval_status !== 'unavailable');
    } catch {
      setArchiveSimilar([]); setArchiveReportPreview(false); // 静默降级：相似提示拿不到不影响归档
      setArchiveSimilarReady(false);
    } finally {
      setArchiveSimilarLoading(false);
    }
  }, []);

  // ── 开始归档（closed → archiving）──
  /** ORS-1010：报告插图上传——走评论附件接口拿 object_path，正文插相对路径
      markdown（预览/审核/知识卡全链路 AuthImage 可渲染）。多张逐张传，
      追加到正文末尾；失败 toast 不中断 */
  const handleReportImageUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    e.target.value = ''; // 允许重复选同一文件
    if (!files.length) return;
    setUploadingReportImage(true);
    try {
      const inserts: string[] = [];
      for (const f of files) {
        const objectPath = await uploadCommentAttachment(f, `report-${Date.now()}`);
        inserts.push(`![${f.name}](/api/tasks/attachments/download?path=${encodeURIComponent(objectPath)})`);
      }
      setArchiveReportContent((prev) => `${prev || ''}${prev?.trim() ? '\n' : ''}${inserts.join('\n')}\n`);
      Toast({ message: `已插入 ${inserts.length} 张图片`, theme: 'success' });
    } catch (err) {
      Toast({ message: `图片上传失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setUploadingReportImage(false);
    }
  };

  // ── 开始归档（closed → archiving）──
  const handleStartArchive = async () => {
    if (!detail || archiveLoading) return;
    setArchiveLoading(true);
    try {
      const ticket: Ticket = await request<Ticket>(`/archive-report/${detail.id}/archive-start`, { method: 'POST' });
      setDetail(ticket);
      setArchiveReportRevision(ticket.current_archive_report?.revision || 1);
      setArchiveEditorMode('edit');
      setArchiveEditorVisible(true);
      // ORS-1010：相似工单与 AI 初稿并行拉取（失败静默，编辑器不等它）
      setArchiveSimilar([]); setArchiveReportPreview(false);
      void fetchArchiveSimilar(Number(detail.id));

      const existing = ticket.current_archive_report?.content || '';
      // ORS-1010：AI 生成改为显式按钮（不自动跑）——编辑器空态给引导条，
      // 处理人自己写或点 AI 均可；已有内容（幂等复用）直接进编辑
      setArchiveReportContent(existing);
      Toast({ message: existing ? '已开始归档，请继续编写归档报告' : '已开始归档，可直接手写，或点「AI 生成初稿」', theme: 'success' });
    } catch (err) {
      // archive-start 失败兜底
      Toast({ message: `${err instanceof Error ? err.message : '开始归档失败'}`, theme: 'error' });
    } finally {
      setArchiveLoading(false);
    }
  };

  // ── ORS-1010：AI 生成初稿（显式按钮触发）──
  const handleAiGenerateReport = async () => {
    if (!detail || archiveGenerating || archiveLoading) return;
    const taskDetail = detail;
    if ((archiveReportContent || '').trim()) {
      // 已有内容 → 覆盖确认（重新生成是显式动作，不能误触丢稿）
      Dialog.confirm?.({
        title: '重新生成报告？',
        content: 'AI 将覆盖当前报告内容（可先「保存草稿」备份），确定继续？',
        confirmBtn: { content: '覆盖并生成', theme: 'primary' },
        cancelBtn: '取消',
        onConfirm: () => { runGenerate(); },
      });
      return;
    }
    runGenerate();

    async function runGenerate() {
    setArchiveGenerating(true);
    setArchiveGenStatus('AI 正在为您总结归档报告…');
    setArchiveReportPreview(false);
    let acc = '';
    try {
      await archiveGenerateStream(
        // task_id 走 int（后端 ArchiveGenerateRequest.task_id: int）；detail.id 是路由字符串
        { task_id: Number(taskDetail.id) },
        {
          onStatus: (m) => setArchiveGenStatus(m),
          onTitle: (t) => { acc = `# ${t}\n\n`; setArchiveReportContent(acc); },
          onDelta: (d) => { acc += d; setArchiveReportContent(acc); },
          onDone: () => {
            setArchiveGenerating(false);
            setArchiveGenStatus('');
            setArchiveReportPreview(true); // 生成完默认预览：先看渲染结果（含图片）
            // ORS-1035：服务端生成写回推进了 revision，重取最新版报告同步本地
            // （内容+版本号），否则保存必撞「已被他人修改」、重开弹窗见旧内容
            request<Ticket>(`/archive-report/${Number(taskDetail.id)}/archive-report`)
              .then((r) => {
                const cr = (r as unknown as {
                  current_archive_report?: { revision?: number; content?: string } | null;
                }).current_archive_report;
                if (cr?.revision) setArchiveReportRevision(cr.revision);
                if (cr?.content) setArchiveReportContent(cr.content);
              })
              .catch(() => {});
            Toast({ message: 'AI 初稿已生成，请核对并修改后提交审核', theme: 'success' });
          },
          onError: (m) => {
            setArchiveGenerating(false);
            setArchiveGenStatus('');
            setArchiveReportContent('');
            Toast({ message: `${m}（可直接手写）`, theme: 'warning' });
          },
          onSkipped: (m) => {
            setArchiveGenerating(false);
            setArchiveGenStatus('');
            Toast({ message: m, theme: 'warning' });
          },
        },
      );
      // SSE 网络级失败：解锁手写（服务端后台生成仍会写回，处理人保存前先刷新）
      setArchiveGenerating(false);
      setArchiveGenStatus('');
    } catch (err) {
      setArchiveGenerating(false);
      setArchiveGenStatus('');
      Toast({ message: `${err instanceof Error ? err.message : 'AI 生成失败'}（可直接手写报告）`, theme: 'warning' });
    }
    }

  };

  // ── 保存归档草稿 ──
  const handleSaveArchiveDraft = async () => {
    if (archiveLoading) return;
    const reportId = detail?.current_archive_report?.id;
    if (!reportId) return;
    setArchiveLoading(true);
    const doSave = () => request(`/archive-report/archive-reports/${reportId}`, {
      method: 'PUT',
      body: JSON.stringify({ content: archiveReportContent, revision: archiveReportRevision }),
    }) as Promise<{ revision: number }>;
    const syncFromServer = async () => {
      const latest = await request<Ticket>(`/archive-report/${Number(detail.id)}/archive-report`);
      const cr = (latest as unknown as {
        current_archive_report?: { revision?: number; content?: string } | null;
      }).current_archive_report;
      if (cr?.revision) setArchiveReportRevision(cr.revision);
      return cr;
    };
    try {
      const report = await doSave();
      setArchiveReportRevision(report.revision);
      // ORS-1035：保存后同步本地 detail 缓存——否则关弹窗再开读到的还是
      // 旧内容（detail 是进页面时的快照，保存不更新它就一直旧）
      setDetail((prev) => prev ? {
        ...prev,
        current_archive_report: {
          ...(prev.current_archive_report || {}),
          content: archiveReportContent,
          revision: report.revision,
        },
      } as Ticket : prev);
      Toast({ message: '草稿已保存', theme: 'success' });
    } catch (err) {
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('已被他人修改')) {
        // 409：AI 生成写回等推进了版本号——拉最新版本号原地重试一次
        try {
          const cr = (await syncFromServer()) || {};
          const report = await doSave();
          setArchiveReportRevision(report.revision);
          setDetail((prev) => prev ? {
            ...prev,
            current_archive_report: {
              ...(prev.current_archive_report || {}),
              content: archiveReportContent,
              revision: report.revision,
            },
          } as Ticket : prev);
          Toast({ message: '草稿已保存（已自动合并最新版本）', theme: 'success' });
        } catch (retryErr) {
          Toast({ message: `保存失败: ${retryErr instanceof Error ? retryErr.message : ''}`, theme: 'error' });
        }
      } else {
        Toast({ message: `保存失败: ${msg}`, theme: 'error' });
      }
    } finally {
      setArchiveLoading(false);
    }
  };

  // ── 提交归档报告审核（审核人由系统按问题领域 + 负荷概率派审）──
  const handleSubmitArchive = async () => {
    if (archiveLoading) return;
    const reportId = detail?.current_archive_report?.id;
    if (!reportId) {
      Toast({ message: '归档报告未找到，请重新开始归档', theme: 'warning' });
      return;
    }
    setArchiveLoading(true);
    try {
      // submit 后端返回 ArchiveReportResponse（不是 Ticket），不调 setDetail
      await request(`/archive-report/archive-reports/${reportId}/submit`, {
        method: 'POST',
        body: JSON.stringify({ content: archiveReportContent, revision: archiveReportRevision }),
      });
      setArchiveEditorVisible(false);
      loadDetail();
      Toast({ message: '已提交归档报告，等待审核', theme: 'success' });
    } catch (err) {
      Toast({ message: `提交失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setArchiveLoading(false);
    }
  };

  // ── 审核通过归档 ──
  const handleArchiveApprove = async () => {
    const reportId = detail?.current_archive_report?.id;
    if (reportId) navigate(`/admin/knowledge/reviews/${reportId}`);
  };

  // ── 审核驳回归档 ──
  const handleArchiveReject = async () => {
    if (archiveLoading) return;
    const reportId = detail?.current_archive_report?.id;
    if (!reportId || !detail) return;
    if (!archiveRejectReason.trim()) {
      Toast({ message: '请填写驳回理由', theme: 'warning' });
      return;
    }
    setArchiveLoading(true);
    try {
      // reject 后端返回 ArchiveReportResponse（不是 Ticket），不调 setDetail
      await request(`/archive-report/archive-reports/${reportId}/reject`, {
        method: 'POST',
        body: JSON.stringify({ review_comment: archiveRejectReason, revision: detail?.current_archive_report?.revision }),
      });
      setShowArchiveRejectPopup(false);
      setArchiveRejectReason('');
      loadDetail();
      Toast({ message: '已驳回归档报告', theme: 'success' });
    } catch (err) {
      Toast({ message: `操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setArchiveLoading(false);
    }
  };

  // ── 打开归档编辑器（根据 submit_status 区分编辑/审核模式）──
  const handleOpenArchiveEditor = () => {
    const report = detail?.current_archive_report;
    if (!report) {
      Toast({ message: '归档报告未找到', theme: 'warning' });
      return;
    }
    setArchiveReportContent(report.content || '');
    setArchiveReportRevision(report.revision || 1);
    if (report.submit_status === 'submitted') {
      navigate(`/admin/knowledge/reviews/${report.id}`);
      return;
    } else {
      setArchiveEditorMode('edit');
    }
    setArchiveEditorVisible(true);
    // ORS-1010：编辑/再编辑打开时后台带出相似工单（失败静默）
    setArchiveSimilar([]); setArchiveReportPreview(false);
    void fetchArchiveSimilar(Number(detail.id));
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
  const handleWsTaskUpdated = (patch: { status?: string; assigned_to?: string | null; assigned_to_name?: string | null; updated_at?: string | null }) => {
    setDetail((prev) => {
      if (!prev) return prev;
      // 用 updated_at 判断是否为过期推送（操作发起人自己已更新，跳过重复刷新）
      if (patch.updated_at && prev.updated_at) {
        const incoming = new Date(patch.updated_at).getTime();
        const current = new Date(prev.updated_at).getTime();
        if (incoming <= current) return prev;
      }
      // WS 推送的 assigned_to 可能为 null（退单/清空处理人），状态类型为 string | undefined，
      // null → undefined 以兼容类型，展示层有 || 兜底，null 与 undefined 表现一致。
      return {
        ...prev,
        ...(patch.status ? { status: patch.status } : {}),
        ...(patch.assigned_to !== undefined ? { assigned_to: patch.assigned_to ?? undefined } : {}),
        ...(patch.assigned_to_name !== undefined ? { assigned_to: patch.assigned_to_name ?? undefined } : {}),
      };
    });
    // WS 推送只含基础字段，阶段性处理等字段需拉全量刷新
    refreshDetail();
  };

  const refreshDetail = async () => {
    if (!detailId) return;
    const refreshed = await request<Ticket>(`/${detailId}?load_comments=true`, { skipCache: true });
    setDetail(refreshed);
    refreshTasks();
  };

  // 工单阶段性处理（协商节点）+ 结束工单（解决方式）：抽到共享 hook，与历史工单详情页复用
  const negotiation = useStepNegotiation(detailId ?? '', detail, refreshDetail);
  // 稳定 DatePicker 受控 value 引用：内联 parse 每次渲染都生成新 dayjs 实例，会触发 rc-picker
  // 的受控同步 effect，把「面板已选但未确认」的暂存值重置回受控 value（选完自动跳回当前时间的根因）
  const editFormDeadlineValue = useMemo(
    () => (editForm.curr_step_endtime ? parseDeadlineString(editForm.curr_step_endtime) : null),
    [editForm.curr_step_endtime],
  );
  const deadlineDraftValue = useMemo(
    () => (deadlineDraft ? parseDeadlineString(deadlineDraft) : null),
    [deadlineDraft],
  );
  const reopenEndTimeValue = useMemo(
    () => (negotiation.reopenEndTime ? parseDeadlineString(negotiation.reopenEndTime) : null),
    [negotiation.reopenEndTime],
  );
  const resolve = useResolveTicket(detailId ?? '', detail, refreshDetail, refreshTasks, (b) => setBlockedError(b));

  // ===== 公司/部门/项目审核 =====
  const approvalInfo = (() => {
    const meta = detail?.metadata_info || {};
    const approvalType = meta.approval_type as string | undefined;
    if (!approvalType) return null;
    const type = approvalType as 'new_company' | 'new_department' | 'new_project';
    return {
      type,
      targetTable: (meta.target_table as string) || '',
      targetId: (meta.target_id as string) || '',
      targetName: (meta.target_name as string) || '',
      companyName: meta.company_name as string | undefined,
      // 新项目额外字段
      targetCode: (meta.target_code as string) || '',
      customer: (meta.customer as string) || '',
      region: (meta.region as string) || '',
    };
  })();

  // approval_type → admin API 的 target_type（company/department/project）
  const approvalTargetType = (t: typeof approvalInfo) =>
    !t ? '' : t.type === 'new_company' ? 'company' : t.type === 'new_department' ? 'department' : 'project';
  // 审核类型对应的中文名（用于 UI 文案）
  const approvalLabelMap: Record<string, string> = {
    new_company: '公司',
    new_department: '部门',
    new_project: '项目',
  };

  const handleApprove = async () => {
    if (!approvalInfo) return;
    setApproving(true);
    try {
      await adminRequest(`/users/options/${approvalTargetType(approvalInfo)}/${approvalInfo.targetId}/approve`, {
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
    const trimmedName = (adjustName || '').trim();
    const trimmedCode = (adjustCode || '').trim();
    if (!trimmedName && !trimmedCode) {
      Toast({ message: '请输入要调整的内容', theme: 'warning' });
      return;
    }
    setApproving(true);
    try {
      const body: Record<string, string> = {};
      if (trimmedName) body.new_name = trimmedName;
      if (approvalInfo.type === 'new_project' && trimmedCode) body.new_code = trimmedCode;
      await adminRequest(`/users/options/${approvalTargetType(approvalInfo)}/${approvalInfo.targetId}/approve`, {
        method: 'PUT',
        body: JSON.stringify(body),
      });
      await request(`/${detail!.id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ status: 'closed' }),
      });
      Toast({ message: '已调整并审核通过', theme: 'success' });
      setShowAdjustPopup(false);
      setAdjustName('');
      setAdjustCode('');
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
      await adminRequest(`/users/options/${approvalTargetType(approvalInfo)}/${approvalInfo.targetId}/reject`, {
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

  const handleReassign = async () => {
    if (!detail || !reassignUser) return;
    if (!reassignKind) {
      Toast({ message: '请选择转派类型', theme: 'warning' });
      return;
    }
    const target = reassignUser.name || reassignUser.username;
    const remark = reassignReason.trim();
    try {
      await request(`/${detail.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          assigned_to: reassignUser.id || reassignUser.username,
          operation_type: 'reassign',
          reassign_kind: reassignKind,
          reassign_reason: remark || undefined,
        }),
      });

      if (remark) {
        try {
          await request(`/${detail.id}/comments`, {
            method: 'POST',
            body: JSON.stringify({ content: `重新指派原因：${remark}`, is_public: true }),
          });
        } catch {
          // 评论写入失败不阻断主流程，工单状态已变更
        }
      }

      await refreshDetail();
      Toast({ message: `已重新指派给 ${target}`, theme: 'success' });
      setReassignUser(null);
      setReassignReason('');
      setReassignKind('');
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

  // 修改当前阶段截止时间：拥有 backend:tasks:operate 权限的用户直接改期（PUT /{id}）
  // deadlineDraft 为 undefined（用户未改动）时不提交 curr_step_endtime 字段，避免误清除
  const handleUpdateDeadline = async () => {
    if (!detail) return;
    setSubmittingDeadline(true);
    try {
      const body: Record<string, unknown> = { operation_type: 'update' };
      if (deadlineDraft !== undefined) body.curr_step_endtime = deadlineDraft;
      await request<Ticket>(`/${detail.id}`, {
        method: 'PUT',
        body: JSON.stringify(body),
        skipCache: true,
      });
      await refreshDetail();
      Toast({ message: '当前阶段截止时间已更新', theme: 'success' });
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
    const authToken = readStored('AUTH_TOKEN') || '';
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
      curr_step_endtime: detail.curr_step_endtime || undefined,
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

  // ── 当前阶段截止时间：编辑弹窗用 antd DatePicker 下拉选择（双端可用）──
  // 浮层 z-index 通过 styles.popup.root 提到高于 tdesign 编辑弹窗（z-index 11500），避免被遮挡



  const copyId = (id: string) => {
    navigator.clipboard?.writeText(id).then(() => Toast({ message: '已复制工单号', theme: 'success' }));
  };

  // ── 普通评论：POST /api/tasks/{id}/comments；返回 true=成功（组件清空输入） ──
  const handleAddComment = async (text: string, files: File[] = [], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    if (!detail) {
      Toast({ message: '请输入评论内容', theme: 'warning' });
      return false;
    }
    setSubmittingComment(true);
    try {
      // 上传附件（同名文件自动改名，避免后端对象名重复覆盖）
      const tempId = generateTempId();
      const uploads = dedupeFileNames(files);
      const objectPaths: string[] = [];
      for (const f of uploads) {
        const p = await uploadCommentAttachment(f, tempId);
        if (p) objectPaths.push(p);
      }
      const newComment = await request<Comment>(`/${detail.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true, attachments: objectPaths, reply_to: options?.replyTo }),
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

  // ── @U老师 讨论：先存用户消息 → 空闲立刻 discuss / 进行中入队；返回 true=成功 ──
  const postDiscussUserComment = async (text: string, files: File[] = [], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    const current = detailRef.current;
    if (!current) return false;
    const tempId = generateTempId();
    const uploads = dedupeFileNames(files);
    const objectPaths: string[] = [];
    for (const f of uploads) {
      const p = await uploadCommentAttachment(f, tempId);
      if (p) objectPaths.push(p);
    }
    try {
      const newComment = await request<Comment>(`/${current.id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true, attachments: objectPaths, reply_to: options?.replyTo }),
      });
      setDetail((prev) => {
        if (!prev) return prev;
        const updatedComments = prev.comments ? [...prev.comments, newComment] : [newComment];
        return { ...prev, comments: updatedComments };
      });
      return true;
    } catch {
      return false;
    }
  };

  const pumpDiscussQueue = () => {
    const next = discussQueueRef.current.shift();
    bumpAiQueue();
    if (!next) return;
    void startDiscussTurn(next.text, next.files, next.options);
  };

  const startDiscussTurn = async (text: string, files: File[] = [], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    // 先占位 busy，让讨论区立刻进入 sending（输入框已清空），不要等 HTTP 流才 setAskingAI
    aiBusyRef.current = true;
    setAskingAI(true);
    const posted = await postDiscussUserComment(text, files, options);
    if (!posted) {
      aiBusyRef.current = false;
      setAskingAI(false);
      Toast({ message: '评论发送失败，请重试', theme: 'error' });
      return false;
    }
    return runDiscussHttp(text, options);
  };

  const runDiscussHttp = async (text: string, options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    const current = detailRef.current;
    if (!current) return false;
    const controller = new AbortController();
    aiAbortRef.current = controller;
    aiBusyRef.current = true;
    setDiagnosing(false);
    diagnosingRef.current = false;
    setAskingAI(true);
    setAiStreamReply('');
    setAiEpoch((n) => n + 1);
    let ok = false;
    try {
      const recentComments = (current.comments || []).slice(-10).map((c) => ({
        author: c.created_by_name || c.created_by || '?',
        content: c.content,
      }));
      const quotedSrc = options?.replyTo != null
        ? (current.comments || []).find((c) => String(c.id) === String(options.replyTo))
        : undefined;
      const quotedComment = quotedSrc
        ? {
            id: quotedSrc.id,
            author: quotedSrc.created_by_name || quotedSrc.created_by || '?',
            content: quotedSrc.content,
          }
        : undefined;
      await taskDiscussStream(
        {
          task_id: String(current.id),
          query: text.replace(/\s*@U老师\s*/g, ' ').trim(),
          context: {
            recent_comments: recentComments,
            ...(quotedComment ? { quoted_comment: quotedComment } : {}),
            ...(options?.replyTo != null ? { reply_to: options.replyTo } : {}),
            ...(options?.uspEnvId != null ? { usp_env_id: options.uspEnvId } : {}),
          },
        },
        {
          onToken: (tok) => {
            if (!tok) return;
            setAiStreamReply((prev) => prev + tok);
          },
          onRewrite: (full) => setAiStreamReply(full || ''),
          onResult: () => { ok = true; },
        },
        controller.signal,
      );
      if (ok) {
        Toast({ message: 'AI 已回复', theme: 'success' });
        loadDetail();
        return true;
      }
      Toast({ message: 'AI 回复失败', theme: 'error' });
      return false;
    } catch (err) {
      const aborted = err instanceof Error && err.name === 'AbortError';
      if (aborted) return false;
      Toast({ message: `AI 回复失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      return false;
    } finally {
      const stillMine = aiAbortRef.current === controller;
      if (stillMine) {
        aiAbortRef.current = null;
        setAskingAI(false);
        setAiStreamReply('');
        aiBusyRef.current = false;
        pumpDiscussQueue();
      }
    }
  };

  const handleAIDiscuss = async (text: string, files: File[] = [], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    if (!detailRef.current) return false;
    if (aiBusyRef.current || diagnosingRef.current) {
      if (discussQueueRef.current.length >= MAX_AI_DISCUSS_QUEUE) {
        Toast({ message: `排队已满（最多 ${MAX_AI_DISCUSS_QUEUE} 条），等当前排查结束后再 @U老师`, theme: 'warning' });
        return false;
      }
      discussQueueRef.current.push({ text, files, options });
      bumpAiQueue();
      Toast({ message: `已排到结束后跟进（${discussQueueRef.current.length}/${MAX_AI_DISCUSS_QUEUE}），本轮结束后再开始`, theme: 'success' });
      return true;
    }
    return startDiscussTurn(text, files, options);
  };

  const handleInsertThisRound = async (text: string, files: File[] = [], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
    const current = detailRef.current;
    if (!current) return false;
    if (aiBusyRef.current || diagnosingRef.current) {
      await postDiscussUserComment(text, files, options);
      try {
        const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/discuss/inject`, {
          method: 'POST',
          body: JSON.stringify({
            task_id: String(current.id),
            text: text.replace(/\s*@U老师\s*/g, ' ').trim(),
          }),
        });
        const data = await res.json();
        if (data.code === 0) {
          Toast({ message: '已插入本轮排查', theme: 'success' });
          return true;
        }
        Toast({ message: data.message || '插入本轮失败', theme: 'error' });
        return false;
      } catch (err) {
        Toast({ message: `插入本轮失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        return false;
      }
    }
    return startDiscussTurn(text, files, options);
  };

  const handleInsertQueueItem = async (index: number): Promise<boolean> => {
    const job = discussQueueRef.current[index];
    if (!job) return false;
    discussQueueRef.current.splice(index, 1);
    bumpAiQueue();
    const ok = await handleInsertThisRound(job.text, job.files, job.options);
    if (!ok) {
      discussQueueRef.current.splice(index, 0, job);
      bumpAiQueue();
    }
    return ok;
  };

  const handleRemoveQueueItem = (index: number) => {
    if (index < 0 || index >= discussQueueRef.current.length) return;
    discussQueueRef.current.splice(index, 1);
    bumpAiQueue();
    Toast({ message: '已取消排队', theme: 'success' });
  };

  // ── onSend：检测是否 @U老师（任意位置，前缀或句尾均触发）决定走普通评论还是 AI 讨论 ──
  const handleSendComment = async (text: string, files: File[], options?: { replyTo?: string | number; uspEnvId?: number }): Promise<boolean> => {
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
    const prevAbort = aiAbortRef.current;
    const controller = new AbortController();
    aiAbortRef.current = controller;
    prevAbort?.abort();
    aiBusyRef.current = true;
    diagnosingRef.current = true;
    setAiEpoch((n) => n + 1);
    setAskingAI(false);
    setDiagnosing(true);
    try {
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/diagnose`, {
        method: 'POST',
        signal: controller.signal,
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
      const aborted = err instanceof Error && err.name === 'AbortError';
      if (!aborted) {
        Toast({ message: `分析失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    } finally {
      const stillMine = aiAbortRef.current === controller;
      if (stillMine) {
        aiAbortRef.current = null;
        setDiagnosing(false);
        diagnosingRef.current = false;
        aiBusyRef.current = false;
        pumpDiscussQueue();
      }
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
        // 二次派单感知增强：派单理由（后端对接单人/提单人/管理员返回 reasoning，非空即展示）
        setDispatchReason(t.redispatch?.result?.reasoning || '');
        setLastReassignReason(t.redispatch?.reassign_reason || '');
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

  if (!detail && detailLoading) {
    return (
      <div>
        <Navbar title="工单详情" fixed leftArrow onLeftClick={handleBack} />
        <div style={{ padding: 32, textAlign: 'center', marginTop: 56 }}>
          <Loading text="加载中…" />
        </div>
      </div>
    );
  }
  if (!detail) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={handleBack} />
      <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted-foreground)', marginTop: 56 }}>工单不存在</div>
    </div>
  );

  const specDocRoles = getCurrentUserRoles();
  // 与后端 spec_doc._can_edit 口径一致：管理员 / 处理人 / 提单人 / 被代理人(已确认跟进)
  const canEditSpecDoc =
    isAdmin || specDocRoles.isAssignee || specDocRoles.isReporter || specDocRoles.isPrincipal;

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
                data-testid="task-status"
                data-status={detail.status?.toLowerCase()}
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
                <Fragment key={index}>
                  {action.nextStatus === 'closed' && <span data-testid="task-close" hidden />}
                  {action.nextStatus === 'resolved' && <span data-testid="task-resolve" hidden />}
                  <AppButton
                    size="small"
                    theme={action.theme as 'primary' | 'default' | 'danger' | 'light'}
                    onClick={() => {
                      if (action.actionType === 'resume') {
                        setShowResumePopup(true);
                      } else if (action.actionType === 'reopen') {
                        // 未解决打回：选择重新开始的阶段 + 节点时间，阶段性处理从头开始
                        const firstStep = [...negotiation.stepTemplate].sort((a, b) => a.sequence - b.sequence)[0];
                        negotiation.setReopenStepId(firstStep ? firstStep.id : null);
                        negotiation.setReopenEndTime(null);
                        negotiation.setShowReopenPopup(true);
                      } else if (action.actionType === 'archive') {
                        handleStartArchive();
                      } else if (action.actionType === 'archive-edit') {
                        handleOpenArchiveEditor();
                      } else if (action.actionType === 'archive-approve') {
                        handleArchiveApprove();
                      } else if (action.actionType === 'archive-reject') {
                        setArchiveRejectReason('');
                        setShowArchiveRejectPopup(true);
                      } else if (action.nextStatus === 'resolved') {
                        // 结束工单（→ resolved）→ 打开 "问题 + AI 解决方式" 确认弹窗
                        resolve.handleResolveClick();
                      } else if (action.nextStatus === 'unsolvable_requested') {
                        // 标记无法解决 → 先弹理由输入弹窗
                        setUnsolvableReason('');
                        setShowUnsolvablePopup(true);
                      } else {
                        handleStatusChange(action);
                      }
                    }}
                    className="detail-card__action-btn"
                    style={action.customStyle}
                  >
                    {action.label}
                  </AppButton>
                </Fragment>
              ))}
            </div>
          </div>
          {/* 代他人提单：关系横幅（代理人「你代 X 提交」/ 被代理人「X 代你提交」+ 确认跟进） */}
          {proxyRelation && (
            <ProxyRelationBanner
              ticketId={detail.id}
              relation={proxyRelation}
              onChanged={(updated) => {
                setProxyRelation(updated);
                // 关系变更影响可操作项（pending 只读 → acknowledged 获协办权），拉一次详情统一刷新
                loadDetail();
              }}
            />
          )}
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
                      <span data-testid="task-assignee" className="detail-info-item__value task-card2__person-name--dispatching">
                        <i className="dispatch-pulse dispatch-pulse--inline" />派单中
                      </span>
                    );
                  }
                  return (
                    <span data-testid="task-assignee" className="detail-info-item__value">
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
                  {detail.deadline_at ? formatRawDateTime(detail.deadline_at) : '未设置'}
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
                <span className="detail-info-item__label">当前协商预期时间</span>
                <span className="detail-info-item__value">{detail.curr_step_endtime ? formatRawDateTime(detail.curr_step_endtime) : '—'}</span>
              </div>
            </div>
          </div>

          {/* 二次派单感知增强（M3）：未派到指定人时的完整话术（与「我要摇人」历史详情同口径） */}
          {redispatchTipDetail && (() => {
            const { isAssignee, isReporter } = getCurrentUserRoles();
            const tipLabel = isAssignee && !isReporter ? '接单提醒' : '派单提醒';
            return <DispatchFold label={tipLabel} text={redispatchTipDetail} variant="tip" />;
          })()}

          {/* 派单原因 / 转派原因：接单人 / 提单人 / 管理员 / 工单操作权限 可见。
              提单人侧有 tip 时 tip 常已含说明，不重复展示 reason；
              接单人可同时看「接单提醒」与「派单原因」。
              转派原因放这一块（**不放工单动态**）——两者都是「为什么派给这个人」的说明。 */}
          {(() => {
            const { isAssignee, isReporter } = getCurrentUserRoles();
            const canOperate = isAdmin || hasPermission('backend:tasks:operate');
            if (!isAssignee && !isReporter && !canOperate) return null;
            // 「有 tip 就不重复展示」只针对**派单原因** —— tip 讲的是这次派单。
            // 转派 tip 里根本没有，不能被连坐挡掉（否则提单人永远看不到转派原因）。
            const showDispatch = !!dispatchReason && !(redispatchTipDetail && isReporter);
            const showReassign = !!lastReassignReason;
            if (!showDispatch && !showReassign) return null;
            return (
              <>
                {showDispatch ? (
                  <DispatchFold label="派单原因" text={dispatchReason} variant="reason" />
                ) : null}
                {showReassign ? (
                  <DispatchFold label="转派原因" text={lastReassignReason} variant="reason" />
                ) : null}
              </>
            );
          })()}
        </div>

        <div className="detail-card">
          {/* 标题行右侧常驻「详细问题文档」入口（问题描述为空也展示，弹窗内再判断有无文档/权限） */}
          <h4 className="detail-card__h detail-card__h--with-action">
            问题描述
            <SpecDocCard taskId={detail.id} canEdit={canEditSpecDoc} />
          </h4>
          <SafeHtml className="detail-card__body detail-card__body--pre" html={detail.description || '<p style="color:var(--muted-foreground)">无描述</p>'} />
        </div>

        {/* 工单阶段性处理（协商节点）：抽到共享组件 StepNegotiationCard，与历史工单详情页复用。
            审核型工单（metadata_info 含 approval_type）不展示，走下方专门的审核卡片。 */}
        {!approvalInfo && (
          <StepNegotiationCard
            negotiation={negotiation}
            detail={detail}
            // 被代理人仅在已确认跟随后才参与协商（pending 只读，决策 10）
            roles={{
              ...getCurrentUserRoles(),
              isPrincipal: getCurrentUserRoles().isPrincipal && proxyRelation?.relation_status === 'acknowledged',
            }}
            onResolve={resolve.handleResolveClick}
            onEscalate={(round, maxRound) => {
              setEscalateUser(null);
              setEscalateReason(`已达最大协商回合（${round}/${maxRound}），申请升级介入处理。`);
              setShowEscalatePopup(true);
            }}
            onReassign={() => {
              setReassignUser(null);
              setReassignReason('');
              setShowReassignPopup(true);
            }}
            onPauseResponse={(nextStatus, rejectReason) => handleStatusChange({ nextStatus, rejectReason })}
            onUnsolvableResponse={(nextStatus, rejectReason) => handleStatusChange({ nextStatus, rejectReason })}
          />
        )}

        {/* 公司/部门/项目审核入口：工单 metadata_info 含 approval_type 时展示卡片（所有人可见信息），
            仅工单处理人或有 backend:tasks:operate 权限的管理员可点击通过/驳回按钮 */}
        {approvalInfo && (
          <div className="detail-card" style={{ border: '1.5px solid var(--blue-4)' }}>
            <h4 className="detail-card__h" style={{ color: 'var(--blue-2)', display: 'flex', alignItems: 'center', gap: 6 }}>
              {approvalInfo.type === 'new_company' ? (
                <Building2 size={15} strokeWidth={2} />
              ) : approvalInfo.type === 'new_department' ? (
                <Store size={15} strokeWidth={2} />
              ) : (
                <FolderKanban size={15} strokeWidth={2} />
              )}
              {approvalInfo.type === 'new_company'
                ? '新公司录入审核'
                : approvalInfo.type === 'new_department'
                  ? '新部门录入审核'
                  : '新项目录入审核'}
            </h4>
            <div style={{ fontSize: 13, color: 'var(--foreground)', lineHeight: 1.8, marginBottom: 16 }}>
              {approvalInfo.type === 'new_project' ? (
                <>
                  <div><strong>项目名称：</strong>{approvalInfo.targetName}</div>
                  {approvalInfo.targetCode && <div><strong>项目代码：</strong>{approvalInfo.targetCode}</div>}
                  {approvalInfo.customer && <div><strong>客户信息：</strong>{approvalInfo.customer}</div>}
                  {approvalInfo.region && <div><strong>项目区域：</strong>{approvalInfo.region}</div>}
                </>
              ) : (
                <>
                  <div>
                    <strong>{approvalInfo.type === 'new_company' ? '公司名称' : '部门名称'}：</strong>
                    {approvalInfo.targetName}
                  </div>
                  {approvalInfo.companyName && (
                    <div><strong>所属公司：</strong>{approvalInfo.companyName}</div>
                  )}
                </>
              )}
              <div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}>
                请审核以下信息，通过后该{approvalLabelMap[approvalInfo.type]}将对所有用户可见
              </div>
            </div>
            {(getCurrentUserRoles().isAssignee || hasPermission('backend:tasks:operate')) ? (
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
                onClick={() => {
                  setAdjustName(approvalInfo.targetName);
                  setAdjustCode(approvalInfo.targetCode);
                  setShowAdjustPopup(true);
                }}
                style={{ borderRadius: '999px' }}
              >
                调整后通过
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
            ) : (
              <div style={{ fontSize: 12, color: 'var(--muted-foreground)', textAlign: 'center', padding: '8px 0' }}>
                审核操作由工单处理人或管理员完成
              </div>
            )}
          </div>
        )}

        {/* 项目信息补充工单（提单人请他人补项目信息时生成）：列出待补充节点，给回项目信息树的入口。
            数据来自建单时写入的 metadata_info.info_supplement；普通工单没有该字段，卡片不出现。 */}
        {(() => {
          const meta = detail.metadata_info || {};
          const raw = meta.info_supplement as
            | { project_id?: string; project_name?: string; nodes?: Array<{ id?: string; path?: string }> }
            | undefined;
          if (!raw || !Array.isArray(raw.nodes) || raw.nodes.length === 0) return null;
          const paths = raw.nodes
            .map((item) => (item && typeof item.path === 'string' ? item.path : ''))
            .filter(Boolean);
          if (!paths.length) return null;
          const projectId = String(raw.project_id || '');
          return (
            <div className="detail-card">
              <h4 className="detail-card__h" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <ClipboardList size={15} strokeWidth={2} />
                项目信息补充
              </h4>
              <div style={{ fontSize: 13, color: 'var(--foreground)', lineHeight: 1.8 }}>
                <div><strong>项目：</strong>{raw.project_name || projectId || '—'}</div>
                <div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 4 }}>
                  待补充 {paths.length} 项，补齐后他人提单即可自动带全背景信息：
                </div>
                <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                  {paths.map((path, index) => (
                    <li key={`${path}-${index}`}>{path}</li>
                  ))}
                </ul>
              </div>
              {projectId ? (
                <Button
                  block
                  theme="primary"
                  style={{ borderRadius: '999px', marginTop: 12, backgroundColor: 'var(--blue-3)', color: '#fff', border: 'none' }}
                  onClick={() => navigate(`/app/admin/project-detail/${encodeURIComponent(projectId)}/edit`)}
                >
                  去补充项目信息
                </Button>
              ) : null}
            </div>
          );
        })()}

        <TicketDynamicsCard taskId={detailId ?? ''} />

        {(() => {
          const meta = detail.metadata_info || {};
          const rs = typeof meta.resolution_summary === 'string' ? meta.resolution_summary as string : '';
          const isTerminal = ['resolved', 'closed'].includes((detail.status || '').toLowerCase());
          if (!isTerminal || !rs.trim()) return null;
          return (
            <div className="detail-card">
              <h4 className="detail-card__h">解决方式</h4>
              <div className="detail-card__body detail-card__body--pre">
                {rs}
              </div>
            </div>
          );
        })()}

        {/* 关联工单 */}
        {detail && (
          <RelationBlock
            taskId={Number(detail.id)}
            projectName={detail.project_name}
            projectId={detail.project_id}
            customer={detail.customer}
            ticketType={detail.ticket_type}
            parentPriority={detail.priority}
            parentDeadlineAt={detail.deadline_at ?? null}
            canOperate={hasPermission('backend:tasks:operate')}
            blockedError={blockedError}
          />
        )}

        <div className="detail-card">
          <h4 className="detail-card__h">讨论摘要</h4>
          {aiSummary ? (
            <SafeHtml className="detail-card__body detail-card__body--pre" html={aiSummary} />
          ) : (
            <p className="detail-card__body detail-card__body--muted">暂无摘要，U老师 将自动总结讨论进展</p>
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
          sending={submittingComment}
          optimisticAi={diagnosing || askingAI}
          onAbortAi={abortAi}
          aiEpoch={aiEpoch}
          aiQueueItems={aiQueueItems}
          onInsertQueueItem={handleInsertQueueItem}
          onRemoveQueueItem={handleRemoveQueueItem}
          aiStreamReply={aiStreamReply}
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
          focusCommentId={focusDiscussion ? focusCommentId : null}
          focusAuthor={focusDiscussion ? focusAuthor : null}
        />

        {(() => {
          const status = detail.status?.toLowerCase();
          const isClosedOrCanceledOrArchivedOrArchiving = ['closed', 'canceled', 'cancelled', 'archived', 'archiving'].includes(status);
          if (isClosedOrCanceledOrArchivedOrArchiving) return null;

          const { isAssignee, isReporter, isPrincipal } = getCurrentUserRoles();
          const canOperate = hasPermission('backend:tasks:operate');
          const assigneeOnlyStatuses = ['new', 'in_progress', 'pending', 'paused'];
          // 已解决：被代理人（已确认跟进）与提单人同权，可确认关闭（与后端 roles.can_close 对齐）
          const showRoleActions = canOperate || (assigneeOnlyStatuses.includes(status) ? isAssignee : (status === 'resolved' ? (isReporter || isPrincipal) : false));
          // 已解决状态：提单人仅可修改工单/升级上报，不应退回工单或重新指派
          // 退回工单/重新指派应由处理人在非已解决状态下操作
          const isResolved = status === 'resolved';
          // 达到最大回合：升级上报强制可见（提单人/接单人任一），替代管理员介入
          const round = detail.step_negotiation_round ?? 0;
          const maxRound = detail.step_neg_max_rounds ?? 3;
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
                    {!isResolved && (
                      <Button size="small" theme="default" onClick={() => { setReturnReason(''); setShowReturnConfirmPopup(true); }}>退回工单</Button>
                    )}
                    {!isResolved && (
                      <Button size="small" theme="default" onClick={() => { setReassignUser(null); setReassignReason(''); setShowReassignPopup(true); }}>重新指派</Button>
                    )}
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
                    title={priorityDisabled ? '仅待处理工单可修改优先级' : undefined}
                    aria-label={priorityDisabled ? `优先级${label}（仅待处理工单可修改优先级）` : `优先级${label}`}
                    className={`tasks-create-modal__radio-btn ${editForm.priority === value ? 'is-active' : ''} ${priorityDisabled ? 'is-disabled' : ''}`}
                    onClick={() => {
                      const r = getDeadlineRange(value, detail?.created_at);
                      setEditForm((p) => ({ ...p, priority: value, ...(r ? { curr_step_endtime: r.max.toISOString() } : {}) }));
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
            {/* 当前阶段截止时间：antd DatePicker 下拉选择（双端可用），浮层 z-index 高于编辑弹窗避免被遮挡 */}
            <div className="ticket-edit-form__field">
              <label className="ticket-edit-form__label">当前阶段截止时间</label>
              <DatePicker
                style={{ width: '100%' }}
                placeholder="点击选择"
                format="YYYY-MM-DD HH:00"
                showTime={{ defaultValue: editDeadlineRange?.max ?? dayjs().hour(9).minute(0), format: 'HH:00', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={(trigger) => trigger.parentElement || document.body}
                value={editFormDeadlineValue}
                disabledDate={editDeadlineRange ? makeDisabledDate(editDeadlineRange.min) : undefined}
                disabledTime={editDeadlineRange ? makeDisabledTime(editDeadlineRange.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  setEditForm((p) => ({
                    ...p,
                    curr_step_endtime: d ? d.minute(0).second(0).millisecond(0).toISOString() : undefined,
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
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px' }}>请选择升级对象</p>
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
          <h4 className="ticket-edit__title">继续处理</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px' }}>选择新的受理人（可不选，直接确认则保持原处理人）</p>
          <UserSelect value={resumeUser?.id ?? null} onChange={setResumeUser} placeholder="请选择受理人（可选）" title="选择受理人" />
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowResumePopup(false); setResumeUser(null); }}>取消</Button>
            <Button theme="primary" onClick={handleResume}>确认继续</Button>
          </div>
        </div>
      </Popup>

      {/* 标记无法解决弹窗：输入理由（提单人将看到并决定） */}
      <Popup visible={showUnsolvablePopup} onClose={() => { setShowUnsolvablePopup(false); setUnsolvableReason(''); }} placement="bottom" showOverlay destroyOnClose>
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">标记无法解决工单</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            无法解决理由将展示给提单人，对方确认后工单进入「无法解决」终态；驳回则回到处理中继续处理。
          </p>
          <Form initialData={{}}>
            <FormItem label="无法解决理由" name="unsolvableReason" labelAlign="top" requiredMark>
              <Textarea
                value={unsolvableReason}
                onChange={(v) => setUnsolvableReason(String(v))}
                placeholder="请说明为什么无法解决工单（例如：问题本身超出能力范围 / 所需资源无法获取 / 客户需求不清晰）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowUnsolvablePopup(false); setUnsolvableReason(''); }}>取消</Button>
            <Button
              theme="primary"
              disabled={!unsolvableReason.trim()}
              onClick={async () => {
                await handleStatusChange({ nextStatus: 'unsolvable_requested', unsolvableReason: unsolvableReason.trim() });
                setShowUnsolvablePopup(false);
                setUnsolvableReason('');
              }}
            >
              提交无法解决请求
            </Button>
          </div>
        </div>
      </Popup>

      {/* 结束工单确认弹窗：问题 + 工单解决方式（抽到 useResolveTicket） */}
      <Popup visible={resolve.showResolutionPopup} onClose={resolve.handleResolveCancel} placement="bottom" showOverlay>
        <div className="ticket-edit-form">
          <div className="ticket-edit-form__header">
            <span className="ticket-edit-form__title">确认完成工单</span>
            <span className="ticket-edit-form__close" onClick={resolve.handleResolveCancel}>×</span>
          </div>
          <div className="ticket-edit-form__body">
            {/* 工单问题 */}
            <div className="ticket-edit-form__field">
              <span className="ticket-edit-form__label">📌 工单问题</span>
              <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--foreground)', marginBottom: '4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{detail?.title}</div>
              <div style={{
                fontSize: '13px', color: 'var(--muted-foreground)', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
              }}>
                {detail?.description || <span style={{ color: 'var(--gray-light)' }}>（无描述）</span>}
              </div>
            </div>

            {/* 工单解决方式 */}
            <div className="ticket-edit-form__field">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span className="ticket-edit-form__label" style={{ marginBottom: 0 }}>✅ 工单解决方式</span>
                {resolve.resolutionFailed && (
                  <span style={{ color: 'var(--apricot)', fontSize: '12px' }}>自动总结出错，请手动补充</span>
                )}
              </div>
              {resolve.resolutionLoading || resolve.resolutionPolling ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '20px 0', color: 'var(--muted-foreground)', fontSize: '13px', justifyContent: 'center' }}>
                  <Loading size="20px" /> 正在生成解决方式…
                </div>
              ) : (
                <Textarea
                  data-testid="task-resolution-summary"
                  value={resolve.resolutionText}
                  onChange={(v) => resolve.setResolutionText(String(v))}
                  placeholder={
                    resolve.resolutionNoSolution
                      ? '当前没有解决方案，请在此补充实际解决方式'
                      : resolve.resolutionFailed
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
            <Button theme="default" onClick={resolve.handleResolveCancel}>取消</Button>
            <Button
              theme={resolve.resolutionFailed ? 'danger' : 'default'}
              onClick={resolve.resolutionFailed ? resolve.handleRetryResolution : resolve.handleGenerateResolution}
              loading={resolve.resolutionLoading || resolve.resolutionPolling}
              disabled={resolve.resolutionSubmitting || resolve.resolutionLoading || resolve.resolutionPolling}
            >
              {resolve.resolutionFailed ? '重试' : '帮我生成'}
            </Button>
            <>
              <span data-testid="task-resolve-confirm" hidden />
              <Button
                theme="primary"
                onClick={resolve.handleConfirmResolve}
                disabled={resolve.resolutionSubmitting || resolve.resolutionLoading || resolve.resolutionPolling || !resolve.resolutionText.trim()}
              >
                确认完成
              </Button>
            </>
          </div>
        </div>
      </Popup>


      <Popup visible={showReassignPopup} onClose={() => { setShowReassignPopup(false); setReassignUser(null); setReassignReason(''); setReassignKind(''); }} placement="bottom" showOverlay destroyOnClose>
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">重新指派</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px' }}>选择新的处理人</p>
          <UserSelect
            value={reassignUser?.id ?? null}
            onChange={setReassignUser}
            placeholder="请选择处理人"
            title="选择处理人"
            pinUserId={detail?.project_contact_person_id || null}
            pinLabel="项目对接人"
          />
          <div style={{ margin: '12px 0 8px', fontSize: '14px', color: 'var(--foreground)' }}>转派类型<span style={{ color: 'var(--danger)' }}> *</span></div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
            {([
              { id: 'misassign' as const, label: '派错了', hint: '不该派给当前处理人，同类单会学习' },
              { id: 'stage' as const, label: '阶段转派', hint: '做到这个阶段该换人，不进入学习' },
              { id: 'other' as const, label: '其它', hint: '太忙、请假等，不进入学习' },
            ]).map((opt) => {
              const on = reassignKind === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setReassignKind(opt.id)}
                  style={{
                    textAlign: 'left',
                    padding: '10px 12px',
                    borderRadius: 'var(--radius-md)',
                    border: on ? '1px solid var(--primary)' : '1px solid var(--border)',
                    background: on ? 'var(--primary-soft)' : 'var(--card)',
                    cursor: 'pointer',
                  }}
                >
                  <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--foreground)' }}>{opt.label}</div>
                  <div style={{ fontSize: '12px', color: 'var(--muted-foreground)', marginTop: '2px' }}>{opt.hint}</div>
                </button>
              );
            })}
          </div>
          <Form initialData={{}}>
            <FormItem label="变更原因" name="reassignReason" labelAlign="top">
              <Textarea
                value={reassignReason}
                onChange={(v) => setReassignReason(String(v))}
                placeholder="选填，补充说明（派错了时写上更有助于下次派准）"
                autosize={{ minRows: 3, maxRows: 6 }}
                maxlength={500}
              />
            </FormItem>
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" onClick={() => { setShowReassignPopup(false); setReassignUser(null); setReassignReason(''); setReassignKind(''); }}>取消</Button>
            <Button theme="primary" onClick={handleReassign} disabled={!reassignUser || !reassignKind}>确认指派</Button>
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
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
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
          <h4 className="ticket-edit__title">修改当前阶段截止时间</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            仅调整本工单的当前阶段截止时间，不影响其他字段；清空后表示不设置截止时间。
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
                value={deadlineDraftValue}
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
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            确定要将此工单退回吗？<br />
            退回后工单状态将变更为<span style={{ color: 'var(--apricot)', fontWeight: 500 }}>挂起</span>，处理人变更为创建人。
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

      {/* 未解决打回弹窗：选择重新开始的阶段 + 节点结束时间，阶段性处理从头开始 */}
      <Popup
        visible={negotiation.showReopenPopup}
        onClose={() => { if (!negotiation.submittingReopen) negotiation.setShowReopenPopup(false); }}
        placement="bottom"
        showOverlay
        destroyOnClose
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">标记未解决</h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            工单将打回到「处理中」，阶段性处理从头再开始：请选择重新开始的阶段及节点结束时间（SLA），打回后等待处理人确认。
          </p>
          <div style={{ marginBottom: 12 }}>
            <label style={{ display: 'block', fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 4 }}>
              重新开始的阶段<span style={{ color: 'var(--danger)' }}>*</span>
            </label>
            <select
              value={negotiation.reopenStepId ?? ''}
              onChange={(e) => negotiation.setReopenStepId(e.target.value ? Number(e.target.value) : null)}
              style={{
                width: '100%', padding: '8px 10px', fontSize: 14,
                border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
                background: 'var(--card)', color: 'var(--foreground)',
              }}
            >
              {[...negotiation.stepTemplate].sort((a, b) => a.sequence - b.sequence).map((s) => (
                <option key={s.id} value={s.id}>{`第${s.sequence + 1}步 · ${s.step_name}`}</option>
              ))}
            </select>
          </div>
          {(() => {
            // 选择下限 = 工单创建时间，与当前阶段截止时间编辑口径一致
            const range = getDeadlineRange(detail?.priority, detail?.created_at);
            return (
              <DatePicker
                style={{ width: '100%', marginBottom: 12 }}
                placeholder="点击选择节点结束时间"
                format="YYYY-MM-DD HH:mm"
                showTime={{ defaultValue: dayjs().hour(18).minute(0), format: 'HH:mm', showNow: false }}
                showNow={false}
                placement="topLeft"
                getPopupContainer={() => document.body}
                value={reopenEndTimeValue}
                disabledDate={range ? makeDisabledDate(range.min) : undefined}
                disabledTime={range ? makeDisabledTime(range.min) : undefined}
                onChange={(d: dayjs.Dayjs | null) =>
                  negotiation.setReopenEndTime(d ? d.second(0).millisecond(0).toISOString() : null)
                }
                allowClear
                styles={{ popup: { root: { zIndex: 13000 } } }}
              />
            );
          })()}
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={negotiation.submittingReopen} onClick={() => negotiation.setShowReopenPopup(false)}>取消</Button>
            <Button theme="primary" loading={negotiation.submittingReopen} onClick={negotiation.handleReopenStep} disabled={!negotiation.reopenEndTime || !negotiation.reopenStepId}>确认打回</Button>
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
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            确定要驳回{approvalInfo ? approvalLabelMap[approvalInfo.type] : '该对象'}「{approvalInfo?.targetName}」的录入申请吗？
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

      {/* 调整名称 / 项目代码弹窗 */}
      <Popup
        visible={showAdjustPopup}
        onClose={() => { if (!approving) { setShowAdjustPopup(false); setAdjustName(''); setAdjustCode(''); } }}
        placement="bottom"
        showOverlay
      >
        <div className="ticket-edit">
          <h4 className="ticket-edit__title">
            调整{approvalInfo?.type === 'new_project' ? '项目' : approvalInfo?.type === 'new_company' ? '公司' : '部门'}信息
          </h4>
          <p style={{ color: 'var(--muted-foreground)', fontSize: '13px', marginBottom: '12px', lineHeight: 1.6 }}>
            修改{approvalInfo ? approvalLabelMap[approvalInfo.type] : ''}信息后审核通过（留空表示不调整）：
          </p>
          <Form initialData={{}}>
            <FormItem label={approvalInfo?.type === 'new_project' ? '项目名称' : '名称'} name="adjustName" labelAlign="top">
              <ClearableInput
                value={adjustName}
                onChange={(v) => setAdjustName(String(v))}
                placeholder={approvalInfo?.type === 'new_project' ? '请输入新的项目名称' : '请输入新的名称'}
                maxlength={64}
              />
            </FormItem>
            {approvalInfo?.type === 'new_project' && (
              <FormItem label="项目代码" name="adjustCode" labelAlign="top">
                <ClearableInput
                  value={adjustCode}
                  onChange={(v) => setAdjustCode(String(v))}
                  placeholder="请输入新的项目代码"
                  maxlength={32}
                />
              </FormItem>
            )}
          </Form>
          <div className="ticket-edit__btns">
            <Button theme="default" disabled={approving} onClick={() => { setShowAdjustPopup(false); setAdjustName(''); setAdjustCode(''); }}>取消</Button>
            <Button theme="primary" loading={approving} onClick={handleAdjustApprove} disabled={!adjustName.trim() && !adjustCode.trim()}>确认通过</Button>
          </div>
        </div>
      </Popup>

      {/* ── 归档报告编辑器 Popup ── */}
      <Popup
        visible={archiveEditorVisible}
        onClose={() => { setArchiveEditorVisible(false); setArchiveSimilar([]); setArchiveReportPreview(false); }}
        placement="bottom"
        showOverlay
      >
        {/* ORS-1010：编辑器+相似工单块易超 88vh，ticket-edit-form 类本身不滚动
            （滚动在其 __body 子层，本弹窗未用）——底部内容曾被硬裁（1010 用户
            iOS 实测提示语只剩半句）。内联开启滚动。 */}
        <div className="ticket-edit-form" style={{ display: 'flex', flexDirection: 'column', maxHeight: '88vh', overflow: 'hidden' }}>
          <span className="ticket-edit-form__close" onClick={() => { setArchiveEditorVisible(false); setArchiveSimilar([]); setArchiveReportPreview(false); }}>×</span>
          <h4 style={{ margin: '0 0 12px', fontSize: 15 }}>
            {archiveEditorMode === 'review' ? '归档报告审核' : (detail?.current_archive_report?.submit_status === 'rejected' ? '修改归档报告' : '编写归档报告')}
          </h4>
          {archiveEditorMode === 'edit' && detail?.current_archive_report?.review_comment && (
            <div style={{ padding: '8px 12px', background: 'var(--destructive)/10%', borderRadius: 6, marginBottom: 12, fontSize: 13 }}>
              <strong style={{ color: 'var(--destructive)' }}>上一次驳回理由：</strong>
              {detail.current_archive_report.review_comment}
            </div>
          )}
          {archiveEditorMode === 'edit' ? (
            <>
              {/* ORS-1010 精简：章节要求压成一行 + 常用操作（补齐章节/上传图片），
                  原格式工具栏整排删除——报告写作用不上，还把正文挤出首屏。
                  只有报告填写区滚动（flex 中段），相似块与按钮行固定 */}
              <div style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
          {/* ORS-1010 相似工单提示：归档人对照已沉淀的相似工单（与 AI 初稿并行
              拉取）。不展示相似度分值——score 是 RRF 融合分非余弦，展示数字会
              误导。verified=rejected 已被推翻，不出示。0 条且检索正常时也提示
              （功能存在感 + 讲清沉淀机制）；检索挂了保持静默。 */}
          {(archiveSimilar.length > 0 || archiveSimilarLoading || archiveSimilarReady) && (
            <div style={{
              paddingTop: 12, borderTop: '1px solid var(--border, #e8eaee)', flexShrink: 0,
              maxHeight: '40vh', overflowY: 'auto', overscrollBehavior: 'contain',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 600 }}>相似工单</span>
                <span style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)' }}>
                  {archiveSimilarLoading
                    ? '检索中…'
                    : archiveSimilar.length > 0
                      ? `发现 ${archiveSimilar.length} 张，供对照参考`
                      : '经验库中暂无相似的已沉淀工单'}
                </span>
              </div>
              {archiveSimilar.map((s) => {
                const expanded = expandedSimilar.has(s.task_id);
                return (
                  <div
                    key={s.task_id}
                    onClick={() => setExpandedSimilar((prev) => {
                      const next = new Set(prev);
                      if (next.has(s.task_id)) next.delete(s.task_id);
                      else next.add(s.task_id);
                      return next;
                    })}
                    style={{ padding: '10px 0', borderBottom: '1px dashed var(--border, #e8eaee)', cursor: 'pointer' }}
                  >
                    {/* 窄屏两行式：行1 = 单号+徽章+展开符（标题不参与挤一行）；行2 = 标题独占整行可换行 */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--brand, #266fe8)', flexShrink: 0 }}>#{s.task_id}</span>
                      {s.verified === 'confirmed' && (
                        <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#e3f9e9', color: '#1f8a5a', flexShrink: 0 }}>✓ 已验证</span>
                      )}
                      {s.verified === 'recurred' && (
                        <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 4, background: '#fff4e0', color: '#b8860b', flexShrink: 0 }}>↻ 曾复发</span>
                      )}
                      <span style={{ fontSize: 11, color: 'var(--muted-foreground, #98a2b3)', marginLeft: 'auto', flexShrink: 0 }}>{expanded ? '收起 ▴' : '展开 ▾'}</span>
                    </div>
                    <div style={{ fontSize: 13, marginTop: 3, lineHeight: 1.5, wordBreak: 'break-word', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{s.title}</div>
                    {s.project_name && (
                      <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)', marginTop: 2 }}>
                        {s.project_name}
                      </div>
                    )}
                    {expanded && s.summary && (
                      <pre style={{ marginTop: 8, padding: '8px 10px', background: 'var(--muted, #f6f8fa)', borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit', fontSize: 12, lineHeight: 1.7, color: 'var(--foreground)', maxHeight: 200, overflowY: 'auto' }}>{s.summary}</pre>
                    )}
                    {expanded && !!s.image_paths?.length && (
                      <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                        {s.image_paths.map((p) => (
                          <SimilarThumb key={p} objectPath={p} />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
              {archiveSimilar.length === 0 && !archiveSimilarLoading && (
                <div style={{ fontSize: 12, color: 'var(--muted-foreground, #98a2b3)', lineHeight: 1.7, wordBreak: 'break-word' }}>
                  本单归档审核通过后也会沉淀进经验库，供以后遇到同类问题的同事参考。
                </div>
              )}
              {archiveSimilar.length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--muted-foreground, #98a2b3)', marginTop: 6 }}>
                  点条目展开查看问题 / 根因 / 解决
                </div>
              )}
            </div>
          )}

              {/* ORS-1010 精简：章节要求一句话 + 操作按钮独立一行（上下结构——
                  横排会把文字压成七行细条，1021 用户实测丑） */}
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 12, lineHeight: 1.6, color: 'var(--muted-foreground)' }}>
                  须含「问题描述」与「解决方案/回答」两部分且各有正文；请勿写入人员姓名及联系方式。
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                  <Button size="small" theme="default" disabled={archiveGenerating} onClick={() => setArchiveReportContent(completeArchiveReportTemplate(archiveReportContent, String(detail?.ticket_type || '')))}>补齐章节</Button>
                  <Button size="small" theme="default" disabled={archiveGenerating || uploadingReportImage} onClick={() => reportImageInputRef.current?.click()}>
                    {uploadingReportImage ? '上传中…' : '📷 上传图片'}
                  </Button>
                  <Button size="small" theme="primary" disabled={archiveGenerating} onClick={handleAiGenerateReport}>
                    ✨AI 初稿
                  </Button>
                  <input
                    ref={reportImageInputRef}
                    type="file"
                    accept="image/*"
                    multiple
                    style={{ display: 'none' }}
                    onChange={handleReportImageUpload}
                  />
                </div>
              </div>
              {archiveGenerating ? (
                /* AI 生成中：只读直播（渲染版——报告内嵌图片随流式内容出现），
                    防止处理人同时手写（服务端 content='' 条件写回兜底） */
                <div style={{ height: 380, border: '1px dashed var(--border, #d8e0ea)', borderRadius: 6, padding: '12px 16px', overflow: 'auto' }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
                    <span className="loading" style={{ width: 12, height: 12, display: 'inline-block', border: '2px solid var(--brand, #266fe8)', borderTopColor: 'transparent', borderRadius: '50%', animation: 'spin 1s linear infinite' }} />
                    <b style={{ fontSize: 14 }}>AI 正在为您总结归档报告…</b>
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginBottom: 8 }}>{archiveGenStatus}</div>
                  {archiveReportContent && (
                    <MarkdownRenderer content={archiveReportContent} streaming />
                  )}
                </div>
              ) : archiveReportPreview ? (
                /* ORS-1010 预览态：渲染版（图片可见），可切回编辑 */
                <div style={{ height: 380, overflowY: 'auto', border: '1px solid var(--border, #d8e0ea)', borderRadius: 6, padding: '12px 16px' }}>
                  <MarkdownRenderer content={archiveReportContent} />
                </div>
              ) : (
                // ORS-1010 精简：MDEditor 工具栏整排删除，写作不需要二十个格式图标
                <MDEditor
                  commands={[]}
                  extraCommands={[]}
                  height={380}
                  value={archiveReportContent}
                  onChange={(v) => setArchiveReportContent(v || '')}
                  preview="edit"
                />
              )}
              </div>
              {!archiveGenerating && (
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 8 }}>
                  <span
                    onClick={() => setArchiveReportPreview((v) => !v)}
                    style={{ fontSize: 12, color: 'var(--brand, #266fe8)', cursor: 'pointer' }}
                  >
                    {archiveReportPreview ? '✎ 切换到编辑' : '👁 切换到预览（看图片）'}
                  </span>
                </div>
              )}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingTop: 12 }}>
                <Button size="small" theme="default" onClick={handleSaveArchiveDraft} loading={archiveLoading} disabled={archiveGenerating}>保存草稿</Button>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>提交后系统自动指定审核人</span>
                  <Button size="small" theme="primary" onClick={handleSubmitArchive} loading={archiveLoading} disabled={archiveGenerating}>提交审核</Button>
                </div>
              </div>
            </>
          ) : (
            <>
              {/* ORS-1035：报告用 MarkdownRenderer 展示——内嵌的相对路径图片
                  （attachments/download）走 AuthImage 带登录态加载；MDEditor
                  的 img 无鉴权渲染会 401 */}
              <MarkdownRenderer content={archiveReportContent} />
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12, marginTop: 16 }}>
                <Button size="small" theme="default" onClick={() => { setArchiveEditorVisible(false); setArchiveSimilar([]); setArchiveReportPreview(false); }}>关闭</Button>
                <Button size="small" theme="primary" onClick={handleArchiveApprove} loading={archiveLoading}>通过归档</Button>
                <Button size="small" theme="danger" onClick={() => { setArchiveEditorVisible(false); setArchiveSimilar([]); setArchiveReportPreview(false); setArchiveRejectReason(''); setShowArchiveRejectPopup(true); }}>驳回归档</Button>
              </div>
            </>
          )}
        </div>
      </Popup>

      {/* ── 驳回归档 Popup ── */}
      <Popup visible={showArchiveRejectPopup} onClose={() => setShowArchiveRejectPopup(false)} placement="bottom" showOverlay>
        <div className="ticket-edit-form">
          <span className="ticket-edit-form__close" onClick={() => { setShowArchiveRejectPopup(false); setArchiveRejectReason(''); }}>×</span>
          <h4 style={{ margin: '0 0 12px', fontSize: 15 }}>驳回归档报告</h4>
          <textarea
            value={archiveRejectReason}
            onChange={(e) => setArchiveRejectReason(e.target.value)}
            placeholder="请填写驳回理由（必填）"
            style={{ width: '100%', minHeight: 100, padding: 8, borderRadius: 6, border: '1px solid var(--border)', background: 'var(--background)', color: 'var(--foreground)', resize: 'vertical', boxSizing: 'border-box' }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
            <Button size="small" theme="default" onClick={() => { setShowArchiveRejectPopup(false); setArchiveRejectReason(''); }}>取消</Button>
            <Button size="small" theme="danger" onClick={handleArchiveReject} loading={archiveLoading} disabled={!archiveRejectReason.trim()}>确认驳回</Button>
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
              urlTransform={appUrlTransform}
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
