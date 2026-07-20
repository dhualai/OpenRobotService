// 可复用 AI 对话面板 —— call 场景对接 /api/ai/qa/ask/stream（SSE 流式诊断）
// tasks 场景对接 /api/ai/task/analyze/stream（任务 Agent 方案生成）
// 我要摇人（全屏）与系统任务（顶部紧凑）共用
// 功能：SSE 流式 / 点赞点踩 / 复制 / 修改己方 / 语音 / 上传·拍照 / ENTER 发送
// call 场景额外：消费工单讨论上下文 + 打包转工单
import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Textarea, Toast } from 'tdesign-mobile-react';
import { useAuthStore } from '@/stores/auth';
import { useWorkbenchStore } from '@/stores/workbench';
import API_CONFIG from '@/config/api';
import { qaSubmit, qaUpload, generateSessionId, trackSession, fetchWithAuth } from '@/api/ai';
import { createConversation, getConversation, listMyConversations, appendMessage, readAiSessionId } from '@/api/conversation';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import SolutionCard from '@/shared/components/SolutionCard';
import type { SolutionDraft } from '@/shared/components/SolutionCard';

interface SpeechRecognitionResultEvent {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onsoundstart: (() => void) | null;
  onsoundend: (() => void) | null;
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
  // 任务 Agent 专属：结构化方案草稿
  subtype?: 'solution_draft';
  solution_draft?: {
    root_cause_analysis: string;
    suggested_actions: string[];
    references: string[];
    confidence: number;
    needs_more_info: boolean;
  };
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

export default function ChatPanel({ scene, compact = false, taskId }: { scene: ChatScene; compact?: boolean; taskId?: string }) {
  const navigate = useNavigate();
  const { token, username } = useAuthStore();
  const { chatContext, consumeChatContext, refreshTasks } = useWorkbenchStore();
  const isCall = scene === 'call';
  const cfg = SCENE_CONFIG[scene];

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [submittingTicket, setSubmittingTicket] = useState(false);
  const [submittingSolution, setSubmittingSolution] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [voiceMode, setVoiceMode] = useState(false);
  const [voiceWillCancel, setVoiceWillCancel] = useState(false);
  const [voiceHasSound, setVoiceHasSound] = useState(false);
  const voiceStartYRef = useRef<number>(0);
  const voiceCancelRef = useRef(false);
  const voiceWillCancelRef = useRef(false);
  const voiceSessionRef = useRef(0);       // 递增，防止延迟的 onend 误修改状态
  const voiceHoldingRef = useRef(false);  // 用户是否正在按住语音按钮
  const finishRecRef = useRef<(() => void) | null>(null); // 松手清理函数，供 button onMouseUp/onTouchEnd 调用
  const voiceBtnRef = useRef<HTMLButtonElement>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const convRef = useRef<number | null>(null); // 当前 DB 会话 id，跨 send 复用

  const scrollToBottom = () => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }); };
  useEffect(() => { scrollToBottom(); }, [messages]);

  // 挂载时恢复"最近一条会话"：从 DB 读回历史消息 + ai_session_id，刷新/切页后内容不丢
  useEffect(() => {
    if (!token || !username) return;
    if (chatContext) return; // 从工单「讨论」进入时走新会话，不恢复旧历史
    let cancelled = false;
    (async () => {
      try {
        const list = await listMyConversations(scene, 1);
        if (cancelled || !list.length) return;
        const full = await getConversation(list[0].id);
        if (cancelled) return;
        convRef.current = full.id;
        const restored = (full.messages || []).map((m) => ({
          id: String(m.id),
          role: m.role as 'user' | 'assistant',
          content: m.content,
          timestamp: m.created_at,
        }));
        if (restored.length) setMessages(restored);
        const sid = readAiSessionId(full);
        if (sid) setSessionId(sid);
      } catch { /* 恢复失败不阻塞新对话 */ }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, username, scene]);

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
      // 持久化用户消息（首条会顺带建会话）
      const convId = await ensureConversation(sid, userContent);
      if (convId) appendMessage(convId, 'user', userContent).catch(() => {});
      setMessages((prev) => [...prev, { id: assistantId, role: 'assistant', content: '', timestamp: new Date().toISOString() }]);

      // tasks 场景：调任务 Agent；call 场景：调提单 Agent
      const AI_BASE = API_CONFIG.AI.BASE_URL;
      const apiPath = isCall
        ? `${AI_BASE}/qa/ask/stream`
        : `${AI_BASE}/task/analyze/stream`;
      const apiBody = isCall
        ? JSON.stringify({ session_id: sid, query: userContent })
        : JSON.stringify({ task_id: taskId || '', session_id: sid });

      const response = await fetchWithAuth(apiPath, { method: 'POST', body: apiBody });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let acc = '';
      let solutionDraft: Message['solution_draft'] | null = null;
      let currentEvent = '';
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
              setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)));
            } else if (data.content) {
              acc += data.content;
              setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, content: acc } : m)));
            }
            // 任务 Agent result 事件：拿到结构化方案草稿
            if (currentEvent === 'result' && data.root_cause_analysis) {
              solutionDraft = {
                root_cause_analysis: data.root_cause_analysis,
                suggested_actions: data.suggested_actions || [],
                references: data.references || [],
                confidence: data.confidence ?? 0,
                needs_more_info: data.needs_more_info ?? false,
              };
            }
          } catch { /* JSON 行解析出错则跳过 */ }
        }
      }

      // 流式结束：持久化 AI 回复
      if (acc && convRef.current) appendMessage(convRef.current, 'assistant', acc).catch(() => {});
      // 任务 Agent 方案草稿：注入 solution_draft 标记
      if (solutionDraft && !isCall) {
        setMessages((prev) => prev.map((m) =>
          m.id === assistantId
            ? { ...m, subtype: 'solution_draft' as const, solution_draft: solutionDraft }
            : m
        ));
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

  // voiceWillCancelRef 在 handleMove 中直接同步写入，不再通过 useEffect 异步同步

  // 语音模式下全局监听 move/end，基于 voiceMode 而非 isRecording，确保录制结束后松手仍可触发 finishRecording
  useEffect(() => {
    if (!voiceMode) return;

    const handleMove = (e: MouseEvent | TouchEvent) => {
      const currentY = 'touches' in e
        ? (e as TouchEvent).touches[0]?.clientY ?? 0
        : (e as MouseEvent).clientY;
      const deltaY = voiceStartYRef.current - currentY; // 正值 = 上移
      const willCancel = deltaY > 60;
      voiceWillCancelRef.current = willCancel; // 同步写 ref，确保 handleEnd/touchend 读到最新值
      setVoiceWillCancel(willCancel);
    };

    // 松手清理：同时被文档 mouseup/touchend 和按钮 onMouseUp/onTouchEnd 调用，靠 voiceHoldingRef 防重
    const finishRecording = () => {
      if (!voiceHoldingRef.current) return; // 已执行过则忽略
      voiceHoldingRef.current = false;
      if (voiceWillCancelRef.current) {
        voiceCancelRef.current = true; // 标记取消，阻止 onresult 写入
      }
      voiceSessionRef.current++;       // 递增，使任何延迟的 onend 失效
      recognitionRef.current?.stop();
      recognitionRef.current = null;
      setIsRecording(false);
      if (!voiceWillCancelRef.current) setVoiceMode(false); // 非取消时回到文本模式
      setVoiceWillCancel(false);
      setVoiceHasSound(false);
    };

    finishRecRef.current = finishRecording; // 供按钮 onMouseUp/onTouchEnd 直接调用

    document.addEventListener('mousemove', handleMove);
    document.addEventListener('touchmove', handleMove, { passive: true });
    document.addEventListener('mouseup', finishRecording);
    document.addEventListener('touchend', finishRecording);

    return () => {
      finishRecRef.current = null;
      document.removeEventListener('mousemove', handleMove);
      document.removeEventListener('touchmove', handleMove);
      document.removeEventListener('mouseup', finishRecording);
      document.removeEventListener('touchend', finishRecording);
    };
  }, [voiceMode]);

  const startVoiceRecording = (e: React.MouseEvent | React.TouchEvent) => {
    if (!SR) { Toast({ message: '当前浏览器不支持语音输入', theme: 'warning' }); return; }
    // 防止 touch 同时合成 mouse 事件导致双开识别实例互相干扰
    if (voiceHoldingRef.current || recognitionRef.current) return;
    e.preventDefault(); // 阻止 touch 后合成 mouse 事件

    voiceHoldingRef.current = true;
    const sessionId = ++voiceSessionRef.current; // 递增，用于 onend 校验
    voiceStartYRef.current = 'touches' in e ? (e.touches[0]?.clientY || 0) : e.clientY;
    voiceCancelRef.current = false;
    setVoiceWillCancel(false);
    setVoiceHasSound(false);

    const rec = new SR();
    rec.lang = 'zh-CN';
    rec.continuous = true;       // 持续识别，不因用户停顿而自动停止
    rec.interimResults = false;  // 仅返回最终识别结果
    rec.onsoundstart = () => setVoiceHasSound(true);
    rec.onsoundend = () => setVoiceHasSound(false);
    rec.onresult = (ev: SpeechRecognitionResultEvent) => {
      if (voiceCancelRef.current) return; // 上移取消，丢弃结果
      // continuous 模式下从 resultIndex 开始遍历，取最新识别结果
      let latest = '';
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        latest = ev.results[i][0].transcript;
      }
      if (latest) {
        setInput((prev) => (prev ? `${prev} ${latest}` : latest));
      }
    };
    rec.onerror = () => {
      // 非当前 session 的延迟回调直接跳过，防止旧实例误操作新状态
      if (voiceSessionRef.current !== sessionId) return;
      voiceSessionRef.current++;       // 递增，防止后续 onend 再处理
      voiceHoldingRef.current = false;
      recognitionRef.current = null;
      setIsRecording(false);
      setVoiceHasSound(false);
      setVoiceMode(false);
    };
    rec.onend = () => {
      // 非当前 session 的延迟回调直接跳过（finishRecording/onerror 已先递增 session）
      if (voiceSessionRef.current !== sessionId) return;
      recognitionRef.current = null;   // 确认是当前 session 再清引用
      setIsRecording(false);
    };
    rec.start();
    recognitionRef.current = rec;
    setIsRecording(true);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    send(input, file);
    e.target.value = '';
  };

  /** 转工单：调 AI 模块 /api/ai/qa/submit 生成工单（写 MySQL + 入待派单列表），刷新「历史工单」，留在摇人页 */
  const handleSubmitTicket = async () => {
    if (submittingTicket || messages.length === 0) return;
    if (!sessionId) { Toast({ message: '会话未就绪，请先发送一条消息', theme: 'warning' }); return; }
    setSubmittingTicket(true);
    try {
      const res = await qaSubmit(sessionId);
      if (res?.code === 0) {
        refreshTasks(); // 触发下方「历史工单」重新拉取 AI 待派单列表
        Toast({ message: '工单已生成，可在下方历史工单查看', theme: 'success' });
      } else {
        Toast({ message: (res as { message?: string })?.message || '生成工单失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `生成工单失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingTicket(false);
    }
  };

  /** 任务 Agent：提交方案 → 调 /api/ai/task/submit */
  const handleSubmitSolution = async (msg: Message) => {
    if (submittingSolution || !msg.solution_draft) return;
    setSubmittingSolution(true);
    try {
      const res = await fetchWithAuth(`${API_CONFIG.AI.BASE_URL}/task/submit`, {
        method: 'POST',
        body: JSON.stringify({
          task_id: taskId || '',
          session_id: sessionId,
          final_solution: msg.solution_draft,
          resolution: 'resolved',
        }),
      });
      const data = await res.json();
      if (data.code === 0) {
        refreshTasks();
        Toast({ message: '方案已提交，工单已解决', theme: 'success' });
      } else {
        Toast({ message: (data as { message?: string })?.message || '提交失败', theme: 'error' });
      }
    } catch (err) {
      Toast({ message: `提交失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setSubmittingSolution(false);
    }
  };

  /** 任务 Agent：重新分析当前工单 */
  const handleReanalyze = () => {
    if (!taskId) return;
    send(`请重新分析工单 #${taskId}`);
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
                msg.subtype === 'solution_draft' && msg.solution_draft ? (
                  <SolutionCard
                    draft={msg.solution_draft}
                    onDraftChange={(draft) =>
                      setMessages((prev) => prev.map((m) =>
                        m.id === msg.id ? { ...m, solution_draft: draft } : m
                      ))
                    }
                    onSubmit={() => handleSubmitSolution(msg)}
                    onReanalyze={handleReanalyze}
                    submitting={submittingSolution}
                  />
                ) : msg.content ? (
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
        {voiceMode ? (
          <div className="chat-input-bar__voice-row">
            <button className="chat-input-btn" onClick={() => setVoiceMode(false)} title="键盘输入" aria-label="键盘输入">
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
              onMouseDown={startVoiceRecording}
              onTouchStart={startVoiceRecording}
              onMouseUp={() => finishRecRef.current?.()}
              onTouchEnd={() => finishRecRef.current?.()}
            >
              {isRecording ? (
                voiceWillCancel ? '松开 取消' : (
                  <span className={`voice-wave-dots${voiceHasSound ? ' has-sound' : ''}`}>
                    <span className="voice-dot" />
                    <span className="voice-dot" />
                    <span className="voice-dot" />
                  </span>
                )
              ) : '按住 说话'}
            </button>
            <button className="chat-input-btn" onClick={() => fileInputRef.current?.click()} title="上传" aria-label="上传文件或拍照">
              <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <circle cx="12" cy="12" r="9.5" />
                <line x1="12" y1="7.5" x2="12" y2="16.5" />
                <line x1="7.5" y1="12" x2="16.5" y2="12" />
              </svg>
            </button>
          </div>
        ) : (
          <>
            <Textarea
              value={input}
              onChange={(v) => setInput(String(v))}
              placeholder="发消息..."
              autosize={{ minRows: 1, maxRows: 6 }}
              className="chat-input-bar__textarea"
            />
            <div className="chat-input-bar__tools">
              <div className="chat-input-bar__tools-left">
                <button className="chat-input-btn" onClick={() => setVoiceMode(true)} title="语音输入" aria-label="语音输入">
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
          </>
        )}
        <input ref={fileInputRef} type="file" accept="image/*" capture="environment" onChange={handleFileChange} style={{ display: 'none' }} />
      </div>
    </div>
  );
}
