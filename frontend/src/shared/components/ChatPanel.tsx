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
import { createConversation, getConversation, listMyConversations, appendMessage, readAiSessionId } from '@/api/conversation';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
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
  // 非图片附件（zip/日志/文档等）：仅当次会话本地预览用，按文件名+大小展示文件卡片
  attachment?: { name: string; size: number } | null;
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

// 单条消息气泡（React.memo）：流式期间仅最后一条 content/streaming 变化，历史消息跳过整列表重渲染，消除抖动
const MessageBubble = memo(function MessageBubble({
  msg, editingId, compact, onToggleReaction, onCopy, onEditStart, onEditChange, onEditSave, onEditCancel,
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
}) {
  return (
    <div className={`chat-bubble-wrap ${msg.role === 'user' ? 'is-right' : 'is-left'}`}>
      <div className={`chat-bubble ${msg.role === 'user' ? 'is-user' : 'is-ai'}`}>
        {msg.imageUrl && (
          <div className="chat-bubble__media">
            <img src={msg.imageUrl} alt="附件" className="chat-bubble__img" />
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
          <div className={`chat-bubble__file${msg.failed ? ' is-failed' : ''}`}>
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

export default function ChatPanel({ scene, compact = false }: { scene: ChatScene; compact?: boolean }) {
  const navigate = useNavigate();
  const { token, name, username } = useAuthStore();
  const { chatContext, consumeChatContext, refreshTasks, conversationId, setConversationId, setConversationTitle, renameConversation, refreshConversations } = useWorkbenchStore();
  const isCall = scene === 'call';
  const cfg = SCENE_CONFIG[scene];

  console.log('[ChatPanel] 用户信息: name="', name, '", username="', username, '", token=', !!token);

  const [messages, setMessages] = useState<Message[]>([]);
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
  const sendingRef = useRef(false); // 防双发（Enter + click 竞态）

  // 滚动跟随：仅在用户贴底时自动跟随；流式中瞬时置底（behavior:'auto'）避免 smooth 动画排队抖动
  const atBottomRef = useRef(true);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const scrollToBottom = useCallback(() => {
    if (!atBottomRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'auto' });
  }, []);
  useEffect(() => { scrollToBottom(); }, [messages, scrollToBottom]);
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

  // 挂载时恢复最近一条会话 → 设置 conversationId（由下方 effect 加载消息）
  useEffect(() => {
    if (!token || !username) return;
    if (chatContext) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await listMyConversations(scene, 1);
        if (cancelled || !list.length) return;
        setConversationId(list[0].id);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, username, scene]);

  // conversationId 变化 → 加载会话（切换）或清空（新建）。首次跳过（等上面的恢复）
  const convLoadedRef = useRef(false);
  useEffect(() => {
    if (!convLoadedRef.current) { convLoadedRef.current = true; return; }
    if (conversationId === null) {
      // 新建会话：清空消息 + sessionId，标题显示「新建会话」
      convRef.current = null;
      setMessages([]);
      setSessionId('');
      setConversationTitle('新建会话');
      return;
    }
    // convRef 已是当前会话 → ensureConversation 刚设置的，不重复加载（避免覆盖正在进行的对话）
    if (convRef.current === conversationId) return;
    if (!conversationId) return;
    let cancelled = false;
    (async () => {
      try {
        const full = await getConversation(conversationId);
        if (cancelled) return;
        convRef.current = full.id;
        const restored = (full.messages || []).map((m) => ({
          id: String(m.id),
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: m.created_at,
        }));
        setMessages(restored);
        setConversationTitle(full.title || '');
        const sid = readAiSessionId(full);
        if (sid) setSessionId(sid);
        else setSessionId('');
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId]);

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

  /** 确保 DB 会话存在：首条消息时创建（title 取首句），后续复用 convRef */
  const ensureConversation = async (sid: string, firstContent: string): Promise<number | null> => {
    if (convRef.current) return convRef.current;
    try {
      const conv = await createConversation({
        title: firstContent.slice(0, 40) || 'AI 对话',
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

      // 上传完成：用户气泡进度遮罩消失
      setMessages((prev) => prev.map((m) => (m.id === userId ? { ...m, uploading: false, percent: 100 } : m)));

      // 持久化用户消息（无文字时以附件说明兜底，便于会话列表/历史展示）
      const convId = await ensureConversation(sid, content || `[发送了附件] ${file.name}`);
      if (convId) appendMessage(convId, 'user', content || `[发送了附件] ${file.name}`).catch(() => {});

      // AI 回复填入占位气泡：文件+文字=完整诊断(ai_response.message)；只传文件=确认回执(ack_message)
      const aiResp = data?.ai_response;
      const assistantContent = (aiResp && aiResp.message) || data?.ack_message || '';
      if (assistantContent) {
        setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: assistantContent } : m)));
        if (convRef.current) appendMessage(convRef.current, 'assistant', assistantContent).catch(() => {});
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
      Toast({ message: `发送失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
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
    const renderAcc = () => setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)));
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
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        for (const line of text.split('\n')) {
          if (line.startsWith('event: ')) {
            currentEvent = line.slice(7);
            if (currentEvent === 'error') continue;
            continue;
          }
          if (!line.startsWith('data: ')) continue;
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
          } catch { /* JSON 行解析出错则跳过 */ }
        }
      }

      // 流式出错且无任何内容 → 抛出，由外层 catch 提示并移除空气泡（不再静默）
      if (streamError && !acc) throw new Error(streamError);

      // 流式结束：持久化 AI 回复
      if (acc && convRef.current) appendMessage(convRef.current, 'assistant', acc).catch(() => {});
      // 首轮问答完成 → 同步会话到列表（标题=首轮提问），定位到新会话
      if (wasNew && convRef.current) {
        setConversationId(convRef.current);
        setConversationTitle(content.slice(0, 40) || 'AI 对话');
        refreshConversations();
      }
      // 任务 Agent 方案草稿：注入 solution_draft 标记
      if (solutionDraft && !isCall) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId
            ? { ...m, subtype: 'solution_draft' as const, solution_draft: solutionDraft }
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
      // 强制刷新最终完整内容：流式结束前最后一次 flush 可能早于 90ms 窗口，确保末态不丢字
      renderAcc();
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

  /** 转工单（二次确认）：prepare 生成草稿 → 弹窗核对/补字段 → confirm 入库 */
  const handleSubmitTicket = async () => {
    if (submittingTicket || ticketConfirm.submitting) return;
    if (messages.length === 0) { Toast({ message: '请先发送一条消息描述问题', theme: 'warning' }); return; }
    if (!sessionId) { Toast({ message: '会话未就绪，请先发送一条消息', theme: 'warning' }); return; }
    setSubmittingTicket(true);
    try {
      const res = await qaPrepareTicket(sessionId);
      if (res?.code !== 0 || !res.data) {
        Toast({ message: res?.message || '生成工单草稿失败', theme: 'error' });
        return;
      }
      const { draft, missing_fields, prompt } = res.data;
      // 打开确认弹窗，让用户核对/编辑/补字段
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
              {isCall ? `你好${name || username ? `，${name || username}` : ''}，请描述你的问题，小U先帮你初步诊断。` : '关于系统任务的问题，可以随时问我。'}
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            msg={msg}
            editingId={editingId}
            compact={compact}
            onToggleReaction={toggleReaction}
            onCopy={copyContent}
            onEditStart={setEditingId}
            onEditChange={(id, v) => setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, content: v } : m)))}
            onEditSave={handleEditSave}
            onEditCancel={() => setEditingId(null)}
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
              className={`chat-ticket-btn${messages.length > 0 ? ' has-content' : ''}${submittingTicket ? ' is-submitting' : ''}`}
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
            <div ref={textareaContainerRef} className="chat-input-bar__textarea">
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
      </div>
    </div>
  );
}
