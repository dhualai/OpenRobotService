// 摇人 · 历史工单详情页（U老师诊断生成的工单）
// 数据源：tasks 服务 GET /api/tasks/{dbId}?load_comments=true（DB id 唯一定位，AI 诊断数据从 metadata_info 提取）；操作：催办 / 上报（任务服务通知）
// 路由 /app/call/ticket/:id 中的 :id 形如 db_<数字id>（Task.id）；session_id 直链仅作旧链接兼容
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { Navbar, Button, Toast, Loading, Tag, Popup, Textarea, DialogPlugin } from 'tdesign-mobile-react';
import AppButton from '@/shared/components/AppButton';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import ClearableInput from '@/shared/components/ClearableInput';
import TitleEllipsis from '@/shared/components/TitleEllipsis';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
import { WECHAT_CONFIG } from '@/config/wechat';
import { ArrowRight, Folder, UserRound, Clock, AlarmClock, Download, FileImage, FileText, FileSpreadsheet, FileCode, FileArchive, Paperclip, Bell, Upload, Undo2, Pencil } from 'lucide-react';
import { getMyProjects, getProjectMembers, type ProjectItem, type ProjectMember } from '@/api/projects';
import { qaGetTicket, fetchWithAuth } from '@/api/ai';
import { cancelTicket, urgeTicket, reportTicket, uploadCommentAttachment } from '@/api/ticket';
import {
  isTerminalTicketStatus,
  canUrgeTicket,
  canReportTicket,
  canShowCancelButton,
  canCancelTicketByUser,
  canEditPriority,
  STATUS_DISPLAY_MAP,
} from '@/shared/constants/ticket';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import DiscussionPanel from '@/shared/components/DiscussionPanel';
import UserSelect from '@/shared/components/UserSelect';
import SafeHtml from '@/shared/components/SafeHtml';
import { isSameUser } from '@/shared/utils/userIdentity';
import { useAuthStore } from '@/stores/auth';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';
import { dedupeFileNames } from '@/shared/utils/uniqueFileNames';
import { formatDateTime, formatRawDateTime } from '@/shared/utils/url';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime, parseDeadlineString } from '@/shared/utils/deadline';
import type { UserItem } from '@/api/users';

interface AiDiagnosis {
  problem_summary?: string;
  hypotheses?: string[];
  ruled_out?: string[];
  collected_info?: Record<string, unknown>;
  rounds?: number;
}
interface Comment { id: string; content: string; created_by_name?: string; created_by?: string; created_at: string; attachments?: Array<string | { path?: string; filename?: string; size?: number }>; reply_to?: string | number; quoted?: { id: string | number; content: string; created_by_name?: string }; }
interface AiTicket {
  ticket_id?: string;
  session_id: string;
  type?: string;
  title?: string;
  description?: string;
  priority?: string;
  status?: string;
  contact?: string;
  // 统一为 ISO 字符串（AI 接口 task_to_dict 与 DB TicketResponse 均已显式 isoformat）
  created_at?: string;
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
  // 所属项目编码（DB TicketResponse 返回，编辑回显与提交用）
  project_id?: string;
  // 当前阶段截止时间（ISO 字符串，编辑弹窗 antd DatePicker 回显/编辑；详情页只读展示。tasks 详情接口返回蛇形 curr_step_endtime）
  curr_step_endtime?: string | null;
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
  const location = useLocation();
  const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
  const { username, userId, name, isAdmin } = useAuthStore();

  const [ticket, setTicket] = useState<AiTicket | null>(null);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);
  const [aiSummary, setAiSummary] = useState('');
  // 二次派单感知增强（M3）：未派到指定人时的完整情商话术（详情页 redispatch.result.tip_detail）
  const [redispatchTipDetail, setRedispatchTipDetail] = useState('');
  const tempIdRef = useRef<string>(typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `t_${Date.now()}_${Math.random().toString(36).slice(2)}`);
  const [viewer, setViewer] = useState<AttachmentViewItem | null>(null);
  // 项目成员（用于讨论区 @ 提及）
  const [projectMembers, setProjectMembers] = useState<ProjectMember[]>([]);
  // 全部在职用户（项目成员 + 项目外，@ 输入过滤字时可 @ 到项目外的人）
  const [allUsers, setAllUsers] = useState<ProjectMember[]>([]);
  // @U老师 AI 讨论中标记
  const [askingAI, setAskingAI] = useState(false);

  // 竞态保护：fetchDetail 是异步多段 await，切换工单（sessionId 变化）时上一个工单的请求可能仍在飞行中，
  // 其响应若晚于新工单返回，会 setTicket 覆盖新工单、或用 setTicket((prev)=>...) 把旧工单字段合并进新工单，
  // 表现为「点的是当前工单，显示/接口却是别的工单」。用 ref 跟踪最新 sessionId，await 后比对发起时的快照，
  // 不一致即视为 stale 请求丢弃结果（不 setTicket/setMsg/setLoading）。同 sessionId 下的 silent 刷新
  // （编辑后 / 派单轮询）不受影响——仅当真正切换工单时才使旧请求失效。
  const latestSessionIdRef = useRef(sessionId);
  latestSessionIdRef.current = sessionId;

  const fetchDetail = useCallback(async (silent = false) => {
    if (!sessionId) return;
    // 捕获本次请求发起时的 sessionId；后续每个 await 后用它对比最新值，判定是否已被新工单取代
    const mySessionId = sessionId;
    const isStale = () => latestSessionIdRef.current !== mySessionId;
    if (!silent) setLoading(true);
    try {
      // 统一按 DB 工单 id 查询（URL 形如 /call/ticket/db_<数字id>）。
      // 列表点击一律用 db_<Task.id> 导航：session_id 在同一会话多次转单时会重复
      // （external_id 用 ticket_seq 区分，DB 唯一约束在 (source, external_id) 而非 session_id），
      // 用 session_id 导航会命中 qaGetTicket 的歧义返回（精确匹配取首条 / LIKE 兜底取最新），
      // 表现为「点的是当前工单，显示却是别的工单」。DB id 唯一定位，彻底消除歧义。
      // AI 工单的 session_id / diagnosis / ai_summary 等存在 metadata_info JSON 列，从此处提取；
      // 下方 qaGetTicket 分支仅作旧链接（session_id 直链）兼容，主流程不再走它。
      const dbIdMatch = /^db_(\d+)$/.exec(sessionId);
      if (dbIdMatch) {
        const dbId = dbIdMatch[1];
        const taskDetail = await request<{ comments: Comment[]; metadata_info?: { ai_summary?: string; session_id?: string; diagnosis?: AiDiagnosis }; status?: string; created_by?: string; created_by_name?: string; assigned_to?: string; assigned_to_name?: string; title?: string; description?: string; priority?: string; ticket_type?: string; customer?: string; project_name?: string; project_id?: string; created_at?: string; curr_step_endtime?: string; redispatch?: { result?: { tip_detail?: string | null } } | null }>(`/${dbId}?load_comments=true`, { skipCache: true });
        if (isStale()) return; // 已切换到别的工单，丢弃本次（旧工单）结果，避免覆盖
        // 二次派单感知增强（M3）：完整情商话术（未派到指定人时）
        setRedispatchTipDetail(taskDetail.redispatch?.result?.tip_detail || '');
        setTicket({
          ticket_id: String(dbId),
          session_id: taskDetail.metadata_info?.session_id || '',
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
          // 当前阶段截止时间：tasks 详情接口 GET /{id} 返回蛇形 curr_step_endtime（见 TicketResponse）
          curr_step_endtime: taskDetail.curr_step_endtime ?? null,
          // AI 诊断数据存在 metadata_info.diagnosis（task_adapter 平铺入库）；手动工单无此字段
          diagnosis: taskDetail.metadata_info?.diagnosis,
          // 附件来自 tasks 服务 GET /{dbId} 的 attachments 字段（object_path 或字典数组）
          attachments: ((taskDetail as unknown as { attachments?: unknown[] }).attachments as Array<Record<string, unknown>> | undefined) ?? [],
        });
        setAiSummary(typeof taskDetail.metadata_info?.ai_summary === 'string' ? taskDetail.metadata_info.ai_summary : '');
        return;
      }
      const res = await qaGetTicket(sessionId);
      if (isStale()) return; // 已切换到别的工单，丢弃本次（旧工单）结果，避免覆盖
      if (res?.code === 0 && res.data) {
        const aiTicket = res.data as AiTicket;
        // silent 刷新（编辑后 / 派单轮询）不重置 ticket：避免被 AI 滞后的 Redis 副本覆盖，
        // 仅由下方 DB 覆盖可编辑字段；diagnosis 等 AI 特有数据保持现有值不动。
        if (!silent) setTicket(aiTicket);
        if (aiTicket.ticket_id) {
          try {
            const taskDetail = await request<{ comments: Comment[]; metadata_info?: { ai_summary?: string }; status?: string; created_by?: string; created_by_name?: string; assigned_to?: string; assigned_to_name?: string; title?: string; description?: string; priority?: string; ticket_type?: string; customer?: string; project_name?: string; project_id?: string; created_at?: string; curr_step_endtime?: string; redispatch?: { result?: { tip_detail?: string | null } } | null }>(`/${aiTicket.ticket_id}?load_comments=true`, { skipCache: true });
            if (isStale()) return; // 已切换工单：prev 可能已是新工单，不可把旧工单的 DB 字段合并进去
            // 二次派单感知增强（M3）：完整情商话术随 DB 刷新
            setRedispatchTipDetail(taskDetail.redispatch?.result?.tip_detail || '');
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
              // 创建时间以 DB 真实创建时间为准（DB 的 created_at 覆盖 AI 接口返回值，与 status/created_by 同口径）。
              created_at: taskDetail.created_at ?? prev.created_at,
              title: taskDetail.title || prev.title,
              description: taskDetail.description ?? prev.description,
              priority: taskDetail.priority || prev.priority,
              type: taskDetail.ticket_type || prev.type,
              contact: taskDetail.customer || prev.contact,
              project_name: taskDetail.project_name || prev.project_name || prev.project,
              // 项目编码以 DB 为准（编辑回显与提交用），AI 接口不返回该字段
              project_id: taskDetail.project_id || prev.project_id,
              // 当前阶段截止时间以 DB 为准（tasks 详情接口蛇形 curr_step_endtime），覆盖 AI 滞后副本
              curr_step_endtime: taskDetail.curr_step_endtime ?? prev.curr_step_endtime ?? null,
            } : prev);
            setAiSummary(typeof taskDetail.metadata_info?.ai_summary === 'string' ? taskDetail.metadata_info.ai_summary : '');
          } catch { /* 评论加载失败不阻塞主流程 */ }
        }
      } else {
        if (isStale()) return; // 已切换工单，不覆盖新工单的 msg 状态
        setMsg(res?.message || '该会话尚未生成工单');
      }
    } catch (err) {
      if (isStale()) return; // 已切换工单，旧工单的报错不覆盖新工单状态
      setMsg(err instanceof Error ? err.message : '加载失败');
    } finally {
      // 仅当本次请求仍是最新工单时才结束 loading，避免 stale 请求把新工单的 loading 提前置 false
      if (!silent && !isStale()) setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => { fetchDetail(); }, [fetchDetail]);

  // 获取项目成员用于 @ 提及（与系统任务详情页同款逻辑）
  useEffect(() => {
    const tid = ticket?.ticket_id;
    if (!tid) { setProjectMembers([]); setAllUsers([]); return; }
    getProjectMembers(tid)
      .then((members) => {
        const reporterUsername = ticket?.created_by || '';
        const sorted = [...members].sort((a, b) => {
          if (a.username === reporterUsername) return -1;
          if (b.username === reporterUsername) return 1;
          return 0;
        });
        setProjectMembers(sorted);
      })
      .catch(() => setProjectMembers([]));
    // 获取全部在职用户（@ 输入过滤字时扩展到项目外的人）
    getProjectMembers(tid, true)
      .then((u) => setAllUsers(u))
      .catch(() => setAllUsers([]));
  }, [ticket?.ticket_id, ticket?.created_by]);

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

  // 操作人标签（与系统任务详情页同款）
  const getOperatorLabel = (): string => name || username || '当前用户';

  // WS 工单状态变更（派单完成/改派/状态流转）实时更新详情，替代轮询
  const handleWsTaskUpdated = (patch: { status?: string; assigned_to?: string | null; assigned_to_name?: string | null }) => {
    setTicket((prev) => {
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

  // 操作日志评论：记录到 task_comments，工单处理过程可追溯（与系统任务详情页同款逻辑）
  const addOperationComment = async (content: string) => {
    if (!ticket?.ticket_id) return;
    try {
      await request(`/${ticket.ticket_id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content, is_public: true }),
      });
    } catch { /* 评论记录失败不阻塞主流程 */ }
  };

  const handleActionConfirm = async () => {
    if (!ticket?.ticket_id || !actionUser) { Toast({ message: '请选择通知用户', theme: 'warning' }); return; }
    setActing(actionType);
    try {
      const operator = getOperatorLabel();
      const target = actionUser.name || actionUser.username;
      if (actionType === 'urge') {
        await urgeTicket(ticket.ticket_id, actionUser.id);
        await addOperationComment(`${operator} 催办了工单，通知 ${target}`);
        Toast({ message: '已催办，已通知处理人', theme: 'success' });
      } else {
        await reportTicket(ticket.ticket_id, actionUser.id);
        await addOperationComment(`${operator} 上报了工单，通知 ${target}`);
        Toast({ message: '已上报，已通知上级', theme: 'success' });
      }
      setShowActionPopup(false);
      await fetchDetail(true);
    } catch (err) {
      Toast({ message: `${actionType === 'urge' ? '催办' : '上报'}失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setActing(null);
    }
  };

  // 撤回：二次确认后，将工单状态置为已取消（Canceled）
  const doCancel = async () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法撤回', theme: 'warning' }); return; }
    setActing('cancel');
    try {
      await cancelTicket(ticket.ticket_id);
      const operator = getOperatorLabel();
      await addOperationComment(`${operator} 撤回了工单`);
      Toast({ message: '已撤回，工单已取消', theme: 'success' });
      fetchDetail();
    } catch (err) {
      Toast({ message: `撤回失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setActing(null);
    }
  };
  const handleCancel = () => {
    if (!ticket?.ticket_id) { Toast({ message: '工单号缺失，无法撤回', theme: 'warning' }); return; }
    const dlg = DialogPlugin.confirm!({
      title: '撤回工单',
      content: '撤回后工单将变为「已取消」，确认撤回吗？',
      confirmBtn: '撤回',
      cancelBtn: '再想想',
      onConfirm: () => { doCancel(); dlg.destroy(); },
    });
  };

  // 派单中：AI 单 status=new 且处理人未写入（Worker 60s 轮询派单，期间 5s 轮询自动刷新）
  const isDispatching = !!ticket && ticket.status === 'new' && !ticket.assigned_to && !ticket.assigned_to_name;
  // 派单完成 / 状态变更由 WS task.updated 实时推送（见 DiscussionPanel onTaskUpdated），不再轮询。

  // 编辑工单（标题/描述/优先级/类型/联系人；权限与后端对齐：admin/创建人/处理人，终态不可编辑）
  const [showEdit, setShowEdit] = useState(false);
  const [editForm, setEditForm] = useState<{ title: string; description: string; priority: string; ticket_type: string; project_id: string; project_name: string; curr_step_endtime?: string }>({ title: '', description: '', priority: '中', ticket_type: 'problem', project_id: '', project_name: '' });
  // 当前阶段截止时间区间：基准 = 工单创建时间（ticket.created_at），而非用户操作时刻
  const editDeadlineRange = getDeadlineRange(editForm.priority, ticket?.created_at);
  // 优先级仅在「尚未派单」（新建/待派单）可修改；已派单及后续状态禁止（置灰不可点）
  const priorityDisabled = !canEditPriority(ticket?.status);
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
    && (isAdmin || isSameUser(ticket.created_by, userId, username) || isSameUser(ticket.assigned_to, userId, username));
  const openEdit = () => {
    if (!ticket) return;
    setEditForm({
      title: ticket.title || '',
      description: ticket.description || '',
      priority: toEnPriority(ticket.priority),
      ticket_type: ticket.type || 'problem',
      project_id: ticket.project_id || '',
      project_name: ticket.project_name || ticket.project || '',
      curr_step_endtime: ticket.curr_step_endtime || undefined,
    });
    setShowEdit(true);
  };
  // ── 当前阶段截止时间：编辑弹窗用 antd DatePicker 下拉选择（双端可用）──
  // 浮层 z-index 通过 styles.popup.root 提到高于 tdesign 编辑弹窗（z-index 11500），避免被遮挡
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
          curr_step_endtime: editForm.curr_step_endtime || null,
        }),
      });
      const operator = getOperatorLabel();
      await addOperationComment(`${operator} 修改了工单信息`);
      Toast({ message: '已保存', theme: 'success' });
      setShowEdit(false);
      await fetchDetail(true);  // 静默刷新，展示端以 DB 为准，编辑后立即可见（await 确保 DB 新值落定）
    } catch (err) {
      Toast({ message: `保存失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSavingEdit(false);
    }
  };

  // ── @U老师 讨论：先存用户消息 → 调 POST /api/ai/task/discuss → 重新加载评论；返回 true=成功 ──
  // 与系统任务详情页同款逻辑，用 ticket.ticket_id 代替 detail.id，fetchDetail(true) 代替 loadDetail()
  const handleAIDiscuss = async (text: string, files: File[] = [], options?: { replyTo?: string | number }): Promise<boolean> => {
    if (!ticket?.ticket_id) return false;
    const userMsg = text;
    setAskingAI(true);
    try {
      // 上传附件（同名文件自动改名，避免后端对象名重复覆盖）
      const tempId = tempIdRef.current;
      const uploads = dedupeFileNames(files);
      for (const f of uploads) {
        await uploadCommentAttachment(f, tempId);
      }
      // 1. 先保存用户的 @U老师 消息到 task_comments
      try {
        const newComment = await request<Comment>(`/${ticket.ticket_id}/comments`, {
          method: 'POST',
          body: JSON.stringify({ content: userMsg, is_public: true, attachments: files.length ? [tempId] : [], reply_to: options?.replyTo }),
        });
        setTicket((prev) => {
          if (!prev) return prev;
          const updatedComments = prev.comments ? [...prev.comments, newComment] : [newComment];
          return { ...prev, comments: updatedComments };
        });
      } catch { /* 保存用户消息失败不阻塞 AI 调用 */ }
      // 2. 调 AI 讨论
      const recentComments = (ticket.comments || []).slice(-10).map((c) => ({
        author: c.created_by_name || c.created_by || '?',
        content: c.content,
      }));
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/discuss`, {
        method: 'POST',
        body: JSON.stringify({
          task_id: String(ticket.ticket_id),
          // 去掉文本中任意位置的 @U老师 标记（可能有空格/重复），保留整段话作为 query，
          // 兼容"先说话、句尾@U老师"的场景（否则 @U老师 在尾部时 query 会带残留或丢失）
          query: userMsg.replace(/\s*@U老师\s*/g, ' ').trim(),
          context: { recent_comments: recentComments },
        }),
      });
      const data = await res.json();
      if (data.code === 0) {
        Toast({ message: 'AI 已回复', theme: 'success' });
        await fetchDetail(true);  // 静默刷新评论（含 AI 回复）
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

  // 发送评论（附件上传 + POST /api/tasks/{ticket_id}/comments）；返回 true=成功（组件清空输入）
  // 检测 @U老师（任意位置，前缀或句尾均触发）：走 AI 讨论而非普通评论（与系统任务详情页同款逻辑）
  const handleSendComment = async (text: string, files: File[], options?: { replyTo?: string | number }): Promise<boolean> => {
    // 只要文本里含 @U老师（@ 在开头/中间/结尾都算）就走 AI 讨论；
    // 兼容"说完话后句尾手动@U老师"（否则会被当成普通评论发出、AI 不回复）
    if (text.includes('@U老师')) {
      return handleAIDiscuss(text, files, options);
    }
    if (!ticket?.ticket_id) {
      Toast({ message: '工单号缺失，无法评论', theme: 'warning' });
      return false;
    }
    setSubmittingComment(true);
    try {
      const tempId = tempIdRef.current;
      // 先逐个上传附件（temp_id 关联，后端登记到 comment_attachment_map）
      // 同名文件自动改名，避免后端对象名重复覆盖
      const uploads = dedupeFileNames(files);
      for (const f of uploads) {
        await uploadCommentAttachment(f, tempId);
      }
      const newComment = await request<Comment>(`/${ticket.ticket_id}/comments`, {
        method: 'POST',
        body: JSON.stringify({ content: text, is_public: true, attachments: files.length ? [tempId] : [], reply_to: options?.replyTo }),
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

  // 删除评论（后端按创建人鉴权）：成功后从本地列表移除
  const handleDeleteComment = async (id: string | number): Promise<void> => {
    if (!ticket?.ticket_id) return;
    await request(`/comments/${id}`, { method: 'DELETE' });
    setTicket((prev) => {
      if (!prev) return prev;
      return { ...prev, comments: (prev.comments || []).filter((c) => String(c.id) !== String(id)) };
    });
    Toast({ message: '评论已删除', theme: 'success' });
  };

  if (loading) return <Loading text="加载中..." />;
  if (!ticket) return (
    <div>
      <Navbar title="工单详情" fixed leftArrow onLeftClick={() => (location.key !== 'default' ? navigate(-1) : navigate('/call/history'))} />
      <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted-foreground)', marginTop: 56 }}>{msg || '工单不存在'}</div>
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
    // 必须拼成绝对 URL：微信内 window.open(相对URL) 打开的是微信内置 WebView，无法下载；
    // 用户「在浏览器打开」后相对路径在外部浏览器解析失败会落到 SPA 404 → 未登录重定向微信 OAuth
    // （表现为「提示跳转到微信客户端」）。绝对 URL 携带 token，在外部浏览器可直接下载。
    const origin = typeof window !== 'undefined' ? window.location.origin : '';
    return `${origin}${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(minioPath)}&filename=${encodeURIComponent(att.filename || 'download')}&token=${encodeURIComponent(authToken)}`;
  };

  /** 构造附件内联预览 URL（/api/tasks/files/{minioPath}），供缩略图 <img> src 与 AttachmentViewer 共用 */
  const buildPreviewUrl = (att: NormalizedAttachment): string | null => {
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

  const openAttachmentViewer = (att: NormalizedAttachment) => {
    const previewUrl = buildPreviewUrl(att);
    if (!previewUrl) { Toast({ message: '附件路径无效', theme: 'error' }); return; }
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
        onLeftClick={() => (location.key !== 'default' ? navigate(-1) : navigate('/call/history'))}
      />
      <div className="page-container" style={{ paddingTop: 56 }}>
        {/* 标题 + 基本信息 */}
        <div className="detail-card">
          <div className="detail-card__meta">
            {/* 类型胶囊（设计稿 04：浅蓝 blue-soft/blue-2） */}
            {ticket.type && (
              <Tag theme="default" style={{ background: 'var(--blue-soft)', color: 'var(--blue-2)', border: 'none', fontWeight: 600, fontSize: 11.5, borderRadius: 999 }}>
                {TYPE_LABEL[ticket.type] || ticket.type}
              </Tag>
            )}
            {/* 优先级胶囊（蓝阶，与列表页一致） */}
            {ticket.priority && (
              <Tag theme="default" style={{ background: 'var(--secondary)', color: 'var(--blue-2)', border: 'none', fontWeight: 600, fontSize: 11.5, borderRadius: 999 }}>
                {displayPriority(ticket.priority)}
              </Tag>
            )}
            {/* 状态胶囊（设计稿：bg-secondary text-blue-2） */}
            <Tag
              theme="default"
              style={{ background: 'var(--secondary)', color: 'var(--blue-2)', border: 'none', fontWeight: 600, fontSize: 11.5, borderRadius: 999 }}
            >
              {STATUS_DISPLAY_MAP[(ticket.status || '').toLowerCase()] || ticket.status || '待派单'}
            </Tag>
            <span className="detail-card__id">{ticket.ticket_id || ''}</span>
          </div>
          <h2 className="detail-card__title"><TitleEllipsis text={ticket.title || '(无标题)'} lines={3} titleClassName="detail-card__title-inner" as="span" fontSize={19} lineHeight={1.3} /></h2>
          {/* 元信息网格（设计稿 04：2×2 MetaItem，lucide 图标 + 标签 + 值） */}
          <div className="detail-card__info-grid">
            {(ticket.project_name || ticket.project) && (
              <div className="detail-info-item">
                <span className="detail-info-item__icon"><Folder size={14} strokeWidth={2} /></span>
                <div className="detail-info-item__content">
                  <span className="detail-info-item__label">所属项目</span>
                  <span className="detail-info-item__value">{ticket.project_name || ticket.project || ''}</span>
                </div>
              </div>
            )}
            {ticket.contact && (
              <div className="detail-info-item">
                <span className="detail-info-item__icon"><UserRound size={14} strokeWidth={2} /></span>
                <div className="detail-info-item__content">
                  <span className="detail-info-item__label">联系人</span>
                  <span className="detail-info-item__value">{ticket.contact}</span>
                </div>
              </div>
            )}
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><Clock size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">创建时间</span>
                <span className="detail-info-item__value">{ticket.created_at ? formatDateTime(ticket.created_at) : ''}</span>
              </div>
            </div>
            <div className="detail-info-item">
              <span className="detail-info-item__icon"><AlarmClock size={14} strokeWidth={2} /></span>
              <div className="detail-info-item__content">
                <span className="detail-info-item__label">当前阶段截止时间</span>
                <span className="detail-info-item__value">{ticket.curr_step_endtime ? formatRawDateTime(String(ticket.curr_step_endtime)) : '未设置'}</span>
              </div>
            </div>
          </div>
        </div>

        {/* 人员流转：发起人 → 处理人（与历史工单列表页同款 task-card2__people 样式）
            人员信息来自 tasks 服务 GET /{ticket_id}；AI 未生成工单(无 ticket_id)时该字段缺失则不显示。
            派单中（status=new 且处理人未写入）：处理人位显示「派单中」呼吸动效，5s 轮询自动更新 */}
        {(ticket.created_by || ticket.created_by_name || ticket.assigned_to || ticket.assigned_to_name || isDispatching) && (
          <div className="detail-card">
            <div className="task-card2__people">
              <div className="task-card2__person task-card2__person--creator" title={`发起人：${ticket.created_by_name || ticket.created_by || '-'}`} aria-label={`发起人：${ticket.created_by_name || ticket.created_by || '-'}`}>
                <span className="task-card2__avatar">{(ticket.created_by_name || ticket.created_by || '?').slice(0, 1).toUpperCase()}</span>
                <span className="task-card2__person-text">
                  <span className="task-card2__person-label">发起人</span>
                  <span className="task-card2__person-name">{ticket.created_by_name || ticket.created_by || '-'}</span>
                </span>
              </div>
              <span className="task-card2__person-arrow"><ArrowRight size={16} strokeWidth={2} /></span>
              {isDispatching ? (
                <div className="task-card2__person task-card2__person--assignee" title="U老师 正在派单，稍候自动更新" aria-label="U老师 正在派单，稍候自动更新">
                  <span className="task-card2__avatar task-card2__avatar--assignee task-card2__avatar--dispatching"><i className="dispatch-pulse" /></span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">处理人</span>
                    <span className="task-card2__person-name task-card2__person-name--dispatching">派单中</span>
                  </span>
                </div>
              ) : (
                <div className="task-card2__person task-card2__person--assignee" title={`处理人：${ticket.assigned_to_name || ticket.assigned_to || '-'}`} aria-label={`处理人：${ticket.assigned_to_name || ticket.assigned_to || '-'}`}>
                  <span className="task-card2__avatar task-card2__avatar--assignee">{(ticket.assigned_to_name || ticket.assigned_to || '?').slice(0, 1).toUpperCase()}</span>
                  <span className="task-card2__person-text">
                    <span className="task-card2__person-label">处理人</span>
                    <span className="task-card2__person-name">{ticket.assigned_to_name || ticket.assigned_to || '-'}</span>
                  </span>
                </div>
              )}
            </div>
            {/* 二次派单感知增强（M3）：未派到指定人时的完整情商话术（仅 matched_pref=false 时有） */}
            {redispatchTipDetail && (
              <div className="redispatch-tip-detail">派单说明：{redispatchTipDetail}</div>
            )}
          </div>
        )}

        {/* 问题描述 */}
        {ticket.description && (
          <div className="detail-card">
            <h4 className="detail-card__h">问题描述</h4>
            <div style={{ whiteSpace: 'pre-wrap', color: 'var(--muted-foreground)', fontSize: 12.5, lineHeight: '24px' }}>{ticket.description}</div>
          </div>
        )}



        {/* 工单附件（图片缩略图网格 + 非图片文件卡片；复用统一 AttachmentViewer，与系统任务页一致）*/}
        {ticket.attachments && ticket.attachments.length > 0 && (
          <div className="detail-card">
            <h4 className="detail-card__h">附件 ({ticket.attachments.length})</h4>
            {(() => {
              const items = ticket.attachments
                .map((rawAtt) => normalizeAttachment(rawAtt))
                .filter((a): a is NormalizedAttachment => a !== null);
              const imgItems = items.filter((a) => isImageFile(a.filename || ''));
              const fileItems = items.filter((a) => !isImageFile(a.filename || ''));
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
                  {/* 非图片文件卡片（lucide 图标 + 文件名 + 下载） */}
                  {fileItems.length > 0 && (
                    <div className="detail-attachment-files">
                      {fileItems.map((att, idx) => {
                        const filename = att.filename || '未命名文件';
                        const size = att.size ?? 0;
                        const sizeLabel = formatFileSize(size);
                        const dl = buildAttachmentDownloadUrl(att);
                        return (
                          <div
                            key={`file-${idx}`}
                            role="button"
                            tabIndex={0}
                            className="detail-attachment-file"
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
                            >
                              <Download size={16} strokeWidth={2} />
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

        {/* AI 讨论摘要（与系统任务共用 tasks 表 metadata_info.ai_summary）*/}
        {ticket?.ticket_id && (
          <div className="detail-card">
            <h4 className="detail-card__h">讨论摘要</h4>
            {aiSummary ? (
              <SafeHtml html={aiSummary} />
            ) : (
              <p style={{ color: 'var(--muted-foreground)', fontSize: 12.5, lineHeight: '24px' }}>暂无摘要，U老师 将自动总结讨论进展</p>
            )}
          </div>
        )}

        <DiscussionPanel
          comments={ticket.comments || []}
          onSend={handleSendComment}
          onDeleteComment={handleDeleteComment}
          sending={submittingComment || askingAI}
          disabled={!ticket?.ticket_id}
          enableAttach
          enableAI
          mentionUsers={projectMembers}
          mentionAllUsers={allUsers}
          taskId={ticket?.ticket_id}
          onTaskUpdated={handleWsTaskUpdated}
        />

        {/* 操作：与历史工单列表页完全一致 —— 终态（已解决/已取消/已关闭）整组不显示；
            新建/待处理可催办、撤回；处理中仅可上报；不可用按钮禁用；
            正在操作的按钮单独禁用（acting 标记当前动作） */}
        {!isTerminalTicketStatus(ticket.status) && (
          <div className="detail-actions__btns">
            <AppButton
              tone="primary" size="small" icon={<Bell size={13} strokeWidth={2} />}
              disabled={!canUrgeTicket(ticket.status) || acting === 'urge'}
              title={canUrgeTicket(ticket.status) ? undefined : '仅新建/待处理工单可催办'}
              aria-label={canUrgeTicket(ticket.status) ? undefined : '催办（仅新建/待处理工单可催办）'}
              onClick={() => openActionPopup('urge')}
            >催办</AppButton>
            <AppButton
              tone="primary" size="small" icon={<Upload size={13} strokeWidth={2} />}
              disabled={!canReportTicket(ticket.status) || acting === 'report'}
              title={canReportTicket(ticket.status) ? undefined : '仅处理中工单可上报'}
              aria-label={canReportTicket(ticket.status) ? undefined : '上报（仅处理中工单可上报）'}
              onClick={() => openActionPopup('report')}
            >上报</AppButton>
            {canShowCancelButton(ticket.status) && canCancelTicketByUser(ticket.created_by, username, isAdmin, userId) && (
            <AppButton
              tone="muted" size="small" icon={<Undo2 size={13} strokeWidth={2} />}
              disabled={acting === 'cancel'}
              onClick={handleCancel}
            >撤回</AppButton>
            )}
            {canEdit && (
              <AppButton
                tone="primary" size="small" icon={<Pencil size={13} strokeWidth={2} />}
                disabled={savingEdit}
                onClick={openEdit}
              >编辑</AppButton>
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
                    disabled={priorityDisabled}
                    title={priorityDisabled ? '仅新建工单可修改优先级' : undefined}
                    aria-label={priorityDisabled ? `优先级${label}（仅新建工单可修改优先级）` : `优先级${label}`}
                    className={`tasks-create-modal__radio-btn ${editForm.priority === PRIORITY_EN[label] ? 'is-active' : ''} ${priorityDisabled ? 'is-disabled' : ''}`}
                    onClick={() => {
                      const v = PRIORITY_EN[label];
                      const r = getDeadlineRange(v, ticket?.created_at);
                      setEditForm((p) => ({ ...p, priority: v, ...(r ? { curr_step_endtime: r.max.toISOString() } : {}) }));
                    }}
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
                value={editForm.curr_step_endtime ? parseDeadlineString(editForm.curr_step_endtime) : null}
                disabledDate={editDeadlineRange ? makeDisabledDate(editDeadlineRange.min, editDeadlineRange.max) : undefined}
                disabledTime={editDeadlineRange ? makeDisabledTime(editDeadlineRange.min, editDeadlineRange.max) : undefined}
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
            <ClearableInput
              value={projectKeyword}
              onChange={(v) => setProjectKeyword(String(v))}
              placeholder="搜索项目名称 / 编码"
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

