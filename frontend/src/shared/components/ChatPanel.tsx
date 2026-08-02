// 可复用 AI 对话面板 — 提单 Agent（/api/ai/qa/ask/stream）
// 用于「我要摇人」页面：诊断+提单。系统任务页面不再使用 ChatPanel。
import { memo, useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Textarea, Toast, Popup, Tag } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import { useWorkbenchStore } from '@/stores/workbench';
import API_CONFIG from '@/config/api';
import { qaUpload, generateSessionId, trackSession, fetchWithAuth, qaPrepareTicket, qaConfirmTicket, type TicketDraft } from '@/api/ai';
import ProjectSelect from '@/shared/components/ProjectSelect';
import { createConversation, getConversation, appendMessage, readAiSessionId } from '@/api/conversation';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
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
  // 任务 Agent 专属：结构化方案草稿
  subtype?: 'solution_draft';
  solution_draft?: {
    _task_id?: string;
    root_cause_analysis: string;
    suggested_actions: string[];
    references: string[];
    confidence: number;
    needs_more_info: boolean;
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
 * 检出异常时 console.warn 抛出，便于排查后端边界问题。
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
      console.warn('[ChatPanel] 拦截未闭合 JSON 泄漏:', raw.slice(0, 80));
      return '';
    }
  }
  // 3) 剥游离残留前缀：} （LLM 多输出的闭合括号）
  const strippedBraces = t.replace(/^(?:\s*\}\s*)+/, '');
  // 剥孤立 fence 残留行：``` 单独出现（后无语言标记，非代码块）
  const result = strippedBraces.replace(/^```\s*\n?(?![a-zA-Z])/, '').trim();
  if (result !== t.trim()) console.warn('[ChatPanel] 剥离 JSON 残留:', t.slice(0, 40));
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
  full: { messages?: Array<{ id: number; role: string; content: string; created_at: string; file_urls?: string | null }> },
): Message[] =>
  (full.messages || [])
    .map((m) => {
      const msg: Message = {
        id: String(m.id),
        role: m.role as 'user' | 'assistant',
        content: m.role === 'assistant' ? sanitizeAiText(m.content) : m.content,
        timestamp: m.created_at,
      };
      if (m.file_urls) {
        try {
          const files = JSON.parse(m.file_urls) as Array<{ filename: string; object_path?: string; size?: number; isImage?: boolean }>;
          const f = files[0];
          if (f && f.object_path) {
            const url = attachmentUrl(f.object_path);
            if (f.isImage) msg.imageUrl = url;
            else msg.attachment = { name: f.filename, size: f.size ?? 0, url };
          }
        } catch { /* ignore */ }
      }
      return msg;
    })
    // 空白 AI 气泡（历史异常落库的空内容/纯空白）不恢复显示
    .filter((m) => m.role !== 'assistant' || m.content.trim().length > 0);

// 单条消息气泡（React.memo）：流式期间仅最后一条 content/streaming 变化，历史消息跳过整列表重渲染，消除抖动
const MessageBubble = memo(function MessageBubble({
  msg, editingId, compact, onToggleReaction, onCopy, onEditStart, onEditChange, onEditSave, onEditCancel, onImageClick,
}: {
  msg: Message;
  editingId: string | null;
  compact: boolean;
  onToggleReaction: (id: string, type: 'like' | 'dislike') => void;
  onCopy: (content: string) => void;
  onEditStart: (id: string) => void;
  onEditChange: (id: string, value: string) => void;
  onEditSave: (msg: Message) => void;
  onEditCancel: () => void;
  onImageClick: (url: string) => void;
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
            <span className="chat-bubble__file-icon">📎</span>
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
          msg.content ? (
            msg.streaming ? (
              <div className="chat-bubble__text" style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</div>
            ) : (
              <MarkdownRenderer content={msg.content} compact={compact} />
            )
          ) : (
            <div className="chat-bubble__typing" aria-label="AI 正在分析">
              <span /><span /><span />
            </div>
          )
        ) : (
          <div className="chat-bubble__text">{msg.content}</div>
        )}
      </div>

      <div className="chat-actions">
        {msg.role === 'assistant' && (
          <>
            <button className={`chat-action ${msg.reaction === 'like' ? 'is-active' : ''}`} onClick={() => onToggleReaction(msg.id, 'like')}>👍</button>
            <button className={`chat-action ${msg.reaction === 'dislike' ? 'is-active' : ''}`} onClick={() => onToggleReaction(msg.id, 'dislike')}>👎</button>
            <button className="chat-action" onClick={() => onCopy(msg.content)}>📋</button>
          </>
        )}
        {msg.role === 'user' && (
          <>
            <button className="chat-action" onClick={() => onCopy(msg.content)}>📋</button>
            {editingId === msg.id ? (
              <>
                <button className="chat-action" onClick={() => onEditSave(msg)}>✅</button>
                <button className="chat-action" onClick={onEditCancel}>✖️</button>
              </>
            ) : (
              <button className="chat-action" onClick={() => onEditStart(msg.id)}>✏️</button>
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
  const navigate = useNavigate();
  const { token, name, username } = useAuthStore();
  const { chatContext, consumeChatContext, refreshTasks, conversationId, setConversationId, setConversationTitle, renameConversation, refreshConversations } = useWorkbenchStore();
  const isCall = scene === 'call';
  const cfg = SCENE_CONFIG[scene];

  console.log('[ChatPanel] 用户信息: name="', name, '", username="', username, '", token=', !!token);

  const [messages, setMessages] = useState<Message[]>([]);
  // 图片预览：点击用户气泡图片 → 全屏遮罩放大查看
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [submittingTicket, setSubmittingTicket] = useState(false);
  // 转工单二次确认弹窗：prepare 生成草稿 → 用户核对/编辑/补字段 → confirm 入库
  const [ticketConfirm, setTicketConfirm] = useState<{
    visible: boolean;
    draft: TicketDraft | null;
    overrides: Partial<TicketDraft>;
    submitting: boolean;
  }>({ visible: false, draft: null, overrides: {}, submitting: false });
  // 转工单信息不足引导（方案A）：prepare 返回 not_ready 时，
  // 在输入框上方常驻「待补充清单」卡片 + 转工单按钮角标，引导用户回对话补全
  const [ticketMissing, setTicketMissing] = useState<{ info: string[]; message: string } | null>(null);
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
  // 文件上传大小上限：100MB
  const MAX_FILE_SIZE = 100 * 1024 * 1024;
  const [showUploadMenu, setShowUploadMenu] = useState(false);
  // 待发送附件：选中文件先挂起（不立即发送），用户可继续打字，发送时随 message 一起上传
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingImageUrl, setPendingImageUrl] = useState<string | null>(null);
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

  // 滚动跟随：仅在用户贴底时自动跟随；流式中瞬时置底（behavior:'auto'）避免 smooth 动画排队抖动
  const atBottomRef = useRef(true);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const prevCountRef = useRef(0); // 上一次消息条数：区分「新消息追加」与「内容增长」
  const scrollToBottom = useCallback(() => {
    if (!atBottomRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
  }, []);
  // 强制滚动到底部（无视 atBottomRef）：加载历史会话后调用，确保「进入即见最新消息」。
  const scrollToBottomNow = useCallback(() => {
    atBottomRef.current = true;
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    });
  }, []);
  useEffect(() => {
    // 新消息追加（条数增加：用户发送 / AI 占位气泡）→ 强制置底，让最新消息进入视野；
    // 仅内容增长（流式 token / 编辑）→ 仅贴底时跟随，不打扰上滑看历史的用户。
    const appended = messages.length > prevCountRef.current;
    prevCountRef.current = messages.length;
    if (appended) {
      atBottomRef.current = true;
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
    } else {
      scrollToBottom();
    }
  }, [messages, scrollToBottom]);
  // 监听用户滚动，判断是否贴底（上滑看历史时不强制拉回）
  useEffect(() => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const onScroll = () => {
      atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

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
        setMessages((prev) => (fresh.length > prev.length ? fresh : prev));
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
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
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

  /** 带附件发送：文件(可附文字)一起上传 /qa/upload，由后端返回 ack/ai_response（非流式）。
   * 方案一（乐观渲染）：点发送即插入用户气泡（附件+文字，内嵌上传进度遮罩）+ AI 分析占位气泡，
   * 上传进度实时更新到用户气泡，完成后遮罩消失、AI 回复填入占位气泡。文件名不拼进文字上下文。 */
  const sendWithFile = async (file: File, content: string) => {
    const isImage = file.type.startsWith('image/');
    const imageUrl = isImage ? URL.createObjectURL(file) : undefined;
    const attachment = isImage ? null : { name: file.name, size: file.size };
    const userId = uid();
    const assistantId = uid();
    // 乐观渲染：立即插入用户气泡（带进度遮罩）+ AI「正在分析」占位气泡
    setMessages((prev) => [
      ...prev,
      { id: userId, role: 'user', content, timestamp: new Date().toISOString(), imageUrl, attachment, uploading: true, percent: 0 },
      { id: assistantId, role: 'assistant', content: '', timestamp: new Date().toISOString() },
    ]);
    setInput('');
    clearPendingFile();
    setLoading(true);
    try {
      const sid = ensureSessionId();
      // 上传进度实时更新到用户气泡遮罩
      const res = await qaUpload(sid, [file], content, (p) =>
        setMessages((prev) => prev.map((m) => (m.id === userId ? { ...m, percent: p } : m))),
      );
      if (!res.ok) throw new Error(`上传失败: ${res.status}`);
      if (res.data?.code !== 0) throw new Error(res.data?.message || '上传失败');
      const data = res.data?.data;

      // 上传完成：回填代理 URL 到用户气泡（图片换掉临时 blob URL；非图片补 url），进度遮罩消失
      // 用后端代理路径 /api/call/files/{object_path} 而非预签名 URL（预签名 host=localhost 生产浏览器访问不了）
      const uploaded = data?.files?.[0];
      const fileUrl = uploaded?.object_path ? attachmentUrl(uploaded.object_path) : undefined;
      setMessages((prev) => prev.map((m) => {
        if (m.id !== userId) return m;
        const updated: Message = { ...m, uploading: false, percent: 100 };
        if (isImage && fileUrl) {
          if (m.imageUrl?.startsWith('blob:')) URL.revokeObjectURL(m.imageUrl);
          updated.imageUrl = fileUrl;
        } else if (!isImage && fileUrl && m.attachment) {
          updated.attachment = { ...m.attachment, url: fileUrl };
        }
        return updated;
      }));

      // 持久化用户消息（存 object_path，前端恢复时拼后端代理路径 /api/call/files/{object_path}）
      const convId = await ensureConversation(sid, content || `[发送了附件] ${file.name}`);
      if (convId) {
        const objectPath = uploaded?.object_path;
        const fileUrls = objectPath ? JSON.stringify([{ filename: file.name, object_path: objectPath, size: file.size, isImage }]) : undefined;
        appendMessage(convId, 'user', content || `[发送了附件] ${file.name}`, { fileUrls, messageType: isImage ? 'image' : 'file' }).catch(() => {});
      }

      // AI 回复填入占位气泡：文件+文字=完整诊断(ai_response.message)；只传文件=确认回执(ack_message)
      // 内容统一过清洗（防 JSON 泄漏/带 } 回复）；持久化用局部 convId 快照——发送期间用户
      // 可能已切换会话，convRef 已指向新会话，直接用会把本会话回复错写进新会话（过时/错位回复）。
      const aiResp = data?.ai_response;
      const assistantContent = sanitizeAiText((aiResp && aiResp.message) || data?.ack_message || '');
      if (assistantContent) {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: assistantContent } : m)));
        if (convId) appendMessage(convId, 'assistant', assistantContent).catch(() => {});
      } else {
        // 无回复：移除 AI 占位气泡，避免空气泡
        setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      }
      // 若本次顺带触发了提单，刷新待派单计数
      if (aiResp?.ticket) refreshTasks();
    } catch (err) {
      // 失败：用户气泡标记失败态（红色遮罩），移除 AI 占位气泡
      setMessages((prev) => prev.map((m) => (m.id === userId ? { ...m, uploading: false, failed: true } : m)));
      setMessages((prev) => prev.filter((m) => m.id !== assistantId));
      // 鉴权失效已由 kickToLogin 统一提示并跳转，此处不重复弹错误（与 send 一致）
      if (!isKickingToLogin()) {
        Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
      }
    } finally {
      setLoading(false);
    }
  };

  const send = async (text: string) => {
    const content = text.trim();
    const file = pendingFile;
    if (!content && !file) return;
    if (!token) { kickToLogin('请先登录'); return; }
    if (sendingRef.current) return; // 防双发
    sendingRef.current = true;
    // 用户开始补充信息：清掉待补充清单卡片（新一轮对话后再 prepare 会重新给出最新缺口）
    setTicketMissing(null);

    // 带附件：走 /qa/upload（非流式），由后端返回 ack/ai_response
    if (file) {
      try {
        await sendWithFile(file, content);
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
      if (convId) appendMessage(convId, 'user', content).catch(() => {});
      // 发送时会话快照：流式期间用户可能切换会话，convRef 会被 effect 改写指向新会话。
      // 后续 AI 回复持久化/首轮会话同步必须用此快照，否则回复会错写进新会话（表现为"过时/错位回复"）。
      const sentConvId = convRef.current;
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '', streaming: true, timestamp: new Date().toISOString() }]);

      // 提单 Agent
      const apiPath = `${API_CONFIG.AI.BASE_URL}/qa/ask/stream`;
      const apiBody = JSON.stringify({ session_id: sid, query: content });

      const response = await fetchWithAuth(apiPath, { method: 'POST', body: apiBody });

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

      // 流式结束：持久化 AI 回复到发送时的会话（快照）——切会话后 convRef 已指向新会话，不能再用
      if (acc && sentConvId) appendMessage(sentConvId, 'assistant', acc).catch(() => {});
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
      // 鉴权失效已由 kickToLogin 统一提示并跳转，此处不重复弹错误
      if (!isKickingToLogin()) {
        Toast({ message: `发送失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
      }
      setMessages((prev) => prev.filter((m) => m.id !== assistantId));
    } finally {
      // 先释放发送锁与 loading，再强制刷新最终内容，避免刷新异常时再次卡死发送
      setLoading(false);
      sendingRef.current = false;
      // 回复完成：强制贴底，确保流式结束（Markdown 切换）后视图定位到最新消息，无需手动滑动
      atBottomRef.current = true;
      // 强制刷新最终完整内容：流式结束前最后一次 flush 可能早于 90ms 窗口，确保末态不丢字；
      // 中间态可能显示"正在思考…"占位（JSON 头泄漏防护），此处以清洗后内容统一定稿
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: sanitizeAiText(acc) || acc } : m)));
      // 置 streaming:false：流式结束，气泡由纯文本降级渲染切换为最终 MarkdownRenderer 渲染
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, streaming: false } : m)));
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
  const clearPendingFile = () => {
    if (pendingImageUrl) URL.revokeObjectURL(pendingImageUrl);
    setPendingFile(null);
    setPendingImageUrl(null);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > MAX_FILE_SIZE) {
      const mb = (file.size / 1024 / 1024).toFixed(1);
      Toast({ message: `「${file.name}」(${mb}MB) 超过 100MB 上限，请压缩或拆分后重试`, theme: 'error' });
      return;
    }
    // 选中后不立即发送：挂到输入栏，用户可继续打字，发送时随 message 一起上传
    clearPendingFile();
    setPendingFile(file);
    if (file.type.startsWith('image/')) {
      setPendingImageUrl(URL.createObjectURL(file));
    }
  };

  /** PC 端粘贴图片：从剪贴板取 image/* 文件，走与「选择文件」一致的待发送附件流程（预览后可随消息上传） */
  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      if (item.kind !== 'file' || !item.type.startsWith('image/')) continue;
      const file = item.getAsFile();
      if (!file) continue;
      e.preventDefault(); // 阻止图片被当作 base64/文本塞进输入框
      if (file.size > MAX_FILE_SIZE) {
        const mb = (file.size / 1024 / 1024).toFixed(1);
        Toast({ message: `「${file.name}」(${mb}MB) 超过 100MB 上限，请压缩后重试`, theme: 'error' });
        return;
      }
      clearPendingFile();
      setPendingFile(file);
      setPendingImageUrl(URL.createObjectURL(file));
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
          ? `工单信息不足，还差：${missing.join('、')}。请直接在对话中告诉我，补全后再点转工单。`
          : '工单信息不足，请补充后再点转工单。');
        setMessages((prev) => [...prev, {
          id: uid(),
          role: 'assistant',
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
      setTicketConfirm({ visible: true, draft, overrides: {}, submitting: false });
      if (missing_fields?.length) {
        Toast({ message: prompt || '请补全必填字段后提交', theme: 'warning' });
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

  /** 确认提交：校验项目必填（所有类型，需绑定 project_id） → 调 confirm 入库 */
  const handleConfirmTicket = async () => {
    const draft = ticketConfirm.draft;
    if (!draft || !sessionId) return;
    const projectIdVal = draftField('project_id').trim();
    if (!projectIdVal) {
      Toast({ message: '请先选择绑定项目', theme: 'warning' });
      return;
    }
    setTicketConfirm((s) => ({ ...s, submitting: true }));
    try {
      const overrides: Partial<TicketDraft> = {
        ...ticketConfirm.overrides,
        project: draftField('project'),
        project_id: projectIdVal,
      };
      const res = await qaConfirmTicket(sessionId, overrides);
      if (res?.code !== 0) {
        Toast({ message: res?.message || '提交工单失败', theme: 'error' });
        return;
      }
      setTicketConfirm({ visible: false, draft: null, overrides: {}, submitting: false });
      refreshTasks(); // 刷新「历史工单」待派单列表
      Toast({ message: res.data?.notice || '工单已生成，可在历史工单查看', theme: 'success' });
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
          // 渲染层兜底：空白 AI 气泡（空内容/纯空白，且非流式占位、无附件）不渲染
          .filter((m) => m.role !== 'assistant' || !!m.streaming || m.content.trim().length > 0 || !!m.imageUrl || !!m.attachment)
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

      {/* 输入区（千问风格卡片：上输入，下工具行） */}
      <div
        className="chat-input-bar"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); }
        }}
      >
        {isCall && (
          <div className="chat-panel__ticket-fab" title="转为工单">
            <button
              className={`chat-ticket-btn${messages.length > 0 ? ' has-content' : ''}${submittingTicket ? ' is-submitting' : ''}${ticketMissing && ticketMissing.info.length ? ' has-missing' : ''}`}
              onClick={handleSubmitTicket}
              disabled={submittingTicket}
              aria-label="转工单"
            >
              {submittingTicket ? (
                <span className="chat-ticket-spinner" />
              ) : (
                <svg viewBox="0 0 24 24" width="20" height="20" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path fill="currentColor" d="M16 1H8V5H16V1Z" />
                <path fill="currentColor" d="M6 3H3V23H13.8762C13.0139 21.897 12.5 20.5085 12.5 19C12.5 15.4101 15.4101 12.5 19 12.5C19.6978 12.5 20.3699 12.61 21 12.8135V3H18V7H6V3Z" />
                <path fill="currentColor" d="M24 20H20V24H18V20H14V18H18V14H20V18H24V20Z" />
              </svg>
              )}
              {ticketMissing && ticketMissing.info.length > 0 && (
                <span className="chat-ticket-btn__badge">{ticketMissing.info.length}</span>
              )}
            </button>
            <span className="chat-ticket-btn__label">{submittingTicket ? '提交中…' : '转工单'}</span>
          </div>
        )}
        {pendingFile && (
          <div className="chat-pending-file">
            {pendingImageUrl ? (
              <img src={pendingImageUrl} alt="附件预览" className="chat-pending-file__thumb" />
            ) : (
              <span className="chat-pending-file__icon">📎</span>
            )}
            <span className="chat-pending-file__name">{pendingFile.name}</span>
            <button type="button" className="chat-pending-file__remove" onClick={clearPendingFile} aria-label="移除附件">✕</button>
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
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9.5" />
                <line x1="12" y1="7.5" x2="12" y2="16.5" />
                <line x1="7.5" y1="12" x2="16.5" y2="12" />
              </svg>
            </button>
          </div>
        ) : (
          <>
            <div ref={textareaContainerRef} className="chat-input-bar__textarea" onPaste={handlePaste}>
              <Textarea
                value={input}
                onChange={(v) => setInput(String(v))}
                placeholder="发消息..."
                autosize={{ minRows: 1, maxRows: 6 }}
              />
            </div>
            {textareaMaxed && !textareaFullscreen && (
              <button
                type="button"
                className="chat-input-bar__expand-btn"
                onClick={() => setTextareaFullscreen(true)}
                aria-label="全屏输入"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="15 3 21 3 21 9" />
                  <polyline points="9 21 3 21 3 15" />
                  <line x1="21" y1="3" x2="14" y2="10" />
                  <line x1="3" y1="21" x2="10" y2="14" />
                </svg>
              </button>
            )}
            <div className="chat-input-bar__tools">
              <div className="chat-input-bar__tools-left">
                {/* === 语音输入入口暂时隐藏（2026-07-28），voiceMode 相关逻辑保留以便后续恢复 ===
                <button className="chat-input-btn" onClick={() => setVoiceMode(true)} title="语音输入" aria-label="语音输入">
                  <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="3" width="6" height="11" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0" />
                    <line x1="12" y1="18" x2="12" y2="21" />
                    <line x1="8" y1="21" x2="16" y2="21" />
                  </svg>
                </button>
                */}
                <button className="chat-input-btn" onClick={() => setShowUploadMenu(true)} title="上传" aria-label="上传文件或拍照">
                  <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                    <circle cx="12" cy="12" r="9.5" />
                    <line x1="12" y1="7.5" x2="12" y2="16.5" />
                    <line x1="7.5" y1="12" x2="16.5" y2="12" />
                  </svg>
                </button>
              </div>
              <button type="button" className="chat-send-btn" onClick={() => send(input)} disabled={(!input.trim() && !pendingFile) || loading} aria-label="发送">
                {loading ? (
                  <span className="chat-send-btn__spinner" />
                ) : (
                  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="19" x2="12" y2="5" />
                    <polyline points="6 11 12 5 18 11" />
                  </svg>
                )}
              </button>
            </div>
          </>
        )}
        <input ref={cameraInputRef} type="file" accept="image/*" capture="environment" onChange={handleFileChange} style={{ display: 'none' }} />
        <input ref={albumInputRef} type="file" accept="image/*" onChange={handleFileChange} style={{ display: 'none' }} />
        <input ref={fileInputRef} type="file" accept="*/*" onChange={handleFileChange} style={{ display: 'none' }} />
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
                <button type="button" className="chat-send-btn" onClick={() => { send(input); setTextareaFullscreen(false); }} disabled={(!input.trim() && !pendingFile) || loading} aria-label="发送">
                  {loading ? (
                    <span className="chat-send-btn__spinner" />
                  ) : (
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="12" y1="19" x2="12" y2="5" />
                      <polyline points="6 11 12 5 18 11" />
                    </svg>
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
        <Popup visible={ticketConfirm.visible} onClose={() => setTicketConfirm((s) => ({ ...s, visible: false }))} placement="bottom" showOverlay>
          <div className="ticket-confirm">
            <h4 className="ticket-confirm__title">确认工单信息</h4>
            {ticketConfirm.draft && (
              <div className="ticket-confirm__body">
                <div className="ticket-confirm__tags">
                  {ticketConfirm.draft.type && <Tag theme="primary">{TICKET_TYPE_LABEL[ticketConfirm.draft.type] || ticketConfirm.draft.type}</Tag>}
                  {ticketConfirm.draft.priority && <Tag theme="warning">{ticketConfirm.draft.priority}</Tag>}
                </div>
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
                  onChange={(e) => setDraftField('priority', e.target.value)}
                >
                  <option value="紧急">紧急</option>
                  <option value="高">高</option>
                  <option value="中">中</option>
                  <option value="低">低</option>
                </select>
                <label className="ticket-confirm__label">联系人</label>
                <input
                  className="ticket-confirm__input"
                  value={draftField('contact')}
                  onChange={(e) => setDraftField('contact', e.target.value)}
                  placeholder="联系人（可选）"
                />
                <label className="ticket-confirm__label">绑定项目 <span style={{ color: '#e34d59' }}>*</span></label>
                <ProjectSelect
                  value={draftField('project_id') || null}
                  onChange={(p) => {
                    setDraftField('project', p.name);
                    setDraftField('project_id', p.project_code);
                  }}
                />
                {!draftField('project_id').trim() && (
                  <span className="ticket-confirm__hint">项目为必选项，未绑定项目无法提交</span>
                )}
              </div>
            )}
            <div className="ticket-confirm__btns">
              <button
                type="button"
                className="ticket-confirm__btn ticket-confirm__btn--cancel"
                onClick={() => setTicketConfirm((s) => ({ ...s, visible: false }))}
              >取消</button>
              <button
                type="button"
                className="ticket-confirm__btn ticket-confirm__btn--confirm"
                onClick={handleConfirmTicket}
                disabled={ticketConfirm.submitting || !draftField('project_id').trim()}
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
