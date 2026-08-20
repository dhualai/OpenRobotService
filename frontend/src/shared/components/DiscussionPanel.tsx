// 工单详情 · 共享讨论区组件
// 历史工单详情页（pages/call/TicketDetailPage）与系统任务工单详情页（pages/tasks/TaskDetailPage）复用。
// 数据共享：两端都走 /api/tasks/{id}/comments（同一工单 → 同一评论流）。
// 布局：当前用户消息靠右（is-right + is-self 蓝气泡），他人靠左。
// 功能开关：enableAttach（附件上传，历史工单用）/ enableAI（@U老师 讨论，系统任务用）。
// 微信化交互：消息引用（长按→引用；气泡内引用块可点击定位原消息）、长按操作菜单（引用/复制/删除）、气泡样式优化。
import { useState, useRef, useEffect, useMemo, useCallback, Fragment } from 'react';
import { Button, Toast, Popover } from 'tdesign-mobile-react';
import { Paperclip, Send } from 'lucide-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import AttachmentViewer, { type AttachmentViewItem } from '@/shared/components/AttachmentViewer';

import { useAuthStore } from '@/stores/auth';
import API_CONFIG from '@/config/api';
import { avatarUrl } from '@/api/profile';
import { parseUtcDate } from '@/shared/utils/url';
import { dedupeFileNames } from '@/shared/utils/uniqueFileNames';
import { useTaskCommentsWS, type OnlineMember } from '@/shared/hooks/useTaskCommentsWS';
import type { AiProgressTodo } from '@/api/ws';

export interface DiscussionComment {
  id: string | number;
  content: string;
  created_by_name?: string;
  created_by?: string;
  created_at: string;
  /** 附件列表：object_path 字符串 或 {path,filename,size} 字典（后端 task_comments.attachments JSON 列两种格式并存） */
  attachments?: Array<string | { path?: string; filename?: string; size?: number }>;
  /** 引用的评论ID（消息引用/回复） */
  reply_to?: string | number;
  /** 被引用评论的简要信息（后端拼装，用于气泡内引用块渲染） */
  quoted?: { id: string | number; content: string; created_by_name?: string };
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp)$/i;
/** 解析评论附件（字符串 object_path 或字典），提取 object_path/filename/isImage */
const parseAttachment = (a: string | { path?: string; filename?: string; size?: number }) => {
  const objectPath = typeof a === 'string' ? a : (a.path || '');
  const filename = typeof a === 'string'
    ? (a.split('/').pop() || a)
    : (a.filename || objectPath.split('/').pop() || '文件');
  return { objectPath, filename, isImage: IMAGE_EXT.test(filename) };
};

/** 去除 HTML 标签，得到引用块/复制用的纯文本摘要 */
const stripHtml = (html: string): string => {
  if (!html) return '';
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return (tmp.textContent || tmp.innerText || '').replace(/\s+/g, ' ').trim();
};

/** 聊天时间分隔格式化：当天显示 HH:MM，非当天显示 M月D日 HH:MM */
const formatChatDividerTime = (dateString: string): string => {
  const date = parseUtcDate(dateString);
  if (!date) return '';
  const now = new Date();
  const isSameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  const pad = (n: number) => String(n).padStart(2, '0');
  const hh = pad(date.getHours());
  const mm = pad(date.getMinutes());
  if (isSameDay) return `${hh}:${mm}`;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${hh}:${mm}`;
};

/** 是否在当前评论前插入居中时间分隔：首条消息或与上一条间隔≥5分钟 */
const shouldShowTimeDivider = (cur: string, prev?: string): boolean => {
  if (!prev) return true;
  const curDate = parseUtcDate(cur);
  const prevDate = parseUtcDate(prev);
  if (!curDate || !prevDate) return true;
  return curDate.getTime() - prevDate.getTime() >= 5 * 60 * 1000;
};

/** 评论时间格式化（姓名旁，非本人消息）：X月X日 HH:MM:SS */
const formatCommentTime = (dateString: string): string => {
  const date = parseUtcDate(dateString);
  if (!date) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};

/** 已读名单的相对时间格式化：刚刚 / N分钟前 / N小时前 / X月X日 HH:MM */
const formatReadTime = (dateString?: string | null): string => {
  if (!dateString) return '';
  const date = parseUtcDate(dateString);
  if (!date) return '';
  const diff = Date.now() - date.getTime();
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (diff < minute) return '刚刚';
  if (diff < hour) return `${Math.floor(diff / minute)}分钟前`;
  if (diff < 24 * hour) return `${Math.floor(diff / hour)}小时前`;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export interface ProjectMember {
  id: string;
  username: string;
  name?: string | null;
  role_name?: string | null;
}

interface DiscussionPanelProps {
  /** 评论列表（两端共用 /api/tasks/{id}/comments 数据） */
  comments: DiscussionComment[];
  /** 发送：父级处理 POST 评论 / @U老师 路由 / 附件上传；返回 true=成功（组件清空输入），false=失败（保留输入）。
   *  options.replyTo 为引用评论ID（消息引用）。 */
  onSend: (text: string, files: File[], options?: { replyTo?: string | number }) => Promise<boolean>;
  /** 发送中（禁用输入与按钮、按钮文案变“发送中”） */
  sending?: boolean;
  /** 整体禁用（如工单号缺失） */
  disabled?: boolean;
  placeholder?: string;
  /** 附件上传（历史工单） */
  enableAttach?: boolean;
  /** @U老师 讨论（系统任务）：点击在输入框前缀 @U老师 */
  enableAI?: boolean;
  /** 消息区点击（系统任务：点诊断报告链接打开弹窗） */
  onMessagesClick?: (e: React.MouseEvent<HTMLDivElement>) => void;
  /** 标题右侧额外内容（如”帮我分析”按钮，仅系统任务用） */
  headerRight?: React.ReactNode;
  /** 标题，默认”讨论（N）” */
  title?: string;
  className?: string;
  /** @提及用户列表（系统任务：项目成员，用于 @ 弹窗选择） */
  mentionUsers?: ProjectMember[];
  /** 删除评论（按创建人鉴权由后端把关）；不传则不显示删除菜单项 */
  onDeleteComment?: (id: string | number) => Promise<void> | void;
  /** 订阅用 taskId（传入即启用 WS 实时评论 / 在线状态 / 输入中 / 已读回执） */
  taskId?: string | number;
  /** 工单状态变更（WS task.updated 推送）：父级据此更新工单字段，替代派单轮询 */
  onTaskUpdated?: (patch: { status?: string; assigned_to?: string | null; assigned_to_name?: string | null }) => void;
  /** 乐观 U老师 执行过程区：为 true 时立即显示占位 todo（无需等 WS running），
   *  收到真实 ai.progress 后用真实数据覆盖。用于 [帮我分析] 这类点击即触发、
   *  但 WS 首条 running 可能稍晚到达的场景，避免过程区“晚出现 / 闪一下”。 */
  optimisticAi?: boolean;
}

export default function DiscussionPanel({
  comments,
  onSend,
  sending = false,
  disabled = false,
  placeholder,
  enableAttach = false,
  enableAI = false,
  onMessagesClick,
  headerRight,
  title,
  className = '',
  mentionUsers,
  onDeleteComment,
  taskId,
  onTaskUpdated,
  optimisticAi = false,
}: DiscussionPanelProps) {
  const { username, name, avatarResourceId } = useAuthStore();
  // 长按操作菜单的浮层由 TDesign Mobile <Popover> 承载（自带箭头/动画/外点关闭）；
  // 通过「透明、pointer-events:none 的代理锚点」定位到被长按气泡的 rect，避免覆盖气泡交互。
  // ── U老师 执行过程（Claude Code 式动态展示）──
  // 后端在 Supervisor 派发能力时逐项推送 ai.progress(phase=running)，全部完成推 phase=done。
  // 执行中在输入框上方渲染过程区；done 收尾后短暂保留再由 sending(false) 隐藏。
  const [aiRunId, setAiRunId] = useState<string | undefined>();
  const [aiTodos, setAiTodos] = useState<AiProgressTodo[]>([]);
  const [aiPhase, setAiPhase] = useState<'running' | 'done'>('done');
  // 过程区是否"活跃"：sending（@U老师 讨论 POST）或 optimisticAi（[帮我分析] diagnose）
  // 任一为 true 都表示有一段 AI 执行正在进行。用于：
  // ① 忽略 AI 结束后迟到的 running 事件（done 丢失又把过程区点亮）
  // ② AI 执行结束（两标志均 false）后强制收起过程区，不再强依赖 WS done 事件送达。
  const aiActive = sending || optimisticAi;
  const aiActiveRef = useRef<boolean>(aiActive);
  aiActiveRef.current = aiActive;

  const handleWsAiProgress = useCallback((ev: { run_id?: string; phase: 'running' | 'done'; todos: AiProgressTodo[] }) => {
    // AI 执行已结束（sending/optimisticAi 均 false）后到的事件一律忽略：
    // 本轮过程区已由收尾逻辑强制收起，迟到的 running（WS 重连/竞态）不应再点亮它。
    if (!aiActiveRef.current) return;
    if (ev.phase === 'running') {
      setAiRunId((prev) => (prev === undefined ? ev.run_id : prev));
    }
    setAiPhase(ev.phase);
    setAiTodos(ev.todos || []);
  }, []);

  // ── WS 实时订阅：合并基线评论与增量事件，含在线/输入中/已读 + U老师 进度 ──
  const {
    displayComments,
    online,
    typingUser,
    readMap,
    sendTyping,
    sendRead,
    readRecords,
    deletedIds,
  } = useTaskCommentsWS(taskId, comments, { currentUser: username, onTaskUpdated, onAiProgress: handleWsAiProgress });

  // 过程区可见性：U老师 正在分析（sending）且至少跑过 running 或有进行中项；done 由 sending(false) 隐藏。
  // optimisticAi：点击 [帮我分析] 时立即置为 true → 前端主动显示占位 todo，不等 WS 首条 running
  //（避免 diagnose 这类“点击即执行、报告几秒返回”的任务，过程区晚出现/闪一下就消失）。
  const showAiProcess =
    (aiPhase === 'running' && aiTodos.length > 0) ||
    (sending && aiRunId !== undefined) ||
    optimisticAi;

  // 乐观占位 todo：仅当 optimisticAi 且尚无真实 todo 时启用（planning+进行中）
  // 文案用通用“分析/规划”，[帮我分析] 与 @U老师 讨论共用。
  const optimisticTodo: AiProgressTodo[] = optimisticAi && aiTodos.length === 0
    ? [{ id: 0, description: 'U老师 正在分析并规划排查步骤', status: 'in_progress', capability: 'planning', phase: 'running' as const }]
    : [];
  // 渲染用 todo：真实优先；否则用乐观占位（保证过程区“立刻出现”占位项）
  const displayTodos = aiTodos.length > 0 ? aiTodos : optimisticTodo;

  // 逐项状态：任一 todo 仍是进行中（phase=running / status=in_progress），就视为整场仍在执行。
  // 头部「正在排查 / 已完成」据此判断而非只看事件封套 phase，杜绝「已完成却还有项在转圈」的矛盾。
  const anyTodoRunning = displayTodos.some((t) => t.phase === 'running' || t.status === 'in_progress');
  const allTodosDone = displayTodos.length > 0 && !anyTodoRunning;

  // 新一轮 U老师 讨论开始（sending false→true）：重置过程区
  const prevSendingRef = useRef<boolean>(sending);
  useEffect(() => {
    if (sending && !prevSendingRef.current) {
      setAiRunId(undefined);
      setAiTodos([]);
      setAiPhase('done');
    }
    prevSendingRef.current = sending;
    // AI 执行结束（sending/optimisticAi 均 false）→ 短暂保留过程区让用户看到结果，
    // 随后**强制收起**。这里不再强依赖 allTodosDone（等价于收到 WS done 事件）——
    // done 广播可能因 WS 断线/竞态丢失，若仅靠它收起，aiPhase 会卡在 'running'
    // 导致过程区「一直挂着」。只要 aiActive=false 即说明 POST 已返回、AI 已跑完，
    // 故直接复位 aiPhase + 清空 todos。
    if (!aiActive && aiRunId !== undefined) {
      const t = setTimeout(() => {
        setAiRunId(undefined);
        setAiTodos([]);
        setAiPhase('done');
      }, 400);
      return () => clearTimeout(t);
    }
  }, [sending, allTodosDone, aiTodos, aiRunId, aiActive]);

  // username → 展示名 映射（用于在线头像 / 输入中提示）
  const nameMap = useMemo(() => {
    const m: Record<string, string> = {};
    for (const c of displayComments) {
      if (c.created_by) m[c.created_by] = c.created_by_name || c.created_by;
    }
    // 补充在线成员的名字
    for (const o of online) {
      if (o.username && !m[o.username]) m[o.username] = o.name || o.username;
    }
    return m;
  }, [displayComments, online]);
  const initialOf = (u?: string) => (u ? (nameMap[u] || u).slice(0, 1).toUpperCase() : '?');
  const typingName = typingUser ? (nameMap[typingUser] || typingUser) : '';

  // username → 头像资源 id 映射：在线成员携带 avatar_resource_id；自己用 authStore 的 avatarResourceId
  const avatarMap = useMemo(() => {
    const m: Record<string, number | null> = {};
    for (const o of online) {
      if (o.username) m[o.username] = o.avatar_resource_id ?? null;
    }
    if (username) m[username] = avatarResourceId;
    return m;
  }, [online, username, avatarResourceId]);
  const avatarSrcOf = (u?: string): string => {
    if (!u) return '';
    const rid = avatarMap[u];
    return rid ? avatarUrl(rid) : '';
  };

  const [commentText, setCommentText] = useState('');
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [viewer, setViewer] = useState<AttachmentViewItem | null>(null);
  // 待发送图片的预览 objectURL（与 pendingFiles 一一对应，非图片为空串），
  // 让用户一眼区分多张同名图片（如剪贴板默认 image.png）；依赖变化时自动 revoke 旧 URL。
  const previewUrls = useMemo(
    () => pendingFiles.map((f) => (f.type.startsWith('image/') ? URL.createObjectURL(f) : '')),
    [pendingFiles],
  );
  useEffect(() => {
    const urls = previewUrls.filter(Boolean);
    if (urls.length) return () => urls.forEach((u) => URL.revokeObjectURL(u));
  }, [previewUrls]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const chatMessagesRef = useRef<HTMLDivElement>(null);
  // 消息内容容器：用 ResizeObserver 监听其高度变化（图片加载/Markdown 渲染/消息追加撑高），
  // 用户处于贴底态时自动跟随滚到底，避免进入后停在顶部或最新消息被截断在边框。
  const chatContentRef = useRef<HTMLDivElement>(null);

  // @mention state
  const [showMentions, setShowMentions] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);

  // @# 工单引用 state（Q2d-①：@# 弹相似工单列表选；@#44123 直接写编号不弹）
  const [showTicketRef, setShowTicketRef] = useState(false);
  const [ticketRefList, setTicketRefList] = useState<Array<{ task_id: number; title: string; status?: string; project_name?: string }>>([]);
  const [ticketRefIndex, setTicketRefIndex] = useState(0);
  const [ticketRefLoading, setTicketRefLoading] = useState(false);

  // 引用（消息引用）state：当前正在引用的评论
  const [quoted, setQuoted] = useState<DiscussionComment | null>(null);
  // 长按操作菜单：{ 评论, 气泡定位矩形 }
  const [menu, setMenu] = useState<{ comment: DiscussionComment; rect: DOMRect } | null>(null);
  // 已读名单弹层：正在查看哪条评论的已读名单（飞书式）
  const [readListCommentId, setReadListCommentId] = useState<string | number | null>(null);
  const [readListAnchor, setReadListAnchor] = useState<DOMRect | null>(null);
  const longPressTimer = useRef<number | null>(null);
  // 长按后抑制紧随的 click（避免误触容器诊断链接等）
  const suppressClickRef = useRef(false);

  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastReadRef = useRef<number | null>(null);

  // ── 滚动管理（微信式）：仅在贴底时自动滚动；非贴底时累计新消息数并提示 ──
  const isAtBottomRef = useRef(true);
  const [newCount, setNewCount] = useState(0);
  const checkAtBottom = useCallback(() => {
    const el = chatMessagesRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }, []);
  const scrollToBottom = useCallback(() => {
    const el = chatMessagesRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setNewCount(0);
  }, []);
  const handleScroll = useCallback(() => {
    isAtBottomRef.current = checkAtBottom();
    if (isAtBottomRef.current) setNewCount(0);
  }, [checkAtBottom]);

  // 新消息到达：贴底则跟随滚动 + 上报已读；非贴底则累计提示数（不强制打断阅读历史）
  const lastMsgIdRef = useRef<string | number | null>(null);
  // 已上报过名单的评论 id 集合（避免重复逐条上报）
  const reportedReadIdsRef = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (!displayComments.length) return;
    const last = displayComments[displayComments.length - 1];
    const lid = last.id;
    if (lid !== lastMsgIdRef.current) {
      const isPrevInit = lastMsgIdRef.current === null;
      lastMsgIdRef.current = lid;
      if (isPrevInit || isAtBottomRef.current) {
        scrollToBottom();
        // 贴底态：把当前所有评论视为已读，逐条记入名单（飞书式）；游标取最后一条 id
        const allIds = displayComments
          .map((c) => Number(c.id))
          .filter((n) => Number.isFinite(n) && n > 0);
        const newIds = allIds.filter((n) => !reportedReadIdsRef.current.has(n));
        if (newIds.length) {
          newIds.forEach((n) => reportedReadIdsRef.current.add(n));
          const numId = allIds[allIds.length - 1];
          if (numId && numId !== lastReadRef.current) {
            lastReadRef.current = numId;
          }
          sendRead(numId, newIds);
        }
      } else {
        setNewCount((n) => n + 1);
      }
    }
  }, [displayComments, sendRead, scrollToBottom]);

  // 内容高度变化跟随（图片加载/Markdown 渲染/消息追加撑高）：贴底态自动滚到底，
  // 解决进入后停在顶部、最新消息被截断在边框等问题。仅监听内容容器尺寸，不干扰用户主动滚动。
  useEffect(() => {
    const content = chatContentRef.current;
    const el = chatMessagesRef.current;
    if (!content || !el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => {
      if (isAtBottomRef.current) el.scrollTop = el.scrollHeight;
    });
    ro.observe(content);
    return () => ro.disconnect();
  }, []);

  // 卸载时清理残留定时器，避免组件销毁后定时器仍触发回调
  useEffect(() => {
    return () => {
      if (longPressTimer.current) clearTimeout(longPressTimer.current);
      if (typingTimer.current) clearTimeout(typingTimer.current);
    };
  }, []);

  // commentText 变化时自适应高度（覆盖 @U老师 按钮 / mention 选择等程序化修改）
  useEffect(() => {
    const el = inputRef.current;
    if (el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 160) + 'px';
    }
  }, [commentText]);

  const handleSelectFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length) {
      // 与已有待发送文件合并后去重重命名，预览区与上传均用唯一文件名
      setPendingFiles((prev) => dedupeFileNames([...prev, ...files]));
    }
    e.target.value = '';
  };
  const removeFile = (idx: number) => setPendingFiles((prev) => prev.filter((_, i) => i !== idx));

  // @U老师：在输入框前缀 @U老师（父级 onSend 依此前缀路由到 U老师 讨论）
  const handleAIClick = () => {
    if (!commentText.startsWith('@U老师 ')) setCommentText('@U老师 ' + commentText);
  };

  // ── @mention: 过滤项目成员 ──
  const filteredMentionUsers = useMemo(() => {
    if (!mentionUsers || mentionUsers.length === 0) return [];
    if (!mentionFilter) return mentionUsers;
    const kw = mentionFilter.toLowerCase();
    return mentionUsers.filter(
      (u) =>
        (u.username || '').toLowerCase().includes(kw) ||
        (u.name || '').toLowerCase().includes(kw),
    );
  }, [mentionUsers, mentionFilter]);

  // 重置 mentionIndex 当过滤结果变化时
  useEffect(() => {
    setMentionIndex(0);
  }, [filteredMentionUsers]);

  // ── @mention: 检测 @ 触发 + 自动增高 ──
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const el = e.target;
    const val = el.value;
    setCommentText(val);

    // 自动增高：先归零再用 scrollHeight 撑开
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';

    const cursorPos = el.selectionStart ?? val.length;
    const textBeforeCursor = val.slice(0, cursorPos);

    // ── @# 工单引用触发（独立于人员 @，两种讨论区都可用）：光标前是 "@#..." ──
    // 只有"刚刚输入 @#（找相似）"或"@#数字（明确引用）"两种；后者不弹列表（@#44123 直接引用）。
    const atHashMatch = textBeforeCursor.match(/@#(\d*)$/);
    if (atHashMatch) {
      setShowMentions(false);
      if (atHashMatch[1]) {
        // 已带编号 → 明确引用，不弹列表
        setShowTicketRef(false);
      } else {
        // 只有 "@#" → 拉相似工单列表让用户选
        setShowTicketRef(true);
        void fetchSimilarTickets();
        // 初始化导航索引
        setTicketRefIndex(0);
      }
      return;
    }

    // ── 无任何触发符（既非 @# 也非 @）→ 收起两个面板 ──
    if (!/@#\d*$/.test(textBeforeCursor) && !/@[\w一-鿿]*$/.test(textBeforeCursor)) {
      setShowTicketRef(false);
    }

    // ── @mention: 无人员列表则跳过（@# 已在上方处理）──
    if (!mentionUsers || mentionUsers.length === 0) {
      setShowMentions(false);
      return;
    }

    const atMatch = textBeforeCursor.match(/@([\w一-鿿]*)$/);
    if (atMatch) {
      setMentionFilter(atMatch[1]);
      setShowMentions(true);
    } else {
      setShowMentions(false);
    }

    // ── 输入中实时提示：有内容时通知房间，停输 3s 自动结束 ──
    if (val.trim()) {
      sendTyping(true);
      if (typingTimer.current) clearTimeout(typingTimer.current);
      typingTimer.current = setTimeout(() => sendTyping(false), 3000);
    } else {
      sendTyping(false);
    }
  };

  // ── @mention: 选中用户 → 替换 @filter 为 @名字  ──
  const handleMentionSelect = (user: ProjectMember) => {
    const cursorPos = inputRef.current?.selectionStart ?? commentText.length;
    const textBeforeCursor = commentText.slice(0, cursorPos);
    const textAfterCursor = commentText.slice(cursorPos);

    const displayName = user.name || user.username;
    const newBefore = textBeforeCursor.replace(/@([\w一-鿿]*)$/, `@${displayName} `);
    const newText = newBefore + textAfterCursor;

    setCommentText(newText);
    setShowMentions(false);

    setTimeout(() => {
      inputRef.current?.focus();
      const pos = newBefore.length;
      inputRef.current?.setSelectionRange(pos, pos);
    }, 0);
  };

  // ── @# 工单引用: 拉取"相似已解决工单"列表（后端 /api/tasks/{taskId}/similar）──
  const fetchSimilarTickets = useCallback(async () => {
    if (taskId === undefined) return;
    setTicketRefLoading(true);
    try {
      const { createRequest } = await import('@/api/client');
      const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
      const res = await request<{ task_id: number; similar: Array<{ task_id: number; title: string; status?: string; project_name?: string }> }>(
        `/${taskId}/similar`
      );
      const list = (res?.similar || []).slice(0, 8);
      setTicketRefList(list);
    } catch {
      setTicketRefList([]);
    } finally {
      setTicketRefLoading(false);
    }
  }, [taskId]);

  // ── @# 工单引用: 选中 → 替换 "@#" 为 "@#编号 " ──
  const handleTicketRefSelect = (t: { task_id: number; title: string }) => {
    const cursorPos = inputRef.current?.selectionStart ?? commentText.length;
    const textBeforeCursor = commentText.slice(0, cursorPos);
    const textAfterCursor = commentText.slice(cursorPos);

    const newBefore = textBeforeCursor.replace(/@#\d*$/, `@#${t.task_id} `);
    const newText = newBefore + textAfterCursor;

    setCommentText(newText);
    setShowTicketRef(false);

    setTimeout(() => {
      inputRef.current?.focus();
      const pos = newBefore.length;
      inputRef.current?.setSelectionRange(pos, pos);
    }, 0);
  };

  // ── 键盘事件：处理 @mention / @# 导航 / Enter 发送 / Shift+Enter 换行 ──
  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showTicketRef && ticketRefList.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setTicketRefIndex((prev) => (prev + 1) % ticketRefList.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setTicketRefIndex((prev) => (prev - 1 + ticketRefList.length) % ticketRefList.length);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleTicketRefSelect(ticketRefList[ticketRefIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowTicketRef(false);
        return;
      }
    }
    if (showMentions && filteredMentionUsers.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionIndex((prev) => (prev + 1) % filteredMentionUsers.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionIndex((prev) => (prev - 1 + filteredMentionUsers.length) % filteredMentionUsers.length);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleMentionSelect(filteredMentionUsers[mentionIndex]);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setShowMentions(false);
        return;
      }
    }
    // Enter 发送（Shift+Enter 换行不做处理，让 textarea 原生行为插入换行）
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // ── 粘贴文件/图片：从剪贴板提取文件，加入待发送列表 ──
  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const pastedFiles: File[] = [];
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file) pastedFiles.push(file);
      }
    }
    // 优先从 files 属性获取（文件管理器复制场景）
    if (pastedFiles.length === 0 && e.clipboardData.files.length > 0) {
      for (let i = 0; i < e.clipboardData.files.length; i++) {
        pastedFiles.push(e.clipboardData.files[i]);
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault();
      // 与已有待发送文件合并后去重重命名，避免多张同名图片（如剪贴板默认 image.png）在预览/上传时混淆
      setPendingFiles((prev) => dedupeFileNames([...prev, ...pastedFiles]));
    }
  };

  const canSend = !sending && !disabled && (commentText.trim().length > 0 || pendingFiles.length > 0);

  const handleSend = async () => {
    if (!canSend) return;
    const text = commentText.trim();
    const files = pendingFiles;
    const replyTo = quoted ? quoted.id : undefined;
    let ok = false;
    try {
      ok = await onSend(text, files, replyTo !== undefined ? { replyTo } : undefined);
    } catch (err) {
      Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
    if (ok) {
      setCommentText('');
      setPendingFiles([]);
      setQuoted(null);
    }
    // 发送完成（无论成功/失败）焦点回到输入框，避免点「发送」按钮夺焦后需手动点回，支持连续输入；
    // textarea 始终挂载，下一帧渲染（sending 解除 disabled）后 focus 生效。
    setTimeout(() => { inputRef.current?.focus(); }, 0);
  };

  // ── 长按操作菜单（微信式）：长按 400ms 或右键唤起 ──
  const cancelLongPress = useCallback(() => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  }, []);

  const openMenu = useCallback((comment: DiscussionComment, rect: DOMRect) => {
    setMenu({ comment, rect });
    // 清除系统已选文本（长按可能触发原生选取），避免与自定义菜单叠加显示
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) sel.removeAllRanges();
    // 标记“刚长按”，抑制随后触摸屏 click 冒泡到容器（误触诊断链接等）
    suppressClickRef.current = true;
    setTimeout(() => { suppressClickRef.current = false; }, 350);
  }, []);

  // 菜单打开期间监听滚动：滚动即自动关闭（仿微信，避免 fixed 定位锚点与气泡实际位置脱节造成定位漂移）。
  // scroll 事件不冒泡，故用捕获阶段监听 window，可覆盖页面滚动与内部滚动容器滚动；
  // wheel 兜底 PC 端在 overflow 容器外的滚轮（此时未必触发 scroll）。
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener('scroll', close, true);
    window.addEventListener('wheel', close, true);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('wheel', close, true);
    };
  }, [menu]);

  const startLongPress = (comment: DiscussionComment, e: React.TouchEvent | React.MouseEvent) => {
    if (disabled || sending) return;
    const target = e.currentTarget as HTMLElement;
    // 落点在文字内容区（Markdown 正文）→ 放行原生文本选择（长按选字复制），不弹自定义菜单。
    // 落点在头像/名字/引用块/附件/留白等非文字区 → 弹自定义「引用/复制/删除」菜单（方案A 双端）。
    const hit = e.target as HTMLElement | null;
    if (hit && hit.closest('.markdown-body')) return;
    cancelLongPress();
    longPressTimer.current = window.setTimeout(() => {
      longPressTimer.current = null;
      openMenu(comment, target.getBoundingClientRect());
    }, 400);
  };

  // 点击引用块 / 引用条 → 平滑滚动并高亮定位原消息
  const locateComment = useCallback((id: string | number) => {
    const el = document.getElementById(`comment-${id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('is-flash');
    setTimeout(() => el.classList.remove('is-flash'), 1200);
  }, []);

  // 菜单动作
  const handleQuote = () => {
    if (menu) {
      setQuoted(menu.comment);
      setMenu(null);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  };
  const handleCopy = () => {
    if (!menu) return;
    const text = stripHtml(menu.comment.content);
    const done = () => Toast({ message: '已复制', theme: 'success' });
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
    setMenu(null);
  };
  const fallbackCopy = (text: string) => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand('copy');
      Toast({ message: '已复制', theme: 'success' });
    } catch {
      Toast({ message: '复制失败', theme: 'error' });
    } finally {
      document.body.removeChild(ta);
    }
  };
  const handleDelete = async () => {
    if (!menu || !onDeleteComment) return;
    const id = menu.comment.id;
    setMenu(null);
    try {
      await onDeleteComment(id);
    } catch (err) {
      Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  };

  const ph = placeholder ?? (enableAI ? '直接评论、@U老师 讨论，或输入 @#工单号 引用历史工单。' : '参与讨论…');

  // 长按菜单浮层交给 TDesign <Popover>（popper 定位 + 箭头 + 动画 + 外点关闭）承载。
  // 用一个「透明、pointer-events:none 的代理锚点」定位到被长按气泡的 rect：
  // 既让 popper 以气泡为基准绘制箭头与上下位置，又不拦截气泡本身的交互/滚动。
  const menuAnchorStyle: React.CSSProperties = menu
    ? {
        position: 'fixed',
        left: menu.rect.left,
        top: menu.rect.top,
        width: menu.rect.width,
        height: menu.rect.height,
        pointerEvents: 'none',
        background: 'transparent',
      }
    : { display: 'none' };
  // 垂直方向依气泡距讨论区容器顶部距离动态选择上方/下方，避免贴近顶部时被遮挡
  const menuPlacement: 'top' | 'bottom' = menu
    ? (() => {
        const containerTop = chatMessagesRef.current?.getBoundingClientRect().top ?? 0;
        const spaceAbove = menu.rect.top - containerTop;
        return spaceAbove >= 64 ? 'top' : 'bottom';
      })()
    : 'top';

  return (
    <div className={`detail-card detail-chat-container ${className}`.trim()}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4 className="detail-card__h">{title ?? `讨论（${displayComments.length}）`}</h4>
        {headerRight}
      </div>
      {/* 在线成员（实时，按用户去重） */}
      {taskId !== undefined && online.length > 0 && (
        <div className="detail-chat-presence">
          {online.map((o) => {
            const av = o.avatar_resource_id ? avatarUrl(o.avatar_resource_id) : '';
            return av ? (
              <img
                key={o.username}
                className="detail-chat-presence__avatar detail-chat-presence__avatar--img"
                src={av}
                alt={o.name || o.username}
                title={o.name || o.username}
              />
            ) : (
              <span key={o.username} className="detail-chat-presence__avatar" title={o.name || o.username}>
                {initialOf(o.username)}
              </span>
            );
          })}
          <span className="detail-chat-presence__text">{online.length} 人在线</span>
        </div>
      )}
      <div
        className="detail-chat-messages"
        ref={chatMessagesRef}
        onScroll={handleScroll}
        onClick={(e) => {
          // 长按释放后的 click 抑制，避免误触容器诊断链接
          if (suppressClickRef.current) {
            suppressClickRef.current = false;
            return;
          }
          onMessagesClick?.(e);
        }}
      >
        <div ref={chatContentRef} className="detail-chat-messages__inner">
        {displayComments.length > 0 ? (
          displayComments.map((c, idx) => {
            const authorName = c.created_by_name || c.created_by || '未知用户';
            const isCurrentUser =
              (c.created_by?.toLowerCase() === username?.toLowerCase()) ||
              (c.created_by_name?.toLowerCase() === username?.toLowerCase()) ||
              (c.created_by_name?.toLowerCase() === name?.toLowerCase());
            // 聊天历史记录模式：首条消息或与上一条间隔≥5分钟，插入居中时间分隔
            const prevCreatedAt = idx > 0 ? displayComments[idx - 1].created_at : undefined;
            const showDivider = shouldShowTimeDivider(c.created_at, prevCreatedAt);
            // 连续消息合并：与上一条同一作者且无需时间分隔时，省略头像/姓名（微信式）
            const prev = idx > 0 ? displayComments[idx - 1] : null;
            const isContinued = !!prev
              && !showDivider
              && (prev.created_by?.toLowerCase() === c.created_by?.toLowerCase());
            const avSrc = avatarSrcOf(c.created_by);
            const avatarEl = avSrc ? (
              <img className="detail-chat-avatar detail-chat-avatar--img" src={avSrc} alt={authorName} />
            ) : (
              <span className="detail-chat-avatar">{initialOf(c.created_by)}</span>
            );
            return (
              <Fragment key={c.id}>
                {showDivider && (
                  <div className="detail-chat-time-divider">
                    {formatChatDividerTime(c.created_at)}
                  </div>
                )}
                <div className={`detail-chat-row ${isCurrentUser ? 'is-right' : ''} ${isContinued ? 'is-continued' : ''}`}>
                  {/* 头像列：连续消息省略（占位保持对齐） */}
                  {!isCurrentUser && (isContinued ? <span className="detail-chat-avatar-ph" /> : avatarEl)}
                  <div
                    id={`comment-${c.id}`}
                    className={`detail-chat-bubble ${isCurrentUser ? 'is-self' : ''}`}
                    onTouchStart={(e) => startLongPress(c, e)}
                    onTouchEnd={cancelLongPress}
                    onTouchMove={cancelLongPress}
                    onMouseDown={(e) => { if (e.button === 0) startLongPress(c, e); }}
                    onMouseUp={cancelLongPress}
                    onMouseLeave={cancelLongPress}
                    onContextMenu={(e) => {
                      e.preventDefault();
                      cancelLongPress();
                      openMenu(c, e.currentTarget.getBoundingClientRect());
                    }}
                  >
                    {/* 引用块（微信式：气泡内顶部高亮，点击定位原消息；被引用消息已删除则显示占位） */}
                    {c.quoted && (() => {
                      const qDeleted = deletedIds.has(String(c.quoted.id));
                      return (
                        <div
                          className={`detail-chat-quote${qDeleted ? ' is-deleted' : ''}`}
                          onClick={qDeleted ? undefined : (e) => { e.stopPropagation(); locateComment(c.quoted!.id); }}
                        >
                          <span className="detail-chat-quote__name">{c.quoted.created_by_name || '用户'}</span>
                          <span className="detail-chat-quote__text">{qDeleted ? '该消息已被删除' : stripHtml(c.quoted.content)}</span>
                        </div>
                      );
                    })()}
                    {!isCurrentUser && !isContinued && (
                      <div className="detail-chat-name">
                        <span className="detail-chat-name__text">{authorName}</span>
                        <span className="detail-chat-name__time">{formatCommentTime(c.created_at)}</span>
                      </div>
                    )}
                    <MarkdownRenderer content={c.content} compact />
                    {c.attachments && c.attachments.length > 0 && (
                      <div className="detail-chat-attachments">
                        {c.attachments.map((a, i) => {
                          const att = parseAttachment(a);
                          if (!att.objectPath) return null;
                          const url = `${API_CONFIG.TASKS.BASE_URL}/files/${att.objectPath}`;
                          const openViewer = () =>
                            setViewer({
                              filename: att.filename || 'file',
                              size: typeof a === 'object' ? a.size : undefined,
                              previewUrl: url,
                              downloadUrl: url,
                            });
                          if (att.isImage) {
                            return (
                              <img
                                key={i}
                                src={url}
                                alt={att.filename}
                                className="detail-chat-attachment-img"
                                loading="lazy"
                                decoding="async"
                                onClick={openViewer}
                                onError={(e) => {
                                  // 微信 WebView 偶发 img 静默渲染失败（HTTP 200 但白屏，多见于大图/缓存损坏）。
                                  // 加时间戳破缓存重试一次；仍失败则换成文件名占位，避免纯白方框无反馈。
                                  const el = e.currentTarget;
                                  if (!el.dataset.retried) {
                                    el.dataset.retried = '1';
                                    const sep = url.includes('?') ? '&' : '?';
                                    el.src = `${url}${sep}_r=${Date.now()}`;
                                  } else {
                                    el.style.display = 'none';
                                    const ph = document.createElement('div');
                                    ph.className = 'detail-chat-attachment-file';
                                    ph.textContent = `📎 ${att.filename}`;
                                    ph.onclick = openViewer;
                                    el.parentNode?.appendChild(ph);
                                  }
                                }}
                              />
                            );
                          }
                          return (
                            <div key={i} className="detail-chat-attachment-file" onClick={openViewer}>
                              📎 {att.filename}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {isCurrentUser && (() => {
                      // 名单（精确）：该评论的已读成员列表（后端按 read_at 倒序）
                      const cid = c.id;
                      const readers = (readRecords[String(cid)] || []).filter(
                        (r) => r.username !== c.created_by,
                      );
                      // 人数兜底：名单为空时用游标 readMap 反推（兼容未上报名单的旧数据）
                      const readCount = readers.length > 0
                        ? readers.length
                        : Object.entries(readMap).filter(
                            ([u, rid]) => u !== c.created_by && Number(rid) >= Number(c.id),
                          ).length;
                      if (readCount <= 0) return null;
                      const isOpen = readListCommentId === cid;
                      return (
                        <button
                          type="button"
                          className={`detail-chat-read${isOpen ? ' is-open' : ''}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            if (isOpen) {
                              setReadListCommentId(null);
                              setReadListAnchor(null);
                            } else {
                              setReadListCommentId(cid);
                              setReadListAnchor(e.currentTarget.getBoundingClientRect());
                            }
                          }}
                          title="查看已读名单"
                        >
                          已读 {readCount} 人
                        </button>
                      );
                    })()}
                  </div>
                  {/* 自己消息头像列（右侧）：连续消息省略（占位保持对齐） */}
                  {isCurrentUser && (isContinued ? <span className="detail-chat-avatar-ph" /> : avatarEl)}
                </div>
              </Fragment>
            );
          })
        ) : (
          <div className="detail-chat-empty">暂无评论</div>
        )}
        </div>
      </div>

      {/* 新消息提示条（微信式）：滚在历史区时收到新消息，显示悬浮条，点击跳底 */}
      {newCount > 0 && (
        <div className="detail-chat-newmsg" onClick={scrollToBottom}>
          <span>{newCount} 条新消息</span>
          <span className="detail-chat-newmsg__arrow">↓</span>
        </div>
      )}

      {/* 长按操作菜单：TDesign Popover（自带箭头/动画/外点关闭），代理锚点定位到被长按气泡 */}
      <Popover
        visible={!!menu}
        placement={menuPlacement}
        showArrow
        theme="light"
        closeOnClickOutside
        onVisibleChange={(v) => { if (!v) setMenu(null); }}
        style={menuAnchorStyle}
        content={
          menu ? (
            <div className="detail-chat-menu">
              <button type="button" className="detail-chat-menu__item" onClick={handleQuote}>引用</button>
              <button type="button" className="detail-chat-menu__item" onClick={handleCopy}>复制</button>
              {onDeleteComment && (
                <button type="button" className="detail-chat-menu__item is-danger" onClick={handleDelete}>删除</button>
              )}
            </div>
          ) : null
        }
      />

      {/* 已读名单弹层（飞书式）：头像 + 姓名 + 阅读时间，按阅读时间倒序 */}
      {readListCommentId !== null && readListAnchor && (() => {
        const cid = readListCommentId;
        const readers = (readRecords[String(cid)] || []).filter(
          (r) => r.username !== (displayComments.find((x) => x.id === cid)?.created_by),
        );
        const anchorStyle: React.CSSProperties = {
          position: 'fixed',
          left: readListAnchor.left,
          top: readListAnchor.top,
          width: readListAnchor.width,
          height: readListAnchor.height,
          pointerEvents: 'none',
          background: 'transparent',
        };
        const spaceAbove = readListAnchor.top - (chatMessagesRef.current?.getBoundingClientRect().top ?? 0);
        const placement: 'top' | 'bottom' = spaceAbove >= 160 ? 'top' : 'bottom';
        return (
          <Popover
            visible
            placement={placement}
            showArrow
            theme="light"
            closeOnClickOutside
            onVisibleChange={(v) => { if (!v) { setReadListCommentId(null); setReadListAnchor(null); } }}
            style={anchorStyle}
            content={
              <div className="detail-chat-readlist">
                <div className="detail-chat-readlist__title">已读 {readers.length} 人</div>
                {readers.length > 0 ? (
                  <div className="detail-chat-readlist__body">
                    {readers.map((r) => {
                      const av = r.avatar_resource_id ? avatarUrl(r.avatar_resource_id) : '';
                      return (
                        <div key={r.username} className="detail-chat-readlist__item">
                          {av ? (
                            <img className="detail-chat-readlist__avatar" src={av} alt={r.name || r.username} />
                          ) : (
                            <span className="detail-chat-readlist__avatar">
                              {(r.name || r.username || '?').slice(0, 1).toUpperCase()}
                            </span>
                          )}
                          <span className="detail-chat-readlist__name">{r.name || r.username}</span>
                          <span className="detail-chat-readlist__time">{formatReadTime(r.read_at)}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="detail-chat-readlist__empty">暂无已读记录</div>
                )}
              </div>
            }
          />
        );
      })()}

      {typingUser && (
        <div className="detail-chat-typing">{typingName} 正在输入…</div>
      )}
      <div className="detail-chat-input" style={{ position: 'relative' }}>
        {/* 引用条：引用某条消息后显示在输入框上方，可点击定位/取消 */}
        {quoted && (
          <div className="detail-chat-quote-bar">
            <div className="detail-chat-quote-bar__body" onClick={() => locateComment(quoted.id)}>
              <span className="detail-chat-quote-bar__name">引用 {quoted.created_by_name || '用户'}</span>
              <span className="detail-chat-quote-bar__text">{stripHtml(quoted.content)}</span>
            </div>
            <button type="button" className="detail-chat-quote-bar__close" onClick={() => setQuoted(null)} aria-label="取消引用">✕</button>
          </div>
        )}
        {/* @mention suggestion panel */}
        {showMentions && filteredMentionUsers.length > 0 && (
          <div className="detail-chat-mention-panel">
            {filteredMentionUsers.map((u, i) => (
              <div
                key={u.id}
                className={`detail-chat-mention-item ${i === mentionIndex ? 'is-active' : ''}`}
                onMouseDown={(e) => { e.preventDefault(); handleMentionSelect(u); }}
              >
                <span className="detail-chat-mention-name">{u.name || u.username}</span>
                <span className="detail-chat-mention-role">{u.role_name || ''}</span>
              </div>
            ))}
          </div>
        )}
        {/* @# 工单引用 suggestion panel（相似已解决工单） */}
        {showTicketRef && (
          <div className="detail-chat-mention-panel">
            {ticketRefLoading ? (
              <div className="detail-chat-mention-item">
                <span className="detail-chat-mention-name">正在加载相似工单…</span>
              </div>
            ) : ticketRefList.length === 0 ? (
              <div className="detail-chat-mention-item">
                <span className="detail-chat-mention-name">没有相似工单，可手动输入 @#工单号 引用</span>
              </div>
            ) : (
              ticketRefList.map((t, i) => (
                <div
                  key={t.task_id}
                  className={`detail-chat-mention-item ${i === ticketRefIndex ? 'is-active' : ''}`}
                  onMouseDown={(e) => { e.preventDefault(); handleTicketRefSelect(t); }}
                >
                  <span className="detail-chat-mention-name">#{t.task_id} {t.title}</span>
                  <span className="detail-chat-mention-role">{(t.status || '').replace('resolved', '已解决')}</span>
                </div>
              ))
            )}
          </div>
        )}
        {/* U老师 执行过程（Claude Code 式动态展示）：Supervisor 派发能力时逐项实时滚动，
            最终回复只写纯答复（不含此过程）；[帮我分析] 点按瞬间用乐观占位立即显示 */}
        {enableAI && showAiProcess && displayTodos.length > 0 && (
          <div className="detail-chat-ai-progress">
            <div className="detail-chat-ai-progress__head">
              <span className="detail-chat-ai-progress__spinner" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              {!allTodosDone ? 'U老师 正在排查执行' : '排查执行完成'}
            </div>
            <ul className="detail-chat-ai-progress__list">
              {displayTodos.map((t, i) => {
                const desc = t.description || t.capability || '分析';
                const status = t.phase === 'done' || t.status === 'completed';
                const running = t.phase === 'running' || t.status === 'in_progress';
                return (
                  <li key={`${t.id ?? i}-${i}`} className={`detail-chat-ai-progress__item ${running ? 'is-running' : ''} ${status ? 'is-done' : ''}`}>
                    <span className="detail-chat-ai-progress__icon" aria-hidden="true">
                      {status ? (
                        <svg viewBox="0 0 16 16" className="detail-chat-ai-progress__ic done-icon">
                          <circle cx="8" cy="8" r="7" />
                          <path d="M4.9 8.3l1.9 1.9 4.2-4.2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                      ) : running ? (
                        <svg viewBox="0 0 16 16" className="detail-chat-ai-progress__ic running-icon">
                          <path d="M8 1.5a6.5 6.5 0 1 0 6.5 6.5" fill="none" strokeLinecap="round" />
                        </svg>
                      ) : (
                        <svg viewBox="0 0 16 16" className="detail-chat-ai-progress__ic pending-icon">
                          <circle cx="8" cy="8" r="5.5" fill="none" />
                        </svg>
                      )}
                    </span>
                    <span className="detail-chat-ai-progress__text">{desc}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        {(enableAI || (enableAttach && pendingFiles.length > 0)) && (
          <div className="detail-chat-toolbar">
            {enableAI && (
              <Button size="small" theme="default" className="detail-chat-mention-btn" onClick={handleAIClick} disabled={sending || disabled}>
                @U老师
              </Button>
            )}
            {enableAttach && pendingFiles.length > 0 && (
              <div className="detail-chat-files">
                {pendingFiles.map((f, i) => (
                  <span key={i} className="detail-chat-file">
                    {previewUrls[i] ? (
                      <span className="detail-chat-file__thumb">
                        <img src={previewUrls[i]} alt={f.name} />
                      </span>
                    ) : (
                      <span className="detail-chat-file__icon">📄</span>
                    )}
                    <span className="detail-chat-file__name">{f.name}</span>
                    <button type="button" onClick={() => removeFile(i)} aria-label="移除">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
        <div className="detail-chat-input-row">
          <textarea
            ref={inputRef}
            className="detail-chat-input-field"
            value={commentText}
            onChange={handleInputChange}
            onKeyDown={handleInputKeyDown}
            onPaste={handlePaste}
            placeholder={disabled ? '工单号缺失，无法评论' : ph}
            disabled={sending || disabled}
            rows={1}
          />
          {enableAttach && (
            <button
              type="button"
              className="detail-chat-attach"
              onClick={() => fileInputRef.current?.click()}
              disabled={sending || disabled}
              aria-label="上传图片或文件"
            >
              <Paperclip size={16} strokeWidth={2} />
            </button>
          )}
          {/* 发送按钮（设计稿 04/05 工单详情输入区：size-10 bg-primary 圆形 + Send 纸飞机图标；ArrowUp 仅用于对话首页） */}
          <Button size="small" theme="primary" className="detail-chat-send" onClick={handleSend} disabled={!canSend} aria-label="发送">
            {sending ? <span className="detail-attachment-file__spinner" /> : <Send size={16} strokeWidth={2.2} />}
          </Button>
          {enableAttach && (
            <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }} onChange={handleSelectFile} />
          )}
        </div>
      </div>

      {/* 附件预览：图片灯箱 / PDF 内联 / Markdown 渲染 */}
      <AttachmentViewer item={viewer} onClose={() => setViewer(null)} />
    </div>
  );
}
