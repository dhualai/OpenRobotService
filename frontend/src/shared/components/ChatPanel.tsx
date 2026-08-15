// 可复用 AI 对话面板 — 提单 Agent（/api/ai/qa/ask/stream）
// 用于「我要摇人」页面：诊断+提单。系统任务页面不再使用 ChatPanel。
import { memo, useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { Textarea, Toast, Popup, Tag, Loading } from 'tdesign-mobile-react';
import { ArrowUp, Plus, MessageSquarePlus, TicketPlus, Paperclip, ThumbsUp, ThumbsDown, Copy, Pencil, Check, X, FolderClosed, User, CheckCircle2 } from 'lucide-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import { useAuthStore } from '@/stores/auth';
import { useWorkbenchStore } from '@/stores/workbench';
import API_CONFIG from '@/config/api';
import { qaUploadStream, generateSessionId, trackSession, fetchWithAuth, qaPrepareTicket, qaConfirmTicket, qaClearDraft, type TicketDraft } from '@/api/ai';
import ProjectSelect from '@/shared/components/ProjectSelect';
import UserSelect from '@/shared/components/UserSelect';
import { createTicket } from '@/api/ticket';
import { getDeadlineRange, makeDisabledDate, makeDisabledTime } from '@/shared/utils/deadline';
import type { UserItem } from '@/api/users';
import { createConversation, getConversation, appendMessage, readAiSessionId, updateMessageContent } from '@/api/conversation';
import { createRequest } from '@/api/client';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
import { compressImage } from '@/shared/utils/imageCompress';
import { useInertiaScroll } from '@/shared/hooks/useInertiaScroll';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import ImageLightbox from '@/shared/components/ImageLightbox';
import SuggestedQuestions from '@/shared/components/SuggestedQuestions';
import { pickRandomQuestions, matchQuestions } from '@/shared/data/suggestedQuestions';

interface SpeechRecognitionResultEvent {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechRecognitionResultEvent) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

const SR: SpeechRecognitionCtor | undefined =
  (window as unknown as { SpeechRecognition?: SpeechRecognitionCtor }).SpeechRecognition ??
  (window as unknown as { webkitSpeechRecognition?: SpeechRecognitionCtor }).webkitSpeechRecognition;

export type ChatScene = 'call' | 'tasks';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  imageUrl?: string;
  // 非图片附件（zip/日志/文档等）：url 为后端返回的预签名 URL（上传成功后回填/恢复时带），上传中无 url
  attachment?: { name: string; size: number; url?: string } | null;
  // 乐观上传进度：附件气泡内嵌进度遮罩；failed=上传失败红色遮罩
  uploading?: boolean;
  percent?: number;
  failed?: boolean;
  reaction?: 'like' | 'dislike' | null;
  // 流式输出进行中标记：true 时气泡用纯文本渲染（避免 Markdown 全量重解析造成抖动），完成后置 false
  streaming?: boolean;
  // 附件上传后的 AI 占位阶段（对应 sendWithFile 动态占位）：analyzing_image/analyzing_file=分析中，thinking=思考中。
  // generating_ticket=提单生成工单草稿中（LLM 的「好的」被抑制，弹窗前显示动画）。
  // 设置后气泡渲染 chat-bubble__typing 动态动画（同纯文字「思考中」），不写入 content（content 留空）
  phase?: 'analyzing_image' | 'analyzing_file' | 'thinking' | 'generating_ticket';
  // 任务 Agent 专属：结构化方案草稿 / 工单概览 / 信息不足提示（长文本可展开）
  subtype?: 'solution_draft' | 'ticket_overview' | 'missing_hint';
  solution_draft?: {
    _task_id?: string;
    root_cause_analysis: string;
    suggested_actions: string[];
    references: string[];
    confidence: number;
    needs_more_info: boolean;
  };
  // 工单确认后的概览气泡：confirm 成功时构造，DB 持久化（metadata_.kind='ticket_overview'）
  ticket_overview?: {
    db_id: number;
    ticket_id: string;
    title: string;
    type?: string;
    priority?: string;
    project?: string;
    contact?: string;
    description?: string;
    created_at?: string;
    assigned_to_name?: string; // 派单完成后轮询填充 + 回写 DB
  };
}

const uid = () => Date.now().toString() + Math.random().toString(36).slice(2, 6);

/** 附件代理下载 URL：前端通过后端代理读取 MinIO 对象（/api/call/files/{object_path}），
 *  不用预签名 URL（其 host=MINIO_ENDPOINT=localhost:9000，生产浏览器访问不了 → 碎图）。 */
const attachmentUrl = (objectPath: string) => `${API_CONFIG.CALL.BASE_URL}/files/${objectPath}`;

/**
 * AI 回复文本清洗（流式定稿 / 持久化 / 历史恢复统一入口）。
 * LLM 输出协议为「JSON 状态块 + 正文」，后端按边界切流；边界判定失败 / max_tokens 截断时
 * JSON 残片（{"action":...}、``` 围栏、游离 }）会泄漏进正文。此处统一剥除：
 * 剥不出正文 → 返回 ''（交由空内容兜底），杜绝残破 JSON / 带 } 的回复上屏。
 */
const sanitizeAiText = (raw: string): string => {
  let t = (raw ?? '').trim();
  if (!t) return '';
  // 1) 剥 fenced JSON 头：```json {...} ``` / ``` {...} ```
  t = t.replace(/^```(?:json)?\s*\{[\s\S]*?\}\s*```/, '').trim();
  // 2) 剥裸 JSON 头：{"action":...}（括号深度跟踪，容错嵌套与字符串内括号）
  if (t.startsWith('{') && t.slice(0, 400).includes('"action"')) {
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let i = 0; i < t.length; i++) {
      const ch = t[i];
      if (esc) { esc = false; continue; }
      if (ch === '\\' && inStr) { esc = true; continue; }
      if (ch === '"') { inStr = !inStr; continue; }
      if (inStr) continue;
      if (ch === '{') depth++;
      else if (ch === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    if (end >= 0) {
      t = t.slice(end + 1).trim();
    } else {
      // JSON 未闭合（LLM 截断泄漏）：整体视为异常，不显示残破 JSON
      return '';
    }
  }
  // 3) 剥游离残留前缀：} （LLM 多输出的闭合括号）
  const strippedBraces = t.replace(/^(?:\s*\}\s*)+/, '');
  // 剥孤立 fence 残留行：``` 单独出现（后无语言标记，非代码块）
  const result = strippedBraces.replace(/^```\s*\n?(?![a-zA-Z])/, '').trim();
  return result;
};

/** 流式中间态判定：疑似 LLM 协议 JSON 头泄漏，流式期间以占位代替上屏。
 *  判定与 sanitizeAiText 对齐，避免误伤正常回复：
 *  - 裸 JSON 头：{ 开头且前 400 字符含 "action" 字段（正文里 { 举例不含 action，不误判）
 *  - fenced JSON 头：```json / 无语言标记 ``` 紧跟 {（```python 等带语言标记的代码块属正常回复，不占位） */
const looksLikeJsonHead = (text: string): boolean => {
  const t = text.trimStart();
  if (t.startsWith('{') && t.slice(0, 400).includes('"action"')) return true;
  return /^```(?:json)?\s*\{/.test(t);
};

/** DB 会话消息 → 前端 Message：附件恢复 + AI 文本清洗 + 空白 AI 气泡过滤（历史异常数据不上屏） */
const mapDbMessages = (
  full: { messages?: Array<{ id: number; role: string; content: string; created_at: string; file_urls?: string | null; message_type?: string; metadata_?: string | null }> },
): Message[] => {
  // metadata_ 经后端 safe_json_dumps 二次编码：可能是 对象 或 嵌套字符串，统一解析两次得到对象
  const parseMeta = (s?: string | null): Record<string, unknown> | null => {
    if (!s) return null;
    try {
      let v: unknown = JSON.parse(s);
      if (typeof v === 'string') v = JSON.parse(v);
      return (v && typeof v === 'object') ? (v as Record<string, unknown>) : null;
    } catch { return null; }
  };
  const mapped = (full.messages || [])
    .flatMap((m) => {
      // 工单概览气泡：metadata_.kind==='ticket_overview'（content 存工单 JSON）
      const meta = parseMeta(m.metadata_);
      if (meta?.kind === 'ticket_overview') {
        try {
          const ov = JSON.parse(m.content) as NonNullable<Message['ticket_overview']>;
          return [{
            id: String(m.id),
            role: 'assistant' as const,
            content: '',
            timestamp: m.created_at,
            subtype: 'ticket_overview' as const,
            ticket_overview: ov,
          }];
        } catch { /* 解析失败降级为普通文本 */ }
      }
      const msg: Message = {
        id: String(m.id),
        role: m.role as 'user' | 'assistant',
        content: m.role === 'assistant' ? sanitizeAiText(m.content) : m.content,
        timestamp: m.created_at,
      };
      const extraMsgs: Message[] = [];
      if (m.file_urls) {
        try {
          const files = JSON.parse(m.file_urls) as Array<{ filename: string; object_path?: string; size?: number; isImage?: boolean }>;
          files.forEach((f, i) => {
            if (!f.object_path) return;
            const url = attachmentUrl(f.object_path);
            if (i === 0) {
              // 第一个文件填入主消息气泡
              if (f.isImage) msg.imageUrl = url;
              else msg.attachment = { name: f.filename, size: f.size ?? 0, url };
            } else {
              // 其余文件各创建独立气泡（与实时上传一致，每个文件单独显示）
              extraMsgs.push({
                id: `${String(m.id)}-${i}`,
                role: m.role as 'user' | 'assistant',
                content: '',
                timestamp: m.created_at,
                imageUrl: f.isImage ? url : undefined,
                attachment: f.isImage ? null : { name: f.filename, size: f.size ?? 0, url },
              });
            }
          });
        } catch { /* ignore */ }
      }
      return [msg, ...extraMsgs];
    });
  // producer 后台生成中：最后一条 assistant content 空（首 token 前），标记 streaming 保留显示。
  // 配合会话加载后的轮询，producer 完成后 content 非空，轮询自动更新为完整回复。
  for (let i = mapped.length - 1; i >= 0; i--) {
    if (mapped[i].role === 'assistant') {
      if (!mapped[i].content.trim() && !mapped[i].subtype) {
        mapped[i] = { ...mapped[i], streaming: true };
      }
      break;
    }
  }
  // 空白 AI 气泡（历史异常落库的空内容/纯空白）不恢复显示；但 streaming 占位（producer 生成中）保留
  return mapped.filter((m) => m.role !== 'assistant' || m.subtype === 'ticket_overview' || m.content.trim().length > 0 || m.streaming);
};

// 单条消息气泡（React.memo）：流式期间仅最后一条 content/streaming 变化，历史消息跳过整列表重渲染，消除抖动
const MessageBubble = memo(function MessageBubble({
  msg, editingId, compact, expandedDesc, onToggleDesc, onToggleReaction, onCopy, onEditStart, onEditChange, onEditSave,   onEditCancel, onImageClick, onOpenTicket,
}: {
  msg: Message;
  editingId: string | null;
  compact: boolean;
  expandedDesc: boolean;
  onToggleDesc: (id: string) => void;
  onToggleReaction: (id: string, type: 'like' | 'dislike') => void;
  onCopy: (content: string) => void;
  onEditStart: (id: string) => void;
  onEditChange: (id: string, value: string) => void;
  onEditSave: (msg: Message) => void;
  onEditCancel: () => void;
  onImageClick: (url: string) => void;
  onOpenTicket: (dbId: number) => void;
}) {
  return (
    <div className={`chat-bubble-wrap ${msg.role === 'user' ? 'is-right' : 'is-left'}`}>
      <div className={`chat-bubble ${msg.role === 'user' ? 'is-user' : 'is-ai'}`}>
        {msg.imageUrl && (
          <div className="chat-bubble__media">
            <img
              src={msg.imageUrl}
              alt="附件"
              className="chat-bubble__img"
              onClick={() => { if (!msg.uploading && !msg.failed && msg.imageUrl) onImageClick(msg.imageUrl); }}
            />
            {msg.uploading && (
              <div className="chat-bubble__media-overlay">
                <span className="chat-bubble__media-spinner" />
                <span className="chat-bubble__media-percent">{msg.percent ?? 0}%</span>
              </div>
            )}
            {msg.failed && (
              <div className="chat-bubble__media-overlay is-failed">
                <span className="chat-bubble__media-failtext">上传失败</span>
              </div>
            )}
          </div>
        )}
        {msg.attachment && (
          <div
            className={`chat-bubble__file${msg.failed ? ' is-failed' : ''}`}
            onClick={() => { if (!msg.uploading && !msg.failed && msg.attachment?.url) window.open(msg.attachment.url, '_blank', 'noopener,noreferrer'); }}
            role={msg.attachment?.url && !msg.uploading && !msg.failed ? 'button' : undefined}
            style={{ cursor: msg.attachment?.url && !msg.uploading && !msg.failed ? 'pointer' : 'default' }}
          >
            <Paperclip size={16} strokeWidth={1.8} className="chat-bubble__file-icon" />
            <span className="chat-bubble__file-name">{msg.attachment.name}</span>
            {msg.failed ? (
              <span className="chat-bubble__file-fail">上传失败</span>
            ) : msg.uploading ? (
              <span className="chat-bubble__file-percent">{msg.percent ?? 0}%</span>
            ) : (
              <span className="chat-bubble__file-size">
                {msg.attachment.size >= 1024 * 1024
                  ? `${(msg.attachment.size / 1024 / 1024).toFixed(1)} MB`
                  : `${Math.max(1, Math.round(msg.attachment.size / 1024))} KB`}
              </span>
            )}
            {msg.uploading && (
              <div className="chat-bubble__file-track">
                <div className="chat-bubble__file-fill" style={{ width: `${msg.percent ?? 0}%` }} />
              </div>
            )}
          </div>
        )}
        {editingId === msg.id ? (
          <Textarea
            value={msg.content}
            autosize={{ minRows: 1, maxRows: 6 }}
            onChange={(v) => onEditChange(msg.id, String(v))}
          />
        ) : msg.role === 'assistant' ? (
          msg.phase ? (
            // 附件上传后的动态占位（同纯文字「思考中」打字动画）：phase 决定文案
            (() => {
              const text = msg.phase === 'thinking' ? '思考中'
                : msg.phase === 'generating_ticket' ? '正在生成工单，请稍等'
                : (msg.phase === 'analyzing_image' ? '正在分析图片' : '正在分析文件');
              return (
                <div className="chat-bubble__typing" aria-label={`AI ${text}`}>
                  <Loading size="small" />
                  {Array.from(text).map((ch, i) => (
                    <span key={i} className="chat-bubble__typing-char" style={{ ['--i' as string]: String(i) }}>{ch}</span>
                  ))}
                </div>
              );
            })()
          ) : msg.subtype === 'missing_hint' ? (
            // 信息不足提示气泡（not_ready）：长文本折叠，提供「展开/收起」
            <div className="chat-missing-hint">
              <div className={`chat-missing-hint__text chat-clamp${expandedDesc ? ' is-expanded' : ''}`} style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
              <button type="button" className="chat-missing-hint__toggle" onClick={() => onToggleDesc(msg.id)}>
                {expandedDesc ? '收起 ▴' : '展开 ▾'}
              </button>
            </div>
          ) : msg.subtype === 'ticket_overview' && msg.ticket_overview ? (
            // 工单概览气泡：confirm 成功后插入，展示工单详情 + 派单状态，点击进入工单详情页
            <div
              className="chat-ticket-overview"
              role="button"
              tabIndex={0}
              title="点击查看工单详情"
              onClick={() => onOpenTicket(msg.ticket_overview!.db_id)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onOpenTicket(msg.ticket_overview!.db_id);
                }
              }}
            >
              <div className="chat-ticket-overview__header">
                <FolderClosed size={14} strokeWidth={2} className="chat-ticket-overview__icon" />
                <span className="chat-ticket-overview__id">工单 #{msg.ticket_overview.db_id}</span>
                {msg.ticket_overview.type && <Tag theme="primary">{TICKET_TYPE_LABEL[msg.ticket_overview.type] || msg.ticket_overview.type}</Tag>}
                {msg.ticket_overview.priority && <Tag theme="warning">{msg.ticket_overview.priority}</Tag>}
                <span className="chat-ticket-overview__arrow" aria-hidden>›</span>
              </div>
              <div className="chat-ticket-overview__title">{msg.ticket_overview.title}</div>
              {msg.ticket_overview.project && <div className="chat-ticket-overview__row"><FolderClosed size={12} strokeWidth={2} /> {msg.ticket_overview.project}</div>}
              {msg.ticket_overview.contact && <div className="chat-ticket-overview__row"><User size={12} strokeWidth={2} /> {msg.ticket_overview.contact}</div>}
              {msg.ticket_overview.description && (
                <>
                  <div className={`chat-ticket-overview__desc chat-clamp${expandedDesc ? ' is-expanded' : ''}`}>{msg.ticket_overview.description}</div>
                  <button type="button" className="chat-ticket-overview__toggle" onClick={(e) => { e.stopPropagation(); onToggleDesc(msg.id); }}>
                    {expandedDesc ? '收起 ▴' : '展开 ▾'}
                  </button>
                </>
              )}
              <div className="chat-ticket-overview__footer">
                {msg.ticket_overview.assigned_to_name ? (
                  <span className="chat-ticket-overview__assigned"><CheckCircle2 size={14} strokeWidth={2} /> 已派单 · {msg.ticket_overview.assigned_to_name}</span>
                ) : (
                  <span className="chat-ticket-overview__dispatching">
                    <i className="dispatch-pulse dispatch-pulse--inline" />派单中…
                  </span>
                )}
              </div>
            </div>
          ) : msg.content ? (
            msg.streaming ? (
              <div className="chat-bubble__text" style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
            ) : (
              <MarkdownRenderer content={msg.content} compact={compact} />
            )
          ) : (
            <div className="chat-bubble__typing" aria-label="AI 正在分析">
              <Loading size="small" />
              <span className="chat-bubble__typing-char">思</span>
              <span className="chat-bubble__typing-char">考</span>
              <span className="chat-bubble__typing-char">中</span>
            </div>
          )
        ) : (
          <div className="chat-bubble__text">{msg.content}</div>
        )}
      </div>

      {/* 气泡操作行（设计稿：22px 圆形小钮 + 14px 图标，muted/70 默认色，hover 出 secondary 圆底，选中 blue-2） */}
      <div className="chat-actions">
        {msg.role === 'assistant' && (
          <>
            <button className={`chat-action ${msg.reaction === 'like' ? 'is-active' : ''}`} onClick={() => onToggleReaction(msg.id, 'like')} aria-label="点赞">
              <ThumbsUp size={14} strokeWidth={2} />
            </button>
            <button className={`chat-action ${msg.reaction === 'dislike' ? 'is-active' : ''}`} onClick={() => onToggleReaction(msg.id, 'dislike')} aria-label="点踩">
              <ThumbsDown size={14} strokeWidth={2} />
            </button>
            <button className="chat-action" onClick={() => onCopy(msg.content)} aria-label="复制">
              <Copy size={14} strokeWidth={2} />
            </button>
          </>
        )}
        {msg.role === 'user' && (
          <>
            <button className="chat-action" onClick={() => onCopy(msg.content)} aria-label="复制">
              <Copy size={14} strokeWidth={2} />
            </button>
            {editingId === msg.id ? (
              <>
                <button className="chat-action is-active" onClick={() => onEditSave(msg)} aria-label="保存">
                  <Check size={14} strokeWidth={2.2} />
                </button>
                <button className="chat-action" onClick={onEditCancel} aria-label="取消">
                  <X size={14} strokeWidth={2.2} />
                </button>
              </>
            ) : (
              <button className="chat-action" onClick={() => onEditStart(msg.id)} aria-label="编辑">
                <Pencil size={14} strokeWidth={2} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
});

const SCENE_CONFIG: Record<ChatScene, {
  sceneType: string;
  emptyEmoji: string;
  emptyTitle: string;
}> = {
  call: { sceneType: 'chat', emptyEmoji: '🆘', emptyTitle: '一支穿云箭，千军万马来相见！' },
  tasks: { sceneType: 'task_assist', emptyEmoji: '🤖', emptyTitle: 'AI 任务助手' },
};

const TICKET_TYPE_LABEL: Record<string, string> = {
  problem: '报障', bug: '缺陷', feature: '需求', support: '支持', other: '其他',
};

// 按会话 id 的内存消息缓存（模块级）：切走前把当前会话最新 messages（含未落库的乐观消息）存入，
// 切回时优先从此同步恢复。提升到模块级以跨 ChatPanel 卸载/重挂载（切 Tab）存活——
// 否则切 Tab 卸载后 ref 丢失，切回只能落库重拉（且 appendMessage 落库竞态会丢新消息）。
const convMessagesCache: Record<number, Message[]> = {};

export default function ChatPanel({ scene, compact = false }: { scene: ChatScene; compact?: boolean }) {

  const { token, name, username } = useAuthStore();
  const { chatContext, consumeChatContext, refreshTasks, conversationId, setConversationId, setConversationTitle, renameConversation, refreshConversations, requestNewConversation } = useWorkbenchStore();
  const isCall = scene === 'call';
  const cfg = SCENE_CONFIG[scene];
  const navigate = useNavigate();

  const [messages, setMessages] = useState<Message[]>([]);
  // 图片预览：点击用户气泡图片 → 全屏遮罩放大查看
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [submittingTicket, setSubmittingTicket] = useState(false);
  // 转工单悬浮球拖拽（设计稿：pointer 捕获拖拽，位移 >8px 视为移动并抑制点击；位置 clamp 在窗口内）
  const fabRef = useRef<HTMLButtonElement>(null);
  const [fabPos, setFabPos] = useState<{ x: number; y: number } | null>(null);
  const fabDragRef = useRef({ active: false, moved: false, justDragged: false, startX: 0, startY: 0, baseX: 0, baseY: 0 });
  const FAB_SIZE = 52;
  const clampFabPos = (x: number, y: number) => ({
    x: Math.min(Math.max(8, x), window.innerWidth - FAB_SIZE - 8),
    y: Math.min(Math.max(8, y), window.innerHeight - FAB_SIZE - 88),
  });
  const onFabPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    const el = fabRef.current;
    if (!el) return;
    el.setPointerCapture(e.pointerId);
    const r = el.getBoundingClientRect();
    fabDragRef.current = { active: true, moved: false, justDragged: false, startX: e.clientX, startY: e.clientY, baseX: r.left, baseY: r.top };
  };
  const onFabPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = fabDragRef.current;
    if (!d.active) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 8) return;
    d.moved = true;
    setFabPos(clampFabPos(d.baseX + dx, d.baseY + dy));
  };
  const onFabPointerUp = () => {
    const d = fabDragRef.current;
    if (d.moved) d.justDragged = true;
    d.active = false;
    d.moved = false;
  };
  // 转工单二次确认弹窗：prepare 生成草稿 → 用户核对/编辑/补字段 → confirm 入库
  const [ticketConfirm, setTicketConfirm] = useState<{
    visible: boolean;
    draft: TicketDraft | null;
    overrides: Partial<TicketDraft>;
    submitting: boolean;
    force_submit?: boolean;  // 收集超限强制提单时为 true，弹窗顶部黄色 banner 提示用户重点核对
    dualTicket: boolean;      // 兜底双工单：项目不在项目集时勾选，生成申请单派给项目负责人
    projectOwner: UserItem | null;  // 双工单场景下选中的项目负责人
  }>({ visible: false, draft: null, overrides: {}, submitting: false, force_submit: false, dualTicket: false, projectOwner: null });
  // 提单基准时间：首次打开确认弹窗时固定（= 提单时刻），切换优先级/后续操作不漂移，
  // 使「最晚解决时间 = 提单时间 + 优先级时长」恒定，不随用户修改时间变化。
  const ticketBaseTimeRef = useRef<dayjs.Dayjs | null>(null);
  // 转工单信息不足引导（方案A）：prepare 返回 not_ready 时，
  // 在输入框上方常驻「待补充清单」卡片 + 转工单按钮角标，引导用户回对话补全
  const [ticketMissing, setTicketMissing] = useState<{ info: string[]; message: string } | null>(null);
  // 气泡长文本「展开/收起」：记录已展开的消息 id（工单概览描述、缺失提示气泡共用）
  const [expandedMsgIds, setExpandedMsgIds] = useState<Set<string>>(new Set());
  const toggleMsgExpanded = useCallback((id: string) => {
    setExpandedMsgIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);
  const [isRecording, setIsRecording] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [voiceWillCancel, setVoiceWillCancel] = useState(false);
  const [textareaFullscreen, setTextareaFullscreen] = useState(false);
  const [textareaMaxed, setTextareaMaxed] = useState(false);
  // 「猜你想问」空输入随机推荐池（首次新建会话时展示 3 条，可「换一批」重新随机）
  const [randomPool, setRandomPool] = useState<string[]>(() => pickRandomQuestions(3));
  // 「猜你想问」防抖关键词：输入停顿 200ms 后才检索，避免每击键刷新列表造成抖动
  const [debouncedKeyword, setDebouncedKeyword] = useState('');
  const textareaContainerRef = useRef<HTMLDivElement>(null);
  const voiceStartYRef = useRef<number>(0);
  const voiceCancelRef = useRef(false);
  const voiceWillCancelRef = useRef(false);
  const voiceSessionRef = useRef(0);       // 递增，防止延迟的 onend 误修改状态
  const voiceHoldingRef = useRef(false);  // 用户是否正在按住语音按钮
  const voiceBtnRef = useRef<HTMLButtonElement>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const albumInputRef = useRef<HTMLInputElement>(null);
  // 文件上传大小上限：100MB（MinIO 单对象理论上限 5TB，此处为前端体验上限，可按需调整）
  const MAX_FILE_SIZE = 100 * 1024 * 1024;
  const [showUploadMenu, setShowUploadMenu] = useState(false);
  // 待发送附件：选中文件先挂起（不立即发送），用户可继续打字，发送时随 message 一起上传
  // 待发送附件：file 与预览 url 绑定为同一对象，彻底消除「双 state + index 对齐」导致的
  // 预览图与实际 File 错位问题（#384：粘贴图片预览正确但发送成另一张）。
  const [pendingItems, setPendingItems] = useState<Array<{ file: File; url?: string }>>([]);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  // 语音 tap/hold 双模式 + 真实音量可视化
  const voiceInteractionModeRef = useRef<'tap' | 'hold' | null>(null);
  const longPressTimerRef = useRef<number | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const voiceRafRef = useRef<number | null>(null);
  const [voiceLevels, setVoiceLevels] = useState<number[]>([0, 0, 0, 0, 0]);
  const convRef = useRef<number | null>(null); // 当前 DB 会话 id，跨 send 复用
  // convMessagesCache 已提升为模块级（见组件定义上方），跨 ChatPanel 卸载/重挂载（切 Tab）存活
  const prevConvIdRef = useRef<number | null>(null); // 记录上一轮会话 id，确保切走时写到「旧会话」而非已切换的「新会话」
  const sendingRef = useRef(false); // 防双发（Enter + click 竞态）
  // 流式中断控制：切换会话/卸载时 abort 正在进行的流式请求，避免后台 setMessages 串台到新会话导致回复丢失
  const abortRef = useRef<AbortController | null>(null);

  // 滚动跟随：仅在用户贴底时自动跟随；流式中瞬时置底（behavior:'auto'）避免 smooth 动画排队抖动
  const atBottomRef = useRef(true);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0); // 上一次消息条数：区分「新消息追加」与「内容增长」
  // 用户滚动意图标记：wheel/touchstart 手势一开始即置 true，800ms 防抖复位。
  // 程序置底据此避让——用户手指刚搭上/滚轮刚动（scrollTop 尚未变化、atBottom 未翻转）时，
  // 新消息/流式更新也不把视图拽走，杜绝「抢滚动」。
  const userScrollingRef = useRef(false);
  const userScrollResetTimerRef = useRef<number | null>(null);
  /** 恢复贴底跟随：用户主动行为（发送消息/确认提交/切换会话）后调用，重置上滑阅读状态 */
  const resumeFollowBottom = useCallback(() => {
    atBottomRef.current = true;
    userScrollingRef.current = false;
    if (userScrollResetTimerRef.current) {
      clearTimeout(userScrollResetTimerRef.current);
      userScrollResetTimerRef.current = null;
    }
  }, []);
  const markUserScrolling = useCallback(() => {
    userScrollingRef.current = true;
    if (userScrollResetTimerRef.current) clearTimeout(userScrollResetTimerRef.current);
    userScrollResetTimerRef.current = window.setTimeout(() => {
      userScrollingRef.current = false;
      userScrollResetTimerRef.current = null;
    }, 800);
  }, []);
  // 惯性滚动（类 GSAP）：桌面端接管 wheel 做 lerp 缓动 + 松手惯性 + 边界橡皮筋，触摸保留原生。
  // 通用 hook 可复用到任意滚动容器；程序 scrollTop 赋值（置底）会被 hook 识别为外部滚动并同步，互不冲突。
  useInertiaScroll(messagesContainerRef);
  // 只滚内层消息容器（scrollTop），绝不用 scrollIntoView——它会连带滚动所有可滚动祖先
  // （外层 .tabbar-shell__content 也在其列），曾导致「上滑看历史被强制拉回底部、无法滚到顶」
  const scrollContainerToBottom = useCallback(() => {
    const el = messagesContainerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);
  const scrollToBottom = useCallback(() => {
    if (!atBottomRef.current) return;
    scrollContainerToBottom();
  }, [scrollContainerToBottom]);
  // 强制滚动到底部（重置滚动状态后贴底）：加载/切换历史会话后调用，确保「进入即见最新消息」。
  const scrollToBottomNow = useCallback(() => {
    resumeFollowBottom();
    requestAnimationFrame(scrollContainerToBottom);
  }, [scrollContainerToBottom, resumeFollowBottom]);
  useEffect(() => {
    // 新消息追加（条数增加）：用户已上滑阅读（atBottom=false）或正处于滚动手势 → 不打断；
    // 用户主动发送（send/sendWithFile 先 resumeFollowBottom 恢复贴底再追加）天然命中置底。
    // 仅内容增长（流式 token / 编辑）→ 仅贴底时跟随。
    const appended = messages.length > prevCountRef.current;
    prevCountRef.current = messages.length;
    if (appended) {
      if (userScrollingRef.current) return;
      if (atBottomRef.current) scrollContainerToBottom();
    } else {
      scrollToBottom();
    }
  }, [messages, scrollToBottom, scrollContainerToBottom]);
  // 监听用户滚动，判断是否贴底（上滑看历史时不强制拉回）；wheel/touchstart 标记滚动意图
  useEffect(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    el.addEventListener('wheel', markUserScrolling, { passive: true });
    el.addEventListener('touchstart', markUserScrolling, { passive: true });
    return () => {
      el.removeEventListener('scroll', onScroll);
      el.removeEventListener('wheel', markUserScrolling);
      el.removeEventListener('touchstart', markUserScrolling);
      if (userScrollResetTimerRef.current) {
        clearTimeout(userScrollResetTimerRef.current);
        userScrollResetTimerRef.current = null;
      }
    };
  }, [markUserScrolling]);

  // 检测 textarea 是否达到最大高度，显示全屏按钮
  useEffect(() => {
    const ta = textareaContainerRef.current?.querySelector('textarea');
    if (ta) setTextareaMaxed(ta.scrollHeight > ta.clientHeight + 2);
  }, [input]);

  // 「猜你想问」检索防抖：输入停顿 200ms 后更新关键词；清空输入立即恢复（不走防抖）
  useEffect(() => {
    const trimmed = input.trim();
    if (!trimmed) {
      setDebouncedKeyword('');
      return;
    }
    const timer = setTimeout(() => setDebouncedKeyword(trimmed), 200);
    return () => clearTimeout(timer);
  }, [input]);

  // 进入页默认恢复「最近会话」：不再强制新建（新建会话仅由抽屉「新建会话」按钮触发）。
  // 挂载/重新进入 我要摇人（登录、点服务号、切回 Tab 等）时：
  //   - 若用户显式点了「新建会话」(pendingNewConversation)，保持空白新会话，不做自动选择；
  //   - 否则若当前未选定会话(conversationId===null)，自动选最近一条历史会话并滚动到底部。
  useEffect(() => {
    if (!token || !username) return;
    (async () => {
      await refreshConversations();
      const {
        conversationId: current,
        conversations: list,
        pendingNewConversation,
      } = useWorkbenchStore.getState();
      if (pendingNewConversation) return; // 保持空白新会话，不自动选
      if (current === null && list.length > 0) {
        // list 已由后端按更新时间倒序返回，list[0] 即最近会话
        setConversationTitle(list[0].title && list[0].title !== '新会话' ? list[0].title : '新建会话');
        setConversationId(list[0].id);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, username, scene]);

  // conversationId 变化 → 加载会话（切换）或清空（新建）。
  // 注意：首次挂载时若 conversationId 已有值（切 Tab 重挂载、store 保留选中），必须从这里恢复，
  // 否则消息为空。仅在「首次挂载且尚未选中会话(null)」时跳过，交给上方 [token,username,scene] effect 选最近会话。
  // （旧实现无条件首次跳过，开发态靠 React StrictMode 二次执行 effect 掩盖了该 bug；
  //  生产无 StrictMode 二次执行 → 切 Tab 回来消息丢失。）
  const convLoadedRef = useRef(false);
  useEffect(() => {
    // 切换会话：中断上一个会话正在进行的流式回复。否则流式后台继续 setMessages，
    // 但 messages 已被新会话替换，旧会话回复会串台丢失（表现为"回复闪一下就没了"）。
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    const firstMountNoSelection = !convLoadedRef.current && conversationId === null;
    convLoadedRef.current = true;
    if (firstMountNoSelection) return;
    if (conversationId === null) {
      // 新建会话：清空消息 + sessionId，标题显示「新建会话」
      convRef.current = null;
      setMessages([]);
      setSessionId('');
      setConversationTitle('新建会话');
      return;
    }
    // 优先从内存缓存恢复：切回已有会话时可零延迟还原（含未落库的乐观消息），
    // 根除 getConversation 早于 appendMessage 落库 / 切回竞态导致的新消息丢失。
    const cached = convMessagesCache[conversationId];
    if (cached) {
      convRef.current = conversationId;
      setMessages(cached);
      scrollToBottomNow();
      // 后台静默校正：缓存可能是乐观消息/流式中间态快照，DB 为最终一致源。
      // 仅当 DB 条数更多（有新落库的消息）才覆盖：appendMessage 是 fire-and-forget，
      // 切回瞬间 DB 可能还少于缓存（乐观用户消息 / AI 回复尚未落库），双向不等会把这些
      // 未落库消息覆盖丢失。会话消息只增不改，条数相同即一致，无需回滚。
      getConversation(conversationId).then((full) => {
        if (convRef.current !== conversationId) return; // 校正期间又切走了，丢弃
        const fresh = mapDbMessages(full);
        setMessages((prev) => {
          if (fresh.length > prev.length) return fresh;
          // 长度未增加：不整体替换（防丢未落库的乐观消息），但以 DB 为准同步工单概览气泡的派单状态——
          // 否则切走期间已派单并回写 DB 后，长度相同不覆盖，气泡仍停留在"派单中"。
          return prev.map((m) => {
            if (m.subtype === 'ticket_overview' && m.ticket_overview && !m.ticket_overview.assigned_to_name) {
              const f = fresh.find((x) => x.ticket_overview?.db_id === m.ticket_overview!.db_id);
              if (f?.ticket_overview?.assigned_to_name) {
                return { ...m, ticket_overview: { ...m.ticket_overview, assigned_to_name: f.ticket_overview.assigned_to_name } };
              }
            }
            return m;
          });
        });
        // 恢复 sessionId / 标题：缓存恢复分支跳过了 getConversation，这里补上，
        // 否则切回后 sessionId 仍为上一会话的/空，发送时 ensureSessionId 会重新生成 → sessionId 漂移
        const sid = readAiSessionId(full);
        if (sid) setSessionId(sid);
        setConversationTitle(full.title && full.title !== '新会话' ? full.title : '新建会话');
      }).catch(() => {});
      return;
    }
    // 缓存为空（首次进入 / 刷新后）→ 从后端加载
    let cancelled = false;
    let pollId: ReturnType<typeof setInterval> | null = null;
    (async () => {
      try {
        const full = await getConversation(conversationId);
        if (cancelled) return;
        convRef.current = full.id;
        // mapDbMessages 统一做：附件恢复 + AI 文本清洗（带 }/JSON 残留）+ 空白 AI 气泡过滤
        const restored: Message[] = mapDbMessages(full);
        setMessages(restored);
        setConversationTitle(full.title && full.title !== '新会话' ? full.title : '新建会话');
        const sid = readAiSessionId(full);
        if (sid) setSessionId(sid);
        else setSessionId('');
        scrollToBottomNow();
        // producer 后台生成中（最后一条 assistant 标记 streaming）：轮询 DB 等 producer 落库完整回复。
        // 刷新打断 SSE 后后端 producer 仍在独立生成，此处轮询拿到 content 后自动替换占位气泡。
        const last = restored[restored.length - 1];
        if (last && last.role === 'assistant' && last.streaming) {
          pollId = setInterval(async () => {
            if (cancelled || convRef.current !== conversationId) {
              if (pollId) clearInterval(pollId);
              return;
            }
            try {
              const fresh = await getConversation(conversationId);
              if (cancelled || convRef.current !== conversationId) {
                if (pollId) clearInterval(pollId);
                return;
              }
              const freshMsgs = mapDbMessages(fresh);
              const newLast = freshMsgs[freshMsgs.length - 1];
              if (newLast && newLast.role === 'assistant' && newLast.content.trim()) {
                setMessages(freshMsgs);
                scrollToBottomNow();
                if (pollId) clearInterval(pollId);
              }
            } catch { /* 轮询失败忽略，下次重试 */ }
          }, 2000);
        }
      } catch (e) {
        console.warn('[ChatPanel] 历史消息加载失败:', e);
        Toast({ message: '历史消息加载失败，请刷新重试', theme: 'error' });
      }
    })();
    return () => { cancelled = true; if (pollId) clearInterval(pollId); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

  // 切换会话时清掉「待补充清单」卡片：缺口属于具体会话，换会话即失效
  useEffect(() => { setTicketMissing(null); }, [conversationId]);

  // 切走前把当前会话的最新 messages 写入内存缓存（按会话 id），供切回时立即恢复。
  // 用 prevConvIdRef 记录上一轮会话 id，确保写入的是「旧会话」而非已切换的「新会话」，
  // 避免 conversationId 已变但 messages 尚未被新会话覆盖时把旧消息错存到新会话 key。
  useEffect(() => {
    const prev = prevConvIdRef.current;
    if (prev != null && messages.length > 0) {
      // 剔除流式中间态气泡：半截内容不缓存（流式完成后的最终内容由 DB/后台校正兜底），
      // 避免切回时恢复出"过时"的流式快照。
      const stable = messages.filter((m) => !m.streaming);
      if (stable.length > 0) convMessagesCache[prev] = stable;
    }
    prevConvIdRef.current = conversationId;
  }, [conversationId, messages]);

  // call 场景：进入时若带工单讨论上下文，注入引导消息（一次性消费）
  useEffect(() => {
    if (!isCall) return;
    const ctx = consumeChatContext();
    if (ctx) {
      setMessages((prev) => [
        ...prev,
        {
          id: uid(),
          role: 'assistant',
          content: `关于工单 #${ctx.ticketId}「${ctx.title}」：\n${ctx.description || ''}\n\n我已了解该工单上下文，请告诉我你需要协助分析或处理的方向。`,
          timestamp: new Date().toISOString(),
        },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatContext]);

  /** 确保 sessionId——新 AI 模块无需预先创建会话 */
  const ensureSessionId = useCallback((): string => {
    if (!sessionId) {
      const id = generateSessionId();
      setSessionId(id);
      trackSession(id);
      return id;
    }
    return sessionId;
  }, [sessionId]);

  /** 确保 DB 会话存在：首条消息时创建（title 用占位「新会话」，第2轮由 AI 生成后同步），后续复用 convRef */
  const ensureConversation = async (sid: string, firstContent: string): Promise<number | null> => {
    if (convRef.current) return convRef.current;
    try {
      const conv = await createConversation({
        title: '新会话',
        scene,
        aiSessionId: sid,
      });
      convRef.current = conv.id;
      return conv.id;
    } catch {
      return null;
    }
  };

  /** 带附件发送：文件(可附文字)一起上传 /qa/upload（SSE 流式），逐步推送 VLM 分析 + 诊断。
   * 方案一（乐观渲染）：点发送即插入用户气泡（附件+文字，内嵌上传进度遮罩）+ AI 分析占位气泡，
   * 上传进度实时更新到用户气泡，完成后遮罩消失、AI 回复填入占位气泡。文件名不拼进文字上下文。 */
  const sendWithFile = async (items: Array<{ file: File; url?: string }>, content: string) => {
    const files = items.map((it) => it.file);
    // 每个文件独立气泡（像原来单文件一样各自显示图片缩略图/文件卡片）
    const firstImage = files.find((f) => f.type.startsWith('image/'));
    const assistantId = uid();
    const hasMessage = content.trim().length > 0;
    const initialPhase: Message['phase'] = firstImage ? 'analyzing_image' : 'analyzing_file';
    const userIds = files.map(() => uid());
    const now = new Date().toISOString();
    const newMsgs: Message[] = [];
    // 有附带文字时先插一个纯文字气泡
    if (hasMessage) {
      newMsgs.push({ id: uid(), role: 'user', content, timestamp: now });
    }
    // 每个文件一个气泡：图片用 blob 缩略图，文件用文件名+大小卡片
    files.forEach((f, i) => {
      const isImage = f.type.startsWith('image/');
      newMsgs.push({
        id: userIds[i],
        role: 'user',
        content: '',
        timestamp: now,
        // 复用已创建的预览 objectURL（避免重复创建；后续上传完成回填真实URL）
        imageUrl: isImage ? (items[i].url || URL.createObjectURL(f)) : undefined,
        attachment: isImage ? null : { name: f.name, size: f.size },
        uploading: true,
        percent: 0,
      });
    });
    // AI 占位气泡
    newMsgs.push({ id: assistantId, role: 'assistant', content: '', phase: initialPhase, streaming: true, timestamp: now });
    setMessages((prev) => [...prev, ...newMsgs]);
    setInput('');
    clearPendingFiles();
    setLoading(true);

    // 流式累计 + 节流渲染（对齐 send 的流式体验）
    let acc = '';
    let visionDone = false;
    let convId: number | null = null;
    let hasResult = false;
    let streamError = '';
    let lastFlush = 0;
    const FLUSH_MS = 90;
    const paint = () => setMessages((prev) => prev.map((m) => {
      if (m.id !== assistantId) return m;
      if (acc) {
        return { ...m, content: looksLikeJsonHead(acc) ? '正在思考…' : acc, phase: undefined };
      }
      const phase: Message['phase'] = hasMessage
        ? (visionDone ? 'thinking' : (firstImage ? 'analyzing_image' : 'analyzing_file'))
        : (firstImage ? 'analyzing_image' : 'analyzing_file');
      return { ...m, phase };
    }));
    const schedulePaint = () => {
      const now = Date.now();
      if (now - lastFlush >= FLUSH_MS) { lastFlush = now; paint(); }
    };

    // 已保存的附件元数据（逐个 file_saved 事件累积）
    const savedItems: Array<{ filename: string; object_path?: string; size: number; isImage: boolean }> = [];

    try {
      const sid = ensureSessionId();

      await qaUploadStream(sid, files, content, {
        onFileSaved: async (d) => {
          try {
            // 逐文件回执：d.saved 为本次完成的单个文件（档1改造后逐文件 yield）
            const uploaded = d.saved?.[0];
            if (!uploaded) return;
            const fileIdx = files.findIndex((f) => f.name === uploaded.filename);
            const matchedFile = fileIdx >= 0 ? files[fileIdx] : null;
            const isImg = matchedFile?.type.startsWith('image/') ?? false;
            const fileUrl = uploaded.object_path ? attachmentUrl(uploaded.object_path) : undefined;
            savedItems.push({ filename: uploaded.filename, object_path: uploaded.object_path, size: uploaded.size, isImage: isImg });
            // 回填对应文件气泡的真实 URL（blob:→代理URL），上传完成遮罩消失
            if (fileIdx >= 0 && userIds[fileIdx]) {
              setMessages((prev) => prev.map((m) => {
                if (m.id !== userIds[fileIdx]) return m;
                const updated: Message = { ...m, uploading: false, percent: 100 };
                if (isImg && fileUrl) {
                  if (m.imageUrl?.startsWith('blob:')) URL.revokeObjectURL(m.imageUrl);
                  updated.imageUrl = fileUrl;
                } else if (!isImg && fileUrl && m.attachment) {
                  updated.attachment = { ...m.attachment, url: fileUrl };
                }
                return updated;
              }));
            }
            // 全部保存后持久化用户消息
            if (savedItems.length === files.length) {
              convId = await ensureConversation(sid, content || `[发送了${files.length}个附件]`);
              if (convId) {
                const cid: number = convId;
                const fileUrls = JSON.stringify(savedItems.map((it) => ({ filename: it.filename, object_path: it.object_path, size: it.size, isImage: it.isImage })));
                const persist = async (attempt: number): Promise<void> => {
                  try {
                    await appendMessage(cid, 'user', content || `[发送了${files.length}个附件]`, { fileUrls, messageType: firstImage ? 'image' : 'file' });
                  } catch (e) {
                    if (attempt < 3) {
                      await new Promise((r) => setTimeout(r, 300 * attempt));
                      return persist(attempt + 1);
                    }
                    console.warn('[ChatPanel] 附件消息落库失败（已重试3次）:', e);
                  }
                };
                void persist(1);
              }
            }
          } catch (e) {
            console.warn('[ChatPanel] 上传文件已保存，但持久化会话失败:', e);
          }
        },
        onToken: (tok) => {
          if (!visionDone && hasMessage) {
            return;
          }
          acc += tok;
          schedulePaint();
        },
        onVisionDone: () => {
          visionDone = true;
          if (hasMessage) paint();
        },
        onResult: (data) => {
          hasResult = true;
          if (data.ticket) refreshTasks();
          if (!acc && typeof data.message === 'string' && data.message) {
            acc = data.message;
            paint();
          }
        },
        onError: (msg) => {
          streamError = msg;
        },
      });

      if (streamError && !acc) {
        // 文件已全部保存成功 → 是诊断流中断，不是上传失败；
        // 只提示 AI 回复中断，不标红文件气泡（避免「图片上传失败」误报）。
        if (savedItems.length === files.length) {
          setMessages((prev) => prev.filter((m) => m.id !== assistantId));
          Toast({ message: `AI 回复中断，请重试: ${streamError}`, theme: 'error' });
          return;
        }
        throw new Error(streamError);
      }

      const finalContent = sanitizeAiText(acc);
      if (finalContent) {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: finalContent, phase: undefined, streaming: false } : m)));
        if (convId) appendMessage(convId, 'assistant', finalContent).catch(() => {});
      } else if (!hasResult) {
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      } else {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)));
      }
    } catch (err) {
      // 区分「文件从未保存成功」和「文件已保存但诊断流中断」：
      // 前者才标红文件气泡（真上传失败），后者只提示 AI 回复中断。
      const allSaved = savedItems.length === files.length;
      if (allSaved) {
        // 文件全部保存成功：不标红文件，只移除 AI 占位气泡 + 提示回复中断
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        if (!isKickingToLogin()) {
          Toast({ message: `AI 回复中断，请重试: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      } else {
        // 部分/全部未保存：只标红未保存成功的文件（已保存的不标红）
        setMessages((prev) => prev.map((m) => {
          const idx = userIds.indexOf(m.id);
          if (idx < 0) return m;
          const fname = files[idx]?.name ?? '';
          const wasSaved = savedItems.some((it) => it.filename === fname);
          return wasSaved ? m : { ...m, uploading: false, failed: true };
        }));
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        if (!isKickingToLogin()) {
          Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      }
    } finally {
      setLoading(false);
    }
  };

  const send = async (text: string) => {
    const content = text.trim();
    const files = pendingItems.map((it) => it.file);
    if (!content && files.length === 0) return;
    if (!token) { kickToLogin('请先登录'); return; }
    if (sendingRef.current) return; // 防双发
    sendingRef.current = true;
    // 用户主动发送：恢复贴底跟随（即使刚才在上滑看历史，最新对话也要立即进入视野）
    resumeFollowBottom();
    // 用户开始补充信息：清掉待补充清单卡片（新一轮对话后再 prepare 会重新给出最新缺口）
    setTicketMissing(null);

    // 带附件：走 /qa/upload（SSE 流式），由 sendWithFile 流式渲染 VLM 分析 + 诊断
    if (files.length > 0) {
      try {
        await sendWithFile(pendingItems, content);
      } finally {
        sendingRef.current = false;
      }
      return;
    }

      // 纯文字：走 /qa/ask/stream 流式
    const userMessage: Message = {
      id: uid(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    const assistantId = uid();
    // 节流渲染相关变量提升到函数作用域：try 块内的 const/let 对 finally 不可见，必须外提
    let acc = '';
    // sentConvId 快照：流式期间用户可能切换会话，convRef 会被 effect 改写指向新会话；
    // 后续 AI 回复落库/首轮会话同步必须用此快照，否则回复会错写进新会话（表现为"过时/错位回复"）
    let sentConvId: number | null = null;
    // 后端增量落库写出的 assistant 消息 DB id：由流式 event:message_created 回传。
    // 命中即说明后端已接管落库，前端不再重复写（避免重复消息）；未命中(老后端/建消息失败)则前端兜底落库。
    let assistantDbId: number | null = null;
    let lastFlush = 0;
    const FLUSH_MS = 90;
    // 流式中间渲染：疑似 LLM 协议 JSON 头泄漏（{ / ``` 开头）时以占位代替，避免残破 JSON 闪现上屏
    const renderAcc = () => setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: looksLikeJsonHead(acc) ? '正在思考…' : acc } : m)));
    const scheduleRender = () => {
      const now = Date.now();
      if (now - lastFlush >= FLUSH_MS) {
        lastFlush = now;
        renderAcc();
      }
    };
    try {
      const sid = ensureSessionId();
      const wasNew = !convRef.current; // 新会话：首轮问答完成后才同步到列表
      // 持久化用户消息（首条会顺带建会话）
      const convId = await ensureConversation(sid, content);
      if (convId) appendMessage(convId, 'user', content).catch((e) => console.warn('[ChatPanel] 用户消息落库失败:', e));
      // 发送时会话快照：流式期间用户可能切换会话，convRef 会被 effect 改写指向新会话。
      // 后续 AI 回复持久化/首轮会话同步必须用此快照，否则回复会错写进新会话（表现为"过时/错位回复"）。
      sentConvId = convRef.current;
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '', streaming: true, timestamp: new Date().toISOString() }]);

      // 提单 Agent
      const apiPath = `${API_CONFIG.AI.BASE_URL}/qa/ask/stream`;
      // 把已落库的会话 id 传给后端，由后端在流式中增量落库 assistant 回复（刷新/切 Tab 可从 DB 恢复）
      const apiBody = JSON.stringify({ session_id: sid, query: content, conversation_id: sentConvId });

      // AbortController：切换会话/卸载时主动中断流式，避免后台 setMessages 串台丢消息
      const controller = new AbortController();
      abortRef.current = controller;
      const response = await fetchWithAuth(apiPath, { method: 'POST', body: apiBody, signal: controller.signal });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let solutionDraft: Message['solution_draft'] | null = null;
      let ticketCreatedThisTurn = false;
      let currentEvent = '';
      let streamError = ''; // 流式 event:error 的错误信息（之前静默吞掉 → 空气泡）
      // SSE 按行解析：chunk 边界可能切开一行（如 data: {"token":"部 + 分"}），
      // 用 buffer 拼接，pop() 保留最后不完整行到下个 chunk，避免 JSON.parse 失败丢 token（空白）。
      const processLine = (line: string) => {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7);
          return;
        }
        if (!line.startsWith('data: ')) return;
        try {
          const data = JSON.parse(line.slice(6));
          // 后端建好的 assistant 消息 DB id（后端 SSE 侧落库接管后回传）
          if (currentEvent === 'message_created' && data.message_id) {
            assistantDbId = data.message_id;
          }
          if (data.token) {
            acc += data.token;
            scheduleRender();
          } else if (data.content) {
            acc += data.content;
            scheduleRender();
          }
          // 流式错误（如诊断 pipeline 抛错）：捕获错误信息，循环结束后抛出，避免静默空气泡
          if (currentEvent === 'error' && data.error) {
            streamError = data.error;
          }
          // 任务 Agent result 事件：拿到结构化方案草稿
          if (currentEvent === 'result' && data.root_cause_analysis) {
            solutionDraft = {
              _task_id: data._task_id,
              root_cause_analysis: data.root_cause_analysis,
              suggested_actions: data.suggested_actions || [],
              references: data.references || [],
              confidence: data.confidence ?? 0,
              needs_more_info: data.needs_more_info ?? false,
            };
          }
          // AI 自动建单（对话中输入「转工单」等）：result 事件携带 ticket，标记本轮已建单
          if (currentEvent === 'result' && data.ticket) {
            ticketCreatedThisTurn = true;
          }
          // 第2轮 AI 生成会话标题：更新当前标题 + 刷新左侧列表（DB 已由后端同步）
          if (currentEvent === 'title' && data.title) {
            setConversationTitle(data.title);
            refreshConversations();
          }
          // submit 相关分支（need_info/need_fields/submit_failed）：后端已 _msg_buf.clear()
          // 改用干净系统话术，但流式期间已 flush 的 LLM 片段收不回 → 前端 acc 会拼接成
          // 「LLM 片段 + 系统话术」混乱。此处清空 acc，丢弃闪现的 LLM 片段，只接收后续系统话术。
          if (currentEvent === 'status' && ['need_info', 'need_fields', 'submit_failed'].includes(data.stage)) {
            acc = '';
          }
          // 提单生成中：LLM 的「好的」被后端抑制，切气泡到「正在生成工单」动画，
          // 弹窗（review）时后端会回填「已生成工单草稿…」话术，届时再显示。
          if (currentEvent === 'status' && data.stage === 'generating_ticket') {
            acc = '';
            setMessages((prev) => prev.map((m) => (
              m.id === assistantId ? { ...m, content: '', phase: 'generating_ticket' as const, streaming: true } : m
            )));
          }
          // 转工单确认弹窗（对话路径）：后端字段齐全 → event:status + stage:review → 弹窗，
          // 复用 ticketConfirm（与按钮路径 handleSubmitTicket 殊途同归）。
          // 幂等：弹窗已打开则不覆盖（保留用户正在编辑的 overrides），防后端重复发 review 重弹。
          if (currentEvent === 'status' && data.stage === 'review' && data.draft) {
            // 生成完成：清掉「正在生成工单」动画，等待后端回填的「已生成工单草稿…」token
            setMessages((prev) => prev.map((m) => (
              m.id === assistantId ? { ...m, phase: undefined } : m
            )));
            setTicketConfirm((s) => {
              if (s.visible) return s;
              // 提单基准时间：首次打开确认弹窗时固定，此后切换优先级/操作不漂移
              if (!ticketBaseTimeRef.current) ticketBaseTimeRef.current = dayjs();
              return {
                visible: true,
                draft: data.draft as TicketDraft,
                overrides: {},
                submitting: false,
                force_submit: !!data.force_submit,
                dualTicket: false,
                projectOwner: null,
              };
            });
          }
        } catch { /* JSON 行解析出错则跳过 */ }
      };
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';  // 最后可能不完整的行留 buffer，下个 chunk 拼接
        for (const line of lines) processLine(line);
      }
      // 流结束处理 buffer 末尾剩余行（最后 data 未跟换行的情况）
      if (buffer) processLine(buffer);

      // 流式定稿：清洗 JSON 泄漏/围栏/游离残留（带 } 回复）；纯空白（含仅空格/换行）→ ''，
      // 再统一走空回复兜底，杜绝空白气泡与残破 JSON 上屏、落库
      acc = sanitizeAiText(acc);
      // 前端空回复兜底：流式结束无任何内容（后端无 token 或前端解析丢字）→ 显示缺省，而非空气泡
      if (!acc && !streamError) acc = '[未收到 AI 回复，请重试]';
      // 流式出错且无任何内容 → 抛出，由外层 catch 提示并移除空气泡（不再静默）
      if (streamError && !acc) throw new Error(streamError);

      // 流式结束：后端已在流式中增量落库完整内容（event:message_created 接管）。
      // 仅当后端未接管（老后端未回传 message_id / 建消息失败）时，前端兜底落库一次，避免丢字。
      if (acc && assistantDbId == null && sentConvId) {
        appendMessage(sentConvId, 'assistant', acc).catch((e) => console.warn('[ChatPanel] AI 回复落库失败:', e));
      }
      // 首轮问答完成 → 同步会话到列表、定位到新会话。
      // 标题保持「新建会话」：标题由 AI 在第2轮回复时生成（event: title），在此之前都叫「新建会话」。
      // 仅当用户未切走（仍在发送时的会话）才执行跳转，避免把用户从别的会话拽回来
      if (wasNew && sentConvId && convRef.current === sentConvId) {
        setConversationId(sentConvId);
        refreshConversations();
      }
      // 任务 Agent 方案草稿：注入 solution_draft 标记
      if (solutionDraft && !isCall) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId
            ? { ...m, subtype: 'solution_draft' as const, solution_draft: solutionDraft ?? undefined }
            : m
        ));
      }
      // AI 自动建单（对话中输入「转工单」等）：本轮已建单 → 触发 badge 重新计数（与外层按钮路径一致）
      if (ticketCreatedThisTurn) {
        refreshTasks();
      }
    } catch (err) {
      // 主动中断 = AbortController 触发（fetch/reader 抛 AbortError）；其余视为真错误
      const aborted = err instanceof DOMException && err.name === 'AbortError';
      const finalAcc = sanitizeAiText(acc);
      if (aborted) {
        // 主动中断（切换会话/卸载）：后端已在流式中增量落库（≤0.8s 残差），切回原会话从 DB 恢复；
        // 仅后端未接管时前端兜底落库。无内容则移除空气泡（避免闪烁残留）。
        if (finalAcc) {
          if (assistantDbId == null && sentConvId) {
            appendMessage(sentConvId, 'assistant', finalAcc).catch((e) => console.warn('[ChatPanel] AI 回复落库失败:', e));
          }
        } else {
          setMessages((prev) => prev.filter((m) => m.id !== assistantId));
        }
      } else if (!isKickingToLogin()) {
        // 真错误：不再删除气泡（避免"闪烁后丢失"）。后端已落库部分内容时直接保留；
        // 仅后端未接管时前端兜底落库。无内容则给占位提示。
        Toast({ message: `发送失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
        if (finalAcc) {
          if (assistantDbId == null && sentConvId) {
            appendMessage(sentConvId, 'assistant', finalAcc).catch((e) => console.warn('[ChatPanel] AI 回复落库失败:', e));
          }
        } else {
          acc = '[回复中断，请重试]';
        }
      }
    } finally {
      // 先释放发送锁与 loading，再强制刷新最终内容，避免刷新异常时再次卡死发送
      setLoading(false);
      sendingRef.current = false;
      abortRef.current = null;
      // 回复完成：仅贴底时跟随（用户已上滑阅读则不打扰）；定稿 streaming:false 触发
      // Markdown 重渲染（代码块等高度突变），双 rAF 待布局稳定后再校正一次贴底
      scrollToBottom();
      requestAnimationFrame(() => requestAnimationFrame(scrollToBottom));
      // 定稿：合并为单次 setMessages。错误/中断时不再删除气泡，保留已接收内容（或占位提示），
      // 杜绝"闪烁一下就消失"。气泡已被 filter 删除时（中断且无内容）map 找不到，无操作。
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: sanitizeAiText(acc) || acc, streaming: false } : m)));
    }
  };

  const editAndResend = (msg: Message) => {
    setMessages((prev) => prev.filter((m) => m.id !== msg.id));
    setEditingId(null);
    send(msg.content);
  };
  // 用 ref 持有最新 editAndResend，向 memo 气泡提供稳定 onEditSave，避免编辑态频繁重渲染所有消息
  const editAndResendRef = useRef(editAndResend);
  editAndResendRef.current = editAndResend;
  const handleEditSave = useCallback((msg: Message) => editAndResendRef.current(msg), []);
  // 稳定 onEditChange / onEditCancel：内联箭头会让 MessageBubble 的 React.memo 失效（每次渲染新引用），
  // 导致流式 flush 时整列表重渲染、页面闪烁。包成 useCallback 后历史气泡可跳过重渲染。
  const handleEditChange = useCallback((id: string, v: string) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: v } : m)));
  }, []);
  const handleEditCancel = useCallback(() => setEditingId(null), []);
  // 稳定的工单跳转回调：传给 MessageBubble 保持 memo 不失效；点击对话内派单卡片跳历史工单详情页
  const handleOpenTicket = useCallback((dbId: number) => {
    navigate(`/call/ticket/db_${dbId}`);
  }, [navigate]);

  // voiceWillCancelRef 在 handleMove 中直接同步写入，不再通过 useEffect 异步同步

  // hold 模式：手指上滑 >60px 标记取消（pointermove 统一 touch/mouse，无合成事件）
  useEffect(() => {
    if (!voiceMode) return;
    const handleMove = (e: PointerEvent) => {
      if (voiceInteractionModeRef.current !== 'hold') return; // 仅 hold 检测上滑取消
      const deltaY = voiceStartYRef.current - e.clientY; // 正值 = 上移
      const willCancel = deltaY > 60;
      voiceWillCancelRef.current = willCancel;
      setVoiceWillCancel(willCancel);
    };
    document.addEventListener('pointermove', handleMove);
    return () => document.removeEventListener('pointermove', handleMove);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceMode]);

  // 创建 SpeechRecognition 实例：onend 时若仍在录音则自动重启，支持"一直长按说话"
  const createRecognition = (sessionId: number): SpeechRecognitionLike => {
    const rec = new SR!();
    rec.lang = 'zh-CN';
    rec.continuous = true;       // 持续识别，不因用户停顿而自动停止
    rec.interimResults = false;  // 仅返回最终识别结果
    rec.onresult = (ev: SpeechRecognitionResultEvent) => {
      if (voiceCancelRef.current) return; // 上移取消，丢弃结果
      let latest = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        latest = ev.results[i][0].transcript;
      }
      if (latest) setInput((prev) => (prev ? `${prev} ${latest}` : latest));
    };
    rec.onerror = () => {
      if (voiceSessionRef.current !== sessionId) return; // 旧 session 忽略
      voiceSessionRef.current++;       // 递增，防止后续 onend 再处理
      voiceHoldingRef.current = false;
      voiceInteractionModeRef.current = null;
      stopRecognition();
      stopAudioMonitor();
      setIsRecording(false);
      setVoiceMode(false);
    };
    rec.onend = () => {
      if (voiceSessionRef.current !== sessionId) return; // 旧 session 忽略
      // 仍在 hold 或 tap 录音 → 自动重启 SR（浏览器无声超时 onend 后续上，保证一直长按）
      if (voiceHoldingRef.current || voiceInteractionModeRef.current === 'tap') {
        try {
          const next = createRecognition(sessionId);
          recognitionRef.current = next;
          next.start();
        } catch { /* 重启失败则按停止处理 */ }
      } else {
        recognitionRef.current = null;
      }
    };
    return rec;
  };

  const startRecognition = () => {
    try {
      const rec = createRecognition(++voiceSessionRef.current);
      rec.start();
      recognitionRef.current = rec;
      setIsRecording(true);
    } catch { /* */ }
  };

  const stopRecognition = () => {
    voiceSessionRef.current++; // 使延迟的 onend/onerror 失效
    try { recognitionRef.current?.stop(); } catch { /* */ }
    recognitionRef.current = null;
  };

  // 真实音量可视化：getUserMedia → AudioContext → AnalyserNode → raf 驱动一排圆点
  const startAudioMonitor = async () => {
    try {
      // 复用 onVoiceBtnDown 在 pointerdown 手势内预创建的 AudioContext（手机端必需）
      const ctx = audioContextRef.current ?? new AudioContext();
      if (ctx.state === 'suspended') await ctx.resume(); // 必须 running，否则 graph 不渲染
      audioContextRef.current = ctx;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 64;
      const src = ctx.createMediaStreamSource(stream);
      src.connect(analyser);
      // 关键：analyser 必须连到 destination 才能驱动 graph 渲染，否则 getByteFrequencyData 恒为 0；
      // 中间插一个静音 gain，避免把麦克风声音回放出来（啸叫）
      const mute = ctx.createGain();
      mute.gain.value = 0;
      analyser.connect(mute);
      mute.connect(ctx.destination);
      analyserRef.current = analyser;
      const bins = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        const an = analyserRef.current;
        if (!an) return;
        an.getByteFrequencyData(bins);
        // 取 5 个频段（人声低-中频区）归一化到 0~1
        setVoiceLevels([2, 4, 6, 8, 10].map((idx) => (bins[idx] || 0) / 255));
        voiceRafRef.current = requestAnimationFrame(tick);
      };
      tick();
    } catch {
      // 麦克风权限/不支持：不影响 SR 识别，圆点保持静止
      setVoiceLevels([0, 0, 0, 0, 0]);
    }
  };

  const stopAudioMonitor = () => {
    if (voiceRafRef.current) { cancelAnimationFrame(voiceRafRef.current); voiceRafRef.current = null; }
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    audioContextRef.current?.close().catch(() => {});
    audioContextRef.current = null;
    analyserRef.current = null;
    setVoiceLevels([0, 0, 0, 0, 0]);
  };

  // tap 模式：再次轻触停止（留在语音模式，可继续轻触或切键盘）
  const stopTapRecording = () => {
    voiceInteractionModeRef.current = null;
    stopRecognition();
    stopAudioMonitor();
    setIsRecording(false);
  };

  // 按钮按下：tap/hold 入口（300ms 计时区分）。用 PointerEvent 统一 touch/mouse，无合成事件干扰，不需 preventDefault
  const onVoiceBtnDown = (e: React.PointerEvent) => {
    if (!SR) { Toast({ message: '当前浏览器不支持语音输入', theme: 'warning' }); return; }
    // tap 录音中 → 再次轻触停止
    if (voiceInteractionModeRef.current === 'tap' && recognitionRef.current) {
      stopTapRecording();
      return;
    }
    if (voiceHoldingRef.current || recognitionRef.current || longPressTimerRef.current) return;
    // 预创建并 resume AudioContext：必须在 pointerdown 用户手势同步段内，iOS/Android 手机端才允许；
    // 后续 setTimeout(hold)/pointerup(tap) 里再创建会被手势策略拒绝，导致 AnalyserNode 拿不到数据
    if (!audioContextRef.current) {
      try {
        const ctx = new AudioContext();
        if (ctx.state === 'suspended') ctx.resume().catch(() => {});
        audioContextRef.current = ctx;
      } catch { /* */ }
    }
    voiceStartYRef.current = e.clientY;
    voiceCancelRef.current = false;
    setVoiceWillCancel(false);
    // 300ms 长按计时：超时 → hold 开始录音
    longPressTimerRef.current = window.setTimeout(() => {
      longPressTimerRef.current = null;
      voiceHoldingRef.current = true;
      voiceInteractionModeRef.current = 'hold';
      startRecognition();
      startAudioMonitor();
    }, 300);
  };

  // hold 模式松手：停止录音（含上滑取消判定）
  const finishRecording = () => {
    if (voiceInteractionModeRef.current !== 'hold' || !voiceHoldingRef.current) return;
    voiceHoldingRef.current = false;
    if (voiceWillCancelRef.current) voiceCancelRef.current = true; // 上移取消，丢弃结果
    voiceSessionRef.current++;       // 递增，使任何延迟的 onend 失效
    stopRecognition();
    stopAudioMonitor();
    voiceInteractionModeRef.current = null;
    setIsRecording(false);
    if (!voiceWillCancelRef.current) setVoiceMode(false); // 非取消时回到文本模式
    setVoiceWillCancel(false);
  };

  // 按钮松手：hold → 停；300ms 内松手 → tap 开始
  const onVoiceBtnUp = () => {
    if (voiceHoldingRef.current) {
      finishRecording(); // hold 模式松手停
      return;
    }
    if (longPressTimerRef.current) {
      clearTimeout(longPressTimerRef.current);
      longPressTimerRef.current = null;
      if (!recognitionRef.current) {
        voiceInteractionModeRef.current = 'tap';
        startRecognition();
        startAudioMonitor();
      }
    }
  };

  // 键盘按钮：退出语音模式 + 停止任何进行中的录音/计时
  const exitVoiceMode = () => {
    if (longPressTimerRef.current) { clearTimeout(longPressTimerRef.current); longPressTimerRef.current = null; }
    voiceHoldingRef.current = false;
    voiceInteractionModeRef.current = null;
    stopRecognition();
    stopAudioMonitor();
    setIsRecording(false);
    setVoiceWillCancel(false);
    setVoiceMode(false);
  };

  /** 清空待发送附件（释放图片预览 objectURL） */
  const clearPendingFiles = () => {
    setPendingItems((prev) => {
      prev.forEach((p) => { if (p.url) URL.revokeObjectURL(p.url); });
      return [];
    });
  };
  /** 移除单个待发送附件 */
  const removePendingFile = (idx: number) => {
    setPendingItems((prev) => {
      const item = prev[idx];
      if (item?.url) URL.revokeObjectURL(item.url);
      return prev.filter((_, i) => i !== idx);
    });
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    // 必须先 Array.from 快照：e.target.files 是活的 FileList，e.target.value='' 清空后 FileList 也变空
    // （讨论区 handleSelectFile 同理：先 Array.from 再清 value）
    const fileArr = Array.from(e.target.files || []);
    e.target.value = '';
    if (fileArr.length === 0) return;
    // 逐个校验大小 + 图片压缩，收集通过的新文件（file 与预览 url 绑定为单一对象）
    const accepted: Array<{ file: File; url?: string }> = [];
    for (const file of fileArr) {
      if (file.size > MAX_FILE_SIZE) {
        Toast({ message: '文件大小超过100M，请重新上传', theme: 'error' });
        continue;
      }
      let finalFile = file;
      if (file.type.startsWith('image/')) {
        const r = await compressImage(file);
        finalFile = r.file;
        if (r.compressed) {
          const beforeMb = (r.originalSize / 1024 / 1024).toFixed(1);
          const afterMb = (r.resultSize / 1024 / 1024).toFixed(1);
          Toast({ message: `「${file.name}」已压缩 ${beforeMb}MB→${afterMb}MB`, theme: 'success' });
        }
      }
      accepted.push({
        file: finalFile,
        url: finalFile.type.startsWith('image/') ? URL.createObjectURL(finalFile) : undefined,
      });
    }
    if (accepted.length === 0) return;
    // 追加到已有待发送列表（支持多次选择累积）
    setPendingItems((prev) => [...prev, ...accepted]);
  };

  /** PC 端粘贴图片：从剪贴板取 image/* 文件，走与「选择文件」一致的待发送附件流程（预览后可随消息上传） */
  const handlePaste = async (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== 'file' || !item.type.startsWith('image/')) continue;
      const file = item.getAsFile();
      if (!file) continue;
      e.preventDefault(); // 阻止图片被当作 base64/文本塞进输入框
      if (file.size > MAX_FILE_SIZE) {
        Toast({ message: '文件大小超过100M，请重新上传', theme: 'error' });
        return;
      }
      // 粘贴图同样压缩；file 与预览 url 绑定为单一对象，避免错位
      const r = await compressImage(file);
      const finalFile = r.file;
      setPendingItems((prev) => [...prev, { file: finalFile, url: URL.createObjectURL(finalFile) }]);
      Toast({ message: '已粘贴图片，可直接发送', theme: 'success' });
      return; // 只处理第一张图片
    }
  };

  /** 转工单（二次确认）：prepare 生成草稿 → 弹窗核对/补字段 → confirm 入库 */
  const handleSubmitTicket = async () => {
    if (submittingTicket || ticketConfirm.submitting) return;
    if (messages.length === 0) { Toast({ message: '请先发送一条消息描述问题', theme: 'warning' }); return; }
    if (!sessionId) { Toast({ message: '会话未就绪，请先发送一条消息', theme: 'warning' }); return; }
    setSubmittingTicket(true);
    try {
      const res = await qaPrepareTicket(sessionId);
      // prepare 两层 code 规范：
      //   外层 code=1 → pipeline 抛异常，message 为异常信息；
      //   外层 code=0 → 正常返回，再按内层 data.code / stage 分流：
      //     ① data.code=1 + stage=not_ready → 信息不足，对话区追问；
      //     ② data.code=1 + 无 stage → 重复提单（_can_submit 拦截），友好提示；
      //     ③ stage=draft_ready / need_fields → 草稿就绪/缺字段，弹确认窗。
      if (res?.code !== 0) {
        Toast({ message: res?.message || '生成工单草稿失败', theme: 'error' });
        return;
      }
      if (!res.data) {
        Toast({ message: '生成工单草稿失败', theme: 'error' });
        return;
      }
      // ① 信息不足：stage=not_ready → 对话区列出缺失项引导补充，不弹窗
      if (res.data.stage === 'not_ready') {
        const missing = res.data.missing_info ?? [];
        const msg = res.data.message || (missing.length
          ? `工单信息不足，还差：${missing.join('、')}。在对话中补充后会自动为您生成工单。`
          : '工单信息不足，在对话中补充后会自动为您生成工单。');
        setMessages((prev) => [...prev, {
          id: uid(),
          role: 'assistant',
          subtype: 'missing_hint',
          content: msg,
          timestamp: new Date().toISOString(),
        }]);
        scrollToBottomNow();
        if (convRef.current) appendMessage(convRef.current, 'assistant', msg).catch(() => {});
        setTicketMissing({ info: missing, message: msg });
        Toast({ message: missing.length ? `还差 ${missing.length} 项信息，已在对话中列出` : '信息不足，请补充', theme: 'warning' });
        return;
      }
      // ② 重复提单：data.code=1 + 无 stage（_can_submit 拦截）→ 友好提示，不弹窗
      if (res.data.code === 1) {
        const msg = res.data.message || '当前会话无需重复提交工单';
        setMessages((prev) => [...prev, {
          id: uid(),
          role: 'assistant',
          content: msg,
          timestamp: new Date().toISOString(),
        }]);
        scrollToBottomNow();
        if (convRef.current) appendMessage(convRef.current, 'assistant', msg).catch(() => {});
        Toast({ message: msg, theme: 'warning', duration: 4000 });
        return;
      }
      // ③ 草稿就绪 / 缺字段：stage=draft_ready | need_fields → 弹确认窗
      const { draft, missing_fields, prompt } = res.data;
      // 打开确认弹窗，让用户核对/编辑/补字段
      setTicketMissing(null); // 已就绪，清掉待补充清单
      ticketBaseTimeRef.current = dayjs(); // 提单基准时间：生成草稿并打开弹窗时固定
      setTicketConfirm({ visible: true, draft, overrides: {}, submitting: false, force_submit: false, dualTicket: false, projectOwner: null });
      if (missing_fields?.length) {
        // 缺失字段明细已在确认弹窗内逐字段展示，Toast 仅作短提示（避免长 prompt 被截断/喧宾夺主）
        Toast({ message: '请补全必填字段后提交', theme: 'warning', duration: 3000 });
      }
    } catch (err) {
      Toast({ message: `生成草稿失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingTicket(false);
    }
  };

  /** 弹窗字段读写 helper：优先取用户编辑值，回退草稿原值 */
  const draftField = (k: keyof TicketDraft): string =>
    String(ticketConfirm.overrides[k] ?? ticketConfirm.draft?.[k] ?? '');
  const setDraftField = (k: keyof TicketDraft, v: string) =>
    setTicketConfirm((s) => ({ ...s, overrides: { ...s.overrides, [k]: v } }));

  // ── 最晚解决时间（截止时间）：antd DatePicker 下拉，与编辑弹窗统一（浮层 z-index 见下方 JSX）──
  // 区间基准 = 提单基准时间（ticketBaseTimeRef），优先级决定时长（紧急24h/高72h/中120h/低336h）。
  const deadlineRange = getDeadlineRange(draftField('priority'), ticketBaseTimeRef.current);
  // 用户是否手动动过 deadline（清空 or 选择）：未动过则默认显示区间最大值（提单时间 + 优先级时长）。
  const deadlineTouched = Object.prototype.hasOwnProperty.call(ticketConfirm.overrides, 'deadline_at');
  const deadlinePickerValue = (() => {
    const raw = draftField('deadline_at');
    if (raw) return dayjs(raw);
    if (deadlineTouched) return null; // 用户主动清空，保持空
    return deadlineRange?.max ?? null; // 未设置 → 默认显示最大值
  })();

  // ── 工单概览气泡 + 派单轮询 ──────────────────────────────────
  const tasksReq = useMemo(() => createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务'), []);
  const pollTimeoutsRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const pollingRef = useRef<Set<string>>(new Set()); // 正在轮询的 msgId（含 await 期间，防恢复 useEffect 重复启动）
  const cancelledRef = useRef(false); // 组件卸载标记（防卸载后 setMessages 产生 React warning）

  const pollDispatch = useCallback(async (msgId: string, dbId: number, ov: NonNullable<Message['ticket_overview']>, attempt: number) => {
    if (attempt >= 12) { pollingRef.current.delete(msgId); return; } // 60s 超时（5s × 12）
    try {
      // 必须 skipCache：createRequest 的 GET 默认缓存 5 分钟，否则第二次轮询起命中缓存返回旧 assigned_to，
      // 控制台看不到请求、气泡永远显示"派单中"（只有刷新清空模块级 requestCache 后才真正请求）。
      const task = await tasksReq<{ assigned_to?: string; assigned_to_name?: string }>(`/${dbId}`, { skipCache: true });
      if (task.assigned_to) {
        const assignedName = task.assigned_to_name || task.assigned_to;
        const newOv = { ...ov, assigned_to_name: assignedName };
        if (!cancelledRef.current) {
          setMessages((prev) => prev.map((m) =>
            m.id === msgId && m.ticket_overview
              ? { ...m, ticket_overview: newOv }
              : m
          ));
        }
        // 回写 DB：派单状态持久化。切换/刷新/历史会话切走后从 DB 读到即显示"已派单"，
        // 不再依赖内存轮询跨切换存活（此前状态只在内存，切换后丢失→气泡停在"派单中"）。
        // 回写句柄 = 气泡 id：confirm 用 String(appendMessage 返回的 DB id)，恢复用 String(m.id)，均为 DB message id。
        const dbMsgId = Number(msgId);
        if (Number.isFinite(dbMsgId) && dbMsgId > 0) {
          updateMessageContent(dbMsgId, JSON.stringify(newOv)).catch(() => {});
        }
        pollingRef.current.delete(msgId);
        return; // 已派单，停止
      }
    } catch { /* 单次失败继续 */ }
    pollTimeoutsRef.current[msgId] = setTimeout(() => {
      delete pollTimeoutsRef.current[msgId]; // timeout 触发后清除 id，递归由 pollingRef 防重
      pollDispatch(msgId, dbId, ov, attempt + 1);
    }, 5000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tasksReq]);

  const startDispatchPoll = useCallback((msgId: string, dbId: number, ov: NonNullable<Message['ticket_overview']>) => {
    if (pollingRef.current.has(msgId)) return; // 已在轮询（含 await 期间）
    pollingRef.current.add(msgId);
    pollDispatch(msgId, dbId, ov, 0);
  }, [pollDispatch]);

  // 卸载清理所有轮询 + 中断流式（避免组件卸载后后台 setMessages 报错/串台）
  useEffect(() => () => {
    cancelledRef.current = true;
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    Object.values(pollTimeoutsRef.current).forEach(clearTimeout);
    pollTimeoutsRef.current = {};
    pollingRef.current.clear();
  }, []);

  // 切会话清理轮询
  useEffect(() => {
    Object.values(pollTimeoutsRef.current).forEach(clearTimeout);
    pollTimeoutsRef.current = {};
    pollingRef.current.clear();
  }, [conversationId]);

  // 恢复后：对派单中的 ticket_overview 气泡启动轮询（传完整 ov，供查到后回写 DB）
  useEffect(() => {
    messages.forEach((m) => {
      if (m.subtype === 'ticket_overview' && m.ticket_overview && !m.ticket_overview.assigned_to_name && m.ticket_overview.db_id) {
        startDispatchPoll(m.id, m.ticket_overview.db_id, m.ticket_overview);
      }
    });
  }, [messages, startDispatchPoll]);

  /** 确认提交：校验项目/项目负责人 → 工单1 confirm_submit + 工单2(双工单) createTicket → 两个概览气泡 */
  // 取消确认（关闭弹窗/放弃提单）：彻底清空本地草稿，并通知后端清除 ticket_draft。
  // 关键：不清后端则 review 幂等分支（pipeline.py existing_draft 已存在）不再发 review 事件，
  // 前端确认弹窗无法再次弹出，提单卡死。清掉后下次对话字段齐全会重新弹窗。
  const handleCancelTicketConfirm = () => {
    const sid = ticketConfirm.draft?.source_conversation_id ?? sessionId;
    ticketBaseTimeRef.current = null; // 关闭弹窗即清空基准，下次打开重新固定
    setTicketConfirm({ visible: false, draft: null, overrides: {}, submitting: false, force_submit: false, dualTicket: false, projectOwner: null });
    if (sid) {
      qaClearDraft(String(sid)).catch(() => { /* 清草稿失败不阻塞，本地已重置 */ });
    }
  };

  const handleConfirmTicket = async () => {
    const draft = ticketConfirm.draft;
    if (!draft || !sessionId) return;
    const projectIdVal = draftField('project_id').trim();
    const projectNameVal = draftField('project').trim();
    const isDual = ticketConfirm.dualTicket;

    // 校验：非双工单要求 project_id；双工单要求项目负责人
    if (!isDual && !projectIdVal) {
      Toast({
        message: projectNameVal
          ? `项目「${projectNameVal}」不在你的名下，请在下拉中选择一个你所属的项目；或勾选「项目不在项目集中」走兜底`
          : '请先选择绑定项目',
        theme: 'warning',
        duration: 4000,
      });
      return;
    }
    if (isDual && !ticketConfirm.projectOwner) {
      Toast({ message: '请选择项目负责人', theme: 'warning' });
      return;
    }

    setTicketConfirm((s) => ({ ...s, submitting: true }));
    try {
      // ── 工单1（正常工单）：confirm_submit。双工单模式强制 project=摇人吧服务号提单（兜底） ──
      const overrides: Partial<TicketDraft> = {
        ...ticketConfirm.overrides,
        project: isDual ? '摇人吧服务号提单' : draftField('project'),
        project_id: isDual ? '' : projectIdVal,
      };
      // deadline 兜底：用户未手动设置时，用区间最大值（提单时间 + 优先级时长）作为默认最晚解决时间，
      // 确保 DatePicker 显示值与提交值一致——否则未触碰 deadline 直接提交时，工单1/工单2 均不落库 deadline。
      // 已手动清空（overrides.deadline_at=''）不兜底，尊重用户主动置空。
      if (!Object.prototype.hasOwnProperty.call(overrides, 'deadline_at') && deadlineRange) {
        overrides.deadline_at = deadlineRange.max.toISOString();
      }
      const res = await qaConfirmTicket(sessionId, overrides);
      if (res?.code !== 0) {
        Toast({ message: res?.message || '提交工单失败', theme: 'error' });
        return;
      }

      // 工单1 概览气泡数据：以 confirm_submit 返回的 ticket（实际入库的那份）为准。
      // 之前用本地 draft（弹窗时的第一版 LLM 草稿）——confirm_submit 内部会重新
      // _build_ticket 生成第二版，两次 LLM 调用有随机性，导致卡片标题/描述与
      // 历史工单页（从 DB 读）不一致。
      const ticket = res.data?.ticket as Record<string, unknown> | undefined;
      const ov1: NonNullable<Message['ticket_overview']> = {
        db_id: (res.data?.db_id as number) ?? 0,
        ticket_id: (ticket?.ticket_id as string) || draft.ticket_id || '',
        title: (ticket?.title as string) || draftField('title') || draft.title || '工单',
        type: (ticket?.type as string) || draftField('type') || draft.type,
        priority: (ticket?.priority as string) || draftField('priority') || draft.priority,
        project: (ticket?.project as string) || overrides.project || '摇人吧服务号提单',
        description: (ticket?.description as string) || draftField('description') || draft.description,
        created_at: new Date().toISOString(),
      };

      // ── 工单2（申请单，仅双工单）：POST /api/tasks/，直接指定 assigned_to=项目负责人 ──
      let ov2: NonNullable<Message['ticket_overview']> | null = null;
      if (isDual && ticketConfirm.projectOwner) {
        const owner = ticketConfirm.projectOwner;
        const expectedProj = draftField('project') || projectNameVal || '（未填写）';
        // 工单2（申请单）描述与工单1（AI 派单）保持一致：复用用户在确认窗看到/编辑的 AI 总结描述，
        // 项目负责人既能看到申请目的，又能看到用户实际问题（车型/故障/场景等），避免空泛固定模板。
        const issueDesc = (draftField('description') || draft.description || '（无问题描述）').trim();
        try {
          const created = await createTicket({
            title: '【项目申请】请求新建项目/添加用户',
            description: `用户提单时项目「${expectedProj}」不在系统项目集中，请处理：1）新建项目；2）将用户加入对应项目。\n\n【用户问题描述】\n${issueDesc}`,
            ticket_type: 'support',
            priority: 'medium',
            project_name: '摇人吧服务号提单',
            project_id: projectIdVal || '',
            assigned_to: owner.username,
            deadline_at: overrides.deadline_at || undefined,
          });
          ov2 = {
            db_id: created.id,
            ticket_id: '',
            title: '【项目申请】请求新建项目/添加用户',
            type: 'support',
            priority: '中',
            project: '摇人吧服务号提单',
            description: `已向项目负责人「${owner.name || owner.username}」发送申请工单`,
            created_at: new Date().toISOString(),
          };
        } catch (err) {
          Toast({ message: `申请工单创建失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      }

      ticketBaseTimeRef.current = null; // 提交完成关闭弹窗，清空基准
      setTicketConfirm({ visible: false, draft: null, overrides: {}, submitting: false, force_submit: false, dualTicket: false, projectOwner: null });
      resumeFollowBottom(); // 用户主动提交：工单概览气泡追加后立即贴底展示

      // 工单1 落库 + 气泡 + 轮询
      let dbMsgId1: number | null = null;
      if (convRef.current) {
        try {
          const saved = await appendMessage(convRef.current, 'assistant', JSON.stringify(ov1), {
            messageType: 'text',
            metadata: JSON.stringify({ kind: 'ticket_overview' }),
          });
          dbMsgId1 = saved.id;
        } catch { /* 落库失败也插入气泡（临时 id），仅不回写 DB */ }
      }
      const ovMsg1: Message = {
        id: dbMsgId1 != null ? String(dbMsgId1) : uid(),
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        subtype: 'ticket_overview',
        ticket_overview: ov1,
      };
      setMessages((prev) => [...prev, ovMsg1]);
      if (ov1.db_id) startDispatchPoll(ovMsg1.id, ov1.db_id, ov1);

      // 工单2 落库 + 气泡 + 轮询（与工单1同处理，刷新/切会话后可恢复）
      if (ov2) {
        let dbMsgId2: number | null = null;
        if (convRef.current) {
          try {
            const saved2 = await appendMessage(convRef.current, 'assistant', JSON.stringify(ov2), {
              messageType: 'text',
              metadata: JSON.stringify({ kind: 'ticket_overview' }),
            });
            dbMsgId2 = saved2.id;
          } catch { /* ignore */ }
        }
        const ovMsg2: Message = {
          id: dbMsgId2 != null ? String(dbMsgId2) : uid(),
          role: 'assistant',
          content: '',
          timestamp: new Date().toISOString(),
          subtype: 'ticket_overview',
          ticket_overview: ov2,
        };
        setMessages((prev) => [...prev, ovMsg2]);
        if (ov2.db_id) startDispatchPoll(ovMsg2.id, ov2.db_id, ov2);
      }

      refreshTasks(); // 刷新「历史工单」待派单列表
      Toast({ message: isDual ? '双工单已生成' : '工单已生成', theme: 'success' });
    } catch (err) {
      Toast({ message: `提交工单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setTicketConfirm((s) => ({ ...s, submitting: false }));
    }
  };

  const toggleReaction = useCallback((id: string, type: 'like' | 'dislike') => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, reaction: m.reaction === type ? null : type } : m)));
  }, []);

  const copyContent = useCallback((content: string) => {
    // Clipboard API（安全上下文可用），否则降级 execCommand
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(content).then(
        () => Toast({ message: '已复制', theme: 'success' }),
        () => fallbackCopy(content),
      );
      return;
    }
    fallbackCopy(content);
  }, []);

  const fallbackCopy = (content: string) => {
    const ta = document.createElement('textarea');
    ta.value = content;
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

  // 「猜你想问」：仅首次新建会话（无消息）且输入为空 → 随机 3 条（可换一批）；有输入 → 基于防抖关键词检索（最多 3 条）
  const suggestedList: string[] = debouncedKeyword
    ? matchQuestions(debouncedKeyword, 3)
    : (messages.length === 0 ? randomPool : []);
  const showSuggestedRefresh = !debouncedKeyword;

  return (
    <div className={`chat-panel${compact ? ' is-compact' : ''}`}>

      <div className="chat-view__messages" ref={messagesContainerRef}>
        {messages.length === 0 && (
          <div className="chat-view__empty">
            {!isCall && <div className="chat-view__empty-emoji">{cfg.emptyEmoji}</div>}
            {!isCall && <p>{cfg.emptyTitle}</p>}
            <p className="chat-view__empty-sub">
              {isCall ? `你好${name || username ? `，${name || username}` : ''}，请描述你的问题，U老师先帮你初步诊断。` : '关于系统任务的问题，可以随时问我。'}
            </p>
          </div>
        )}

        {messages
          // 渲染层兜底：空白 AI 气泡（空内容/纯空白，且非流式占位、无附件、非工单概览）不渲染
          .filter((m) => m.role !== 'assistant' || !!m.streaming || m.content.trim().length > 0 || !!m.imageUrl || !!m.attachment || m.subtype === 'ticket_overview')
          .map((msg) => (
          <MessageBubble
            key={msg.id}
            msg={msg}
            editingId={editingId}
            compact={compact}
            onToggleReaction={toggleReaction}
            onCopy={copyContent}
            onEditStart={setEditingId}
            onEditChange={handleEditChange}
            onEditSave={handleEditSave}
            onEditCancel={handleEditCancel}
            onImageClick={setPreviewUrl}
            onOpenTicket={handleOpenTicket}
            expandedDesc={expandedMsgIds.has(msg.id)}
            onToggleDesc={toggleMsgExpanded}
          />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 「猜你想问」：文档流内嵌于消息区与输入栏之间（不遮挡对话内容） */}
      {suggestedList.length > 0 && (
        <SuggestedQuestions
          questions={suggestedList}
          onPick={(q) => send(q)}
          onRefresh={showSuggestedRefresh ? () => setRandomPool(pickRandomQuestions(3)) : undefined}
        />
      )}

      {/* 转工单信息不足引导卡片（方案A）：常驻输入区上方，列出缺失项，点按钮重试 prepare */}
      {isCall && ticketMissing && ticketMissing.info.length > 0 && (
        <div className="chat-ticket-missing" role="status">
          <div className="chat-ticket-missing__title">
            <span className="chat-ticket-missing__badge">缺 {ticketMissing.info.length} 项</span>
            <span>转工单前请补全以下信息</span>
            <button
              type="button"
              className="chat-ticket-missing__close"
              onClick={() => setTicketMissing(null)}
              aria-label="关闭"
            >✕</button>
          </div>
          <ul className="chat-ticket-missing__list">
            {ticketMissing.info.map((item) => (
              <li key={item} className="chat-ticket-missing__item">{item}</li>
            ))}
          </ul>
          <button
            type="button"
            className="chat-ticket-missing__retry"
            onClick={handleSubmitTicket}
            disabled={submittingTicket}
          >
            {submittingTicket ? '检查中…' : '重新检测转工单'}
          </button>
        </div>
      )}

      {/* 转工单悬浮球（设计稿 FloatingTicketButton：52px 液态玻璃 + 可拖拽自由定位，
          拖拽超过 8px 视为移动、否则触发点击；位置限制在窗口内，底部避开导航） */}
      {isCall && (
        <div
          className="chat-panel__ticket-fab"
          title="转为工单"
          style={fabPos ? { left: fabPos.x, top: fabPos.y, right: 'auto', bottom: 'auto' } : undefined}
        >
          <button
            ref={fabRef}
            className={`chat-ticket-btn${messages.length > 0 ? ' has-content' : ''}${submittingTicket ? ' is-submitting' : ''}${ticketMissing && ticketMissing.info.length ? ' has-missing' : ''}`}
            onPointerDown={onFabPointerDown}
            onPointerMove={onFabPointerMove}
            onPointerUp={onFabPointerUp}
            onPointerCancel={onFabPointerUp}
            onClick={() => {
              // 拖拽结束时抑制本次 click（避免拖动误触发提交）
              if (fabDragRef.current.justDragged) { fabDragRef.current.justDragged = false; return; }
              handleSubmitTicket();
            }}
            disabled={submittingTicket}
            aria-label="转工单"
          >
            {submittingTicket ? (
              <span className="chat-ticket-spinner" />
            ) : (
              <TicketPlus size={20} strokeWidth={2} />
            )}
            {ticketMissing && ticketMissing.info.length > 0 && (
              <span className="chat-ticket-btn__badge">{ticketMissing.info.length}</span>
            )}
          </button>
          <span className="chat-ticket-btn__label">{submittingTicket ? '提交中…' : '转工单'}</span>
        </div>
      )}

      {/* 输入区（设计稿单行横排：上传 + 输入框 + 发送 + 新建会话） */}
      <div
        className="chat-input-bar"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
        }}
      >
        {pendingItems.length > 0 && (
          <div className="chat-pending-files">
            {pendingItems.map((p, i) => (
              <div className="chat-pending-file" key={`${p.file.name}-${i}`}>
                {p.url ? (
                  <img src={p.url} alt="附件预览" className="chat-pending-file__thumb" />
                ) : (
                  <Paperclip className="chat-pending-file__icon" size={20} strokeWidth={1.8} />
                )}
                <span className="chat-pending-file__name">{p.file.name}</span>
                <button type="button" className="chat-pending-file__remove" onClick={() => removePendingFile(i)} aria-label="移除附件">✕</button>
              </div>
            ))}
            {pendingItems.length > 3 && (
              <div className="chat-pending-files__count">共 {pendingItems.length} 个附件</div>
            )}
          </div>
        )}
        {voiceMode ? (
          <div className="chat-input-bar__voice-row">
            <button className="chat-input-btn" onClick={exitVoiceMode} title="键盘输入" aria-label="键盘输入">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2" y="5" width="20" height="14" rx="2" />
                <line x1="6" y1="9" x2="6" y2="9" strokeWidth="2" strokeLinecap="round" />
                <line x1="10" y1="9" x2="14" y2="9" />
                <line x1="6" y1="13" x2="10" y2="13" />
                <line x1="12" y1="13" x2="16" y2="13" />
                <line x1="6" y1="17" x2="12" y2="17" />
              </svg>
            </button>
            <button
              ref={voiceBtnRef}
              className={`chat-voice-hold-btn${isRecording ? ' is-recording' : ''}${voiceWillCancel ? ' is-cancelling' : ''}`}
              onPointerDown={onVoiceBtnDown}
              onPointerUp={onVoiceBtnUp}
            >
              {isRecording ? (
                voiceWillCancel ? '松开 取消' : (
                  <span className="voice-wave-dots">
                    {voiceLevels.map((lv, i) => (
                      <span key={i} className="voice-dot" style={{ height: `${5 + lv * 15}px`, opacity: 0.4 + lv * 0.6 }} />
                    ))}
                  </span>
                )
              ) : '轻触或按住 说话'}
            </button>
            <button className="chat-input-btn" onClick={() => setShowUploadMenu(true)} title="上传" aria-label="上传文件或拍照">
              <Plus size={22} strokeWidth={2} />
            </button>
          </div>
        ) : (
          /* 设计稿单行横排输入区：[+ 上传] [输入框] [↑ 发送] [💬 新建会话]，底部对齐 */
          <div className="chat-input-bar__row">
            <button className="chat-input-btn chat-input-btn--secondary" onClick={() => setShowUploadMenu(true)} title="上传" aria-label="上传文件或拍照">
              <Plus size={16} strokeWidth={2} />
            </button>
            <div ref={textareaContainerRef} className="chat-input-bar__textarea" onPaste={handlePaste}>
              <Textarea
                value={input}
                onChange={(v) => setInput(String(v))}
                placeholder="发消息…"
                autosize={{ minRows: 1, maxRows: 6 }}
              />
              {textareaMaxed && !textareaFullscreen && (
                <button
                  type="button"
                  className="chat-input-bar__expand-btn"
                  onClick={() => setTextareaFullscreen(true)}
                  aria-label="全屏输入"
                >
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="15 3 21 3 21 9" />
                    <polyline points="9 21 3 21 3 15" />
                    <line x1="21" y1="3" x2="14" y2="10" />
                    <line x1="3" y1="21" x2="10" y2="14" />
                  </svg>
                </button>
              )}
            </div>
            <button type="button" className="chat-send-btn" onClick={() => send(input)} disabled={(!input.trim() && pendingItems.length === 0) || loading} aria-label="发送">
              {loading ? (
                <span className="chat-send-btn__spinner" />
              ) : (
                <ArrowUp size={16} strokeWidth={2.4} />
              )}
            </button>
            <button className="chat-input-btn chat-input-btn--blue" onClick={requestNewConversation} title="新建会话" aria-label="新建会话">
              <MessageSquarePlus size={16} strokeWidth={2} />
            </button>
          </div>
        )}
        <input ref={cameraInputRef} type="file" accept="image/*" capture="environment" onChange={handleFileChange} style={{ display: 'none' }} />
        <input ref={albumInputRef} type="file" accept="image/*" multiple onChange={handleFileChange} style={{ display: 'none' }} />
        <input ref={fileInputRef} type="file" accept="*/*" multiple onChange={handleFileChange} style={{ display: 'none' }} />
        {textareaFullscreen && (
          <div className="chat-input-bar__fullscreen-overlay" onClick={() => setTextareaFullscreen(false)}>
            <div className="chat-input-bar__fullscreen-panel" onClick={(e) => e.stopPropagation()}>
              <div className="chat-input-bar__fullscreen-header">
                <button
                  type="button"
                  className="chat-input-bar__clear-btn"
                  onClick={() => { setInput(''); }}
                >
                  清空
                </button>
                <button
                  type="button"
                  className="chat-input-bar__collapse-btn"
                  onClick={() => setTextareaFullscreen(false)}
                  aria-label="收起"
                >
                  <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="4 8 12 16 20 8" />
                  </svg>
                </button>
              </div>
              <textarea
                className="chat-input-bar__fullscreen-textarea"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onPaste={handlePaste}
                placeholder="发消息..."
                autoFocus
              />
              <div className="chat-input-bar__fullscreen-footer">
                <button type="button" className="chat-send-btn" onClick={() => { send(input); setTextareaFullscreen(false); }} disabled={(!input.trim() && pendingItems.length === 0) || loading} aria-label="发送">
                  {loading ? (
                    <span className="chat-send-btn__spinner" />
                  ) : (
                    <ArrowUp size={18} strokeWidth={2.4} />
                  )}
                </button>
              </div>
            </div>
          </div>
        )}
        <Popup visible={showUploadMenu} onClose={() => setShowUploadMenu(false)} placement="bottom" showOverlay>
          <div className="upload-menu">
            <button type="button" className="upload-menu__item" onClick={() => { setShowUploadMenu(false); cameraInputRef.current?.click(); }}>拍摄</button>
            <button type="button" className="upload-menu__item" onClick={() => { setShowUploadMenu(false); albumInputRef.current?.click(); }}>从相册选择</button>
            <button type="button" className="upload-menu__item" onClick={() => { setShowUploadMenu(false); fileInputRef.current?.click(); }}>上传文件</button>
            <button type="button" className="upload-menu__cancel" onClick={() => setShowUploadMenu(false)}>取消</button>
          </div>
        </Popup>

        {/* 转工单二次确认弹窗：核对草稿字段，problem 类型必填 project */}
        <Popup visible={ticketConfirm.visible} onClose={handleCancelTicketConfirm} placement="bottom" showOverlay closeOnOverlayClick={false}>
          <div className="ticket-confirm">
            <h4 className="ticket-confirm__title">确认工单信息</h4>
            {ticketConfirm.draft && (
              <div className="ticket-confirm__body">
                <div className="ticket-confirm__tags">
                  {ticketConfirm.draft.type && <Tag theme="primary">{TICKET_TYPE_LABEL[ticketConfirm.draft.type] || ticketConfirm.draft.type}</Tag>}
                  {ticketConfirm.draft.priority && <Tag theme="warning">{ticketConfirm.draft.priority}</Tag>}
                </div>
                {ticketConfirm.force_submit && (
                  <div className="ticket-confirm__banner">⚠️ 信息收集超限，请重点核对项目、车型等关键字段</div>
                )}
                <label className="ticket-confirm__label">标题</label>
                <input
                  className="ticket-confirm__input"
                  value={draftField('title')}
                  onChange={(e) => setDraftField('title', e.target.value)}
                  placeholder="工单标题"
                />
                <label className="ticket-confirm__label">描述</label>
                <textarea
                  className="ticket-confirm__textarea"
                  value={draftField('description')}
                  onChange={(e) => setDraftField('description', e.target.value)}
                  placeholder="问题描述"
                  rows={3}
                />
                <label className="ticket-confirm__label">优先级</label>
                <select
                  className="ticket-confirm__select"
                  value={draftField('priority')}
                  onChange={(e) => {
                    const p = e.target.value;
                    setDraftField('priority', p);
                    // 切换优先级：最晚解决时间重算为「提单时间 + 新优先级时长」最大值
                    const r = getDeadlineRange(p, ticketBaseTimeRef.current);
                    if (r) setDraftField('deadline_at', r.max.toISOString());
                  }}
                >
                  <option value="紧急">紧急</option>
                  <option value="高">高</option>
                  <option value="中">中</option>
                  <option value="低">低</option>
                </select>
                {/* 最晚解决时间：antd DatePicker 下拉（与编辑弹窗统一），浮层 z-index 高于弹窗避免被遮挡 */}
                <label className="ticket-confirm__label">最晚解决时间</label>
                <DatePicker
                  style={{ width: '100%' }}
                  placeholder="点击选择"
                  format="YYYY-MM-DD HH:00"
                  showTime={{ defaultValue: deadlineRange?.max ?? dayjs().hour(9).minute(0), format: 'HH:00' }}
                  value={deadlinePickerValue}
                  disabledDate={deadlineRange ? makeDisabledDate(deadlineRange.min, deadlineRange.max) : undefined}
                  disabledTime={deadlineRange ? makeDisabledTime(deadlineRange.min, deadlineRange.max) : undefined}
                  onChange={(d: dayjs.Dayjs | null) => setDraftField('deadline_at', d ? d.minute(0).second(0).millisecond(0).toISOString() : '')}
                  allowClear
                  styles={{ popup: { root: { zIndex: 12000 } } }}
                />
                <label className="ticket-confirm__label">绑定项目 {!ticketConfirm.dualTicket && <span style={{ color: '#e34d59' }}>*</span>}</label>
                <ProjectSelect
                  value={draftField('project_id') || null}
                  nameHint={draftField('project') || null}
                  onChange={(p) => {
                    setDraftField('project', p.name);
                    setDraftField('project_id', p.project_code);
                  }}
                />
                {!draftField('project_id').trim() && !ticketConfirm.dualTicket && (
                  <span className="ticket-confirm__hint">项目为必选项，未绑定项目无法提交</span>
                )}
                {/* 兜底双工单：项目不在项目集时勾选，生成申请单派给项目负责人 */}
                <label className="ticket-confirm__checkbox">
                  <input
                    type="checkbox"
                    checked={ticketConfirm.dualTicket}
                    onChange={(e) => setTicketConfirm((s) => ({ ...s, dualTicket: e.target.checked, projectOwner: e.target.checked ? s.projectOwner : null }))}
                  />
                  <span>我的项目不在所属项目集中，向项目负责人发送申请工单</span>
                </label>
                {ticketConfirm.dualTicket && (
                  <>
                    <div className="ticket-confirm__banner ticket-confirm__banner--info">
                      项目不在项目集中，将默认提单至「摇人吧服务号提单」项目，同时向项目负责人发送申请工单
                    </div>
                    <label className="ticket-confirm__label">项目负责人 <span style={{ color: '#e34d59' }}>*</span></label>
                    <UserSelect
                      value={ticketConfirm.projectOwner?.id ?? null}
                      onChange={(u) => setTicketConfirm((s) => ({ ...s, projectOwner: u }))}
                      placeholder="请选择项目负责人"
                      title="选择项目负责人"
                    />
                  </>
                )}
              </div>
            )}
            <div className="ticket-confirm__btns">
              <button
                type="button"
                className="ticket-confirm__btn ticket-confirm__btn--cancel"
                onClick={handleCancelTicketConfirm}
              >取消</button>
              <button
                type="button"
                className="ticket-confirm__btn ticket-confirm__btn--confirm"
                onClick={handleConfirmTicket}
                disabled={ticketConfirm.submitting}
              >{ticketConfirm.submitting ? '提交中…' : '确认提交'}</button>
            </div>
          </div>
        </Popup>

        {/* 图片预览：点击用户气泡图片放大查看 + 复制/下载 */}
        <ImageLightbox
          src={previewUrl || ''}
          alt="预览"
          open={!!previewUrl}
          onClose={() => setPreviewUrl(null)}
        />
      </div>
    </div>
  );
}
