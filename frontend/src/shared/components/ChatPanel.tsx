// 可复用 AI 对话面板 —— 对接 /api/ai/qa/ask/stream（SSE 流式诊断）
// 我要摇人（全屏）与系统任务（顶部紧凑）共用
// 功能：SSE 流式 / 点赞点踩 / 复制 / 修改己方 / 语音 / 上传·拍照 / ENTER 发送
// call 场景额外：消费工单讨论上下文 + 打包转工单
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Textarea, Toast } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import { useWorkbenchStore, type TicketDraft } from '@/stores/workbench';
import { qaAskStream, qaSubmit, qaUpload, generateSessionId, trackSession } from '@/api/ai';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';

interface SpeechRecognitionResultEvent {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}

interface SpeechRecognitionLike {
  lang: string;
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
  reaction?: 'like' | 'dislike' | null;
}

const uid = () => Date.now().toString() + Math.random().toString(36).slice(2, 6);

const SCENE_CONFIG: Record<ChatScene, {
  sceneType: string;
  emptyEmoji: string;
  emptyTitle: string;
}> = {
  call: { sceneType: 'chat', emptyEmoji: '🆘', emptyTitle: '一支穿云箭，千军万马来相见！' },
  tasks: { sceneType: 'task_assist', emptyEmoji: '🤖', emptyTitle: 'AI 任务助手' },
};

export default function ChatPanel({ scene, compact = false }: { scene: ChatScene; compact?: boolean }) {
  const navigate = useNavigate();
  const { token, username } = useAuthStore();
  const { chatContext, consumeChatContext, goToTab, setTicketDraft } = useWorkbenchStore();
  const isCall = scene === 'call';
  const cfg = SCENE_CONFIG[scene];

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [submittingTicket, setSubmittingTicket] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);

  const scrollToBottom = () => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); };
  useEffect(() => { scrollToBottom(); }, [messages]);

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

  /** 将图片上传到 AI 后端 */
  const uploadImage = async (file: File): Promise<string> => {
    const sid = ensureSessionId();
    const res = await qaUpload(sid, [file]);
    if (!res.ok) throw new Error(`上传失败: ${res.status}`);
    const data = await res.json();
    if (data.code !== 0) throw new Error(data.message || '上传失败');
    return file.name;
  };

  const send = async (text: string, imageFile?: File) => {
    const content = text.trim();
    if (!content && !imageFile) return;
    if (!token) { kickToLogin('请先登录'); return; }

    let imageTag = '';
    if (imageFile) {
      try {
        const name = await uploadImage(imageFile);
        imageTag = `[上传了附件] ${name}\n`;
        Toast({ message: '图片已上传', theme: 'success' });
      } catch (err) {
        Toast({ message: `上传失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        return;
      }
    }

    const userContent = imageTag ? imageTag + content : content;
    const userMessage: Message = {
      id: uid(),
      role: 'user',
      content: userContent,
      timestamp: new Date().toISOString(),
      imageUrl: imageFile ? URL.createObjectURL(imageFile) : undefined,
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    const assistantId = uid();
    try {
      const sid = ensureSessionId();
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '', timestamp: new Date().toISOString() }]);

      const response = await qaAskStream({
        session_id: sid,
        query: userContent,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let acc = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        for (const line of text.split('\n')) {
          // SSE 事件行：event: first_token / event: done / event: error
          if (line.startsWith('event: ') && line.includes('error')) continue; // 错误行在下面处理

          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.token) {
              // 新版 AI 诊断：token 增量
              acc += data.token;
              setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)));
            } else if (data.content) {
              // 兼容旧版 message
              acc += data.content;
              setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)));
            }
          } catch { /* JSON 行解析出错则跳过 */ }
        }
      }
    } catch (err) {
      // 鉴权失效已由 kickToLogin 统一提示并跳转，此处不重复弹错误
      if (!isKickingToLogin()) {
        Toast({ message: `发送失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
      }
      setMessages((prev) => prev.filter((m) => m.id !== assistantId));
    } finally {
      setLoading(false);
    }
  };

  const editAndResend = (msg: Message) => {
    setMessages((prev) => prev.filter((m) => m.id !== msg.id));
    setEditingId(null);
    send(msg.content);
  };

  const startVoice = () => {
    if (!SR) { Toast({ message: '当前浏览器不支持语音输入', theme: 'warning' }); return; }
    if (recognitionRef.current) { recognitionRef.current.stop(); recognitionRef.current = null; return; }
    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.interimResults = false;
    rec.onresult = (e: SpeechRecognitionResultEvent) => {
      const text = e.results[0][0].transcript;
      setInput((prev) => (prev ? `${prev} ${text}` : text));
    };
    rec.onerror = () => Toast({ message: '语音识别失败', theme: 'error' });
    rec.onend = () => { recognitionRef.current = null; };
    rec.start();
    recognitionRef.current = rec;
    Toast({ message: '语音识别中，再次点击结束', theme: 'success' });
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    send(input, file);
    e.target.value = '';
  };

  /** 转工单：优先调用后端 /api/ai/qa/submit，失败则本地 draft 模式兜底 */
  const handleSubmitTicket = async () => {
    if (submittingTicket || messages.length === 0) return;
    setSubmittingTicket(true);

    const recent = messages.filter((m) => m.content).slice(-6);
    const userMsgs = recent.filter((m) => m.role === 'user').map((m) => m.content);
    const aiSummary = recent.filter((m) => m.role === 'assistant').map((m) => m.content).join('\n');
    const draft: TicketDraft = {
      title: userMsgs[0]?.slice(0, 40) || 'AI 咨询转工单',
      description: `【用户描述】\n${userMsgs.join('\n')}\n\n【AI 诊断摘要】\n${aiSummary}`,
      ticket_type: 'support',
      priority: 'medium',
      source_conversation_id: sessionId || undefined,
    };

    if (sessionId) {
      try {
        const result = await qaSubmit(sessionId);
        if (result.code === 0) {
          setTicketDraft(draft);
          goToTab('tasks', { ticketDraft: draft });
          navigate('/app/tasks');
          Toast({ message: '工单已提交，正在跳转…', theme: 'success' });
          setSubmittingTicket(false);
          return;
        }
      } catch { /* 后端失败则走本地 draft 兜底 */ }
    }

    // 本地 draft 模式兜底
    setTicketDraft(draft);
    goToTab('tasks', { ticketDraft: draft });
    navigate('/app/tasks');
    Toast({ message: '已打包转工单，正在跳转…', theme: 'success' });
    setSubmittingTicket(false);
  };

  const toggleReaction = (id: string, type: 'like' | 'dislike') => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, reaction: m.reaction === type ? null : type } : m)));
  };

  const copyContent = (content: string) => {
    // Clipboard API（安全上下文可用），否则降级 execCommand
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(content).then(
        () => Toast({ message: '已复制', theme: 'success' }),
        () => fallbackCopy(content),
      );
      return;
    }
    fallbackCopy(content);
  };

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

  return (
    <div className={`chat-panel${compact ? ' is-compact' : ''}`}>

      <div className="chat-view__messages">
        {messages.length === 0 && (
          <div className="chat-view__empty">
            <div className="chat-view__empty-emoji">{cfg.emptyEmoji}</div>
            <p>{cfg.emptyTitle}</p>
            <p className="chat-view__empty-sub">
              {isCall ? `你好${username ? `，${username}` : ''}，描述你的问题，AI 先帮你初步诊断。` : '关于系统任务的问题，可以随时问我。'}
            </p>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`chat-bubble-wrap ${msg.role === 'user' ? 'is-right' : 'is-left'}`}>
            <div className={`chat-bubble ${msg.role === 'user' ? 'is-user' : 'is-ai'}`}>
              {msg.imageUrl && <img src={msg.imageUrl} alt="附件" className="chat-bubble__img" />}
              {editingId === msg.id ? (
                <Textarea
                  value={msg.content}
                  autosize={{ minRows: 1, maxRows: 6 }}
                  onChange={(v) =>
                    setMessages((prev) => prev.map((m) => (m.id === msg.id ? { ...m, content: String(v) } : m)))
                  }
                />
              ) : msg.role === 'assistant' ? (
                msg.content ? (
                  <MarkdownRenderer content={msg.content} compact={compact} />
                ) : (
                  loading ? <div className="chat-bubble__text">思考中…</div> : null
                )
              ) : (
                <div className="chat-bubble__text">
                  {msg.content}
                </div>
              )}
            </div>

            <div className="chat-actions">
              {msg.role === 'assistant' && (
                <>
                  <button className={`chat-action ${msg.reaction === 'like' ? 'is-active' : ''}`} onClick={() => toggleReaction(msg.id, 'like')}>👍</button>
                  <button className={`chat-action ${msg.reaction === 'dislike' ? 'is-active' : ''}`} onClick={() => toggleReaction(msg.id, 'dislike')}>👎</button>
                  <button className="chat-action" onClick={() => copyContent(msg.content)}>📋</button>
                </>
              )}
              {msg.role === 'user' && (
                <>
                  <button className="chat-action" onClick={() => copyContent(msg.content)}>📋</button>
                  {editingId === msg.id ? (
                    <>
                      <button className="chat-action" onClick={() => editAndResend(msg)}>✅</button>
                      <button className="chat-action" onClick={() => setEditingId(null)}>✖️</button>
                    </>
                  ) : (
                    <button className="chat-action" onClick={() => setEditingId(msg.id)}>✏️</button>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

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
        <Textarea
          value={input}
          onChange={(v) => setInput(String(v))}
          placeholder="发消息..."
          autosize={{ minRows: 1, maxRows: 6 }}
          className="chat-input-bar__textarea"
        />
        <div className="chat-input-bar__tools">
          <div className="chat-input-bar__tools-left">
            <button className="chat-input-btn" onClick={startVoice} title="语音输入" aria-label="语音输入">
              <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="3" width="6" height="11" rx="3" />
                <path d="M5 11a7 7 0 0 0 14 0" />
                <line x1="12" y1="18" x2="12" y2="21" />
                <line x1="8" y1="21" x2="16" y2="21" />
              </svg>
            </button>
            <button className="chat-input-btn" onClick={() => fileInputRef.current?.click()} title="上传" aria-label="上传文件或拍照">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9.5" />
                <line x1="12" y1="7.5" x2="12" y2="16.5" />
                <line x1="7.5" y1="12" x2="16.5" y2="12" />
              </svg>
            </button>
          </div>
          <button type="button" className="chat-send-btn" onClick={() => send(input)} disabled={!input.trim() || loading} aria-label="发送">
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
        <input ref={fileInputRef} type="file" accept="image/*" capture="environment" onChange={handleFileChange} style={{ display: 'none' }} />
      </div>
    </div>
  );
}
