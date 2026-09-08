// 后台管理「AI 数据助手」入口 —— 悬浮球 + 聊天抽屉（含会话管理：新建 / 历史 / 删除）
//
// 设计对照：
//  - 悬浮球形制模仿「我要摇人」聊天页的转工单悬浮球（ChatPanel 内 .chat-panel__ticket-fab）：
//    52px 液态玻璃圆钮 + 常显小标签 + 可拖拽自由定位；差异点是色相换为深一号蓝（--blue-2）、
//    呼吸闪烁放慢至 3.6s。
//  - 点开为右侧抽屉式聊天对话框（窄屏自动全宽），气泡样式复用全局 .chat-bubble 体系，与摇人对话观感一致。
//  - 问答走真实接口：POST /api/ai/analysis/chat（AiDataAnalysisPlatform 快速对话，非流式 JSON）。
//  - 会话持久化：独立表 dataqa_conversations/messages（/api/dataqa/*），与摇人对话库表完全隔离：
//    首问自动建会话（标题=首问截断）并逐轮落库；头部可新建会话、查看历史会话列表（恢复完整记录）
//    并可删除历史会话。
//  - 权限：入口可见性由权限码 frontend:dataqa:view（「后台管理-问数据按钮可见性」）控制——
//    在后台管理-其他-权限管理里定义、按角色在角色管理/分配角色里勾选：
//    授予该权限的角色可见，未授予（且非 admin 通配权限）整组件不渲染。
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Popup, Button, Toast } from 'tdesign-mobile-react';
import { Bot, History, MessageSquarePlus, Send, Sparkles, Trash2, X } from 'lucide-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { analysisChat } from '@/api/analysis';
import {
  createConversation,
  listMyConversations,
  getConversation,
  deleteConversation,
  appendMessage,
  type DataqaConversation,
} from '@/api/dataqa';
import { formatDateTimeShort } from '@/shared/utils/url';
import { kickToLogin, isKickingToLogin } from '@/shared/utils/session';
import { useAuthStore } from '@/stores/auth';
import './AdminDataAssistant.css';

/** 气泡 id 生成（纯本地） */
const uid = (() => {
  let n = 0;
  return () => `ada-msg-${++n}-${Date.now().toString(36)}`;
})();

interface AdaMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** true = 正在等待后端回答（打字占位），内容定稿前不渲染 Markdown */
  typing?: boolean;
  /** 响应模式：chat（纯闲聊）或 analysis（指标分析），由后端意图识别返回；仅 assistant 有效 */
  mode?: 'chat' | 'analysis';
}

/** 空态推荐问题 */
const CHIP_QUESTIONS = [
  '今天服务号有多少新报障？',
  '本周工单处理情况怎么样？',
  '服务号最近用户增长如何？',
  '本月报障集中在哪些车型？',
];

const WELCOME_TEXT = `你好，我是**后台数据助手** 👋 可以问我服务号的运营情况：新增报障、处理时效、用户增长、项目进展……`;

/** 「问数据」按钮可见性权限码（后台管理-权限管理里维护，按角色勾选授予） */
const DATA_ASSISTANT_PERMISSION = 'frontend:dataqa:view';

export default function AdminDataAssistant() {
  const { pathname } = useLocation();
  const isAdminPath = pathname.startsWith('/admin');
  const permissions = useAuthStore((s) => s.permissions);
  const isAdmin = useAuthStore((s) => s.isAdmin);
  // 用户ID（users.id）：传给分析接口，分析意图时后端按该用户关联项目自动查库
  const userId = useAuthStore((s) => s.userId);
  // 页面上下文中的项目代码：从后台管理项目详情/子页面路由中提取
  // （/admin/project/:code/...），问题未提及具体项目时作为兜底
  const pageProjectCode = useMemo(() => {
    const m = pathname.match(/^\/admin\/project\/([^/]+)/);
    return m?.[1] ?? undefined;
  }, [pathname]);

  // ── 权限门禁：按「后台管理-问数据按钮可见性」（frontend:dataqa:view）控制可见性 ──
  // 后端 /users/{username}/detail 会把用户所有角色授予的权限码聚合进 permissions；
  // admin 通配（isAdmin 或 permissions 含 'admin'）始终可见，其余看是否被授予该权限码。
  const canAskData = useMemo(() => {
    if (isAdmin) return true; // 鉴权中心返回的 admin 判定
    if ((permissions ?? []).includes('admin')) return true; // admin 通配权限
    return (permissions ?? []).includes(DATA_ASSISTANT_PERMISSION);
  }, [isAdmin, permissions]);

  // ── 悬浮球（转工单同款拖拽：pointer 捕获，位移 >8px 视为移动并抑制点击） ──
  const fabRef = useRef<HTMLButtonElement>(null);
  const [fabPos, setFabPos] = useState<{ x: number; y: number } | null>(null);
  const fabDragRef = useRef({ active: false, moved: false, justDragged: false, startX: 0, startY: 0, baseX: 0, baseY: 0 });
  const FAB_SIZE = 52;
  const clampFabPos = (x: number, y: number) => ({
    x: Math.min(Math.max(8, x), window.innerWidth - FAB_SIZE - 8),
    // 底部避开三 Tab 导航及其他悬浮元素（留 ~188px，与 CSS 初始 bottom:192px 对齐）
    y: Math.min(Math.max(8, y), window.innerHeight - FAB_SIZE - 188),
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

  // ── 聊天抽屉状态 ──
  const [open, setOpen] = useState(false);
  const welcomeMsg = useMemo<AdaMessage>(
    () => ({ id: uid(), role: 'assistant', content: WELCOME_TEXT }),
    [],
  );
  const [messages, setMessages] = useState<AdaMessage[]>(() => [welcomeMsg]);
  const [input, setInput] = useState('');
  const thinking = messages.some((m) => m.typing);
  const userTurnCount = messages.filter((m) => m.role === 'user').length;
  // 在途请求：发新问题 / 清空 / 关抽屉 / 卸载时 abort，杜绝迟到响应回写已关闭的对话框
  const pendingRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false); // 防双发（Enter + click 竞态）
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // ── 会话管理状态（独立表 dataqa_conversations/messages，与摇人对话隔离） ──
  const [convId, setConvId] = useState<number | null>(null);
  const convIdRef = useRef<number | null>(null);
  const [conversations, setConversations] = useState<DataqaConversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [deleting, setDeleting] = useState<DataqaConversation | null>(null);

  const abortPending = () => {
    if (pendingRef.current) {
      pendingRef.current.abort();
      pendingRef.current = null;
    }
  };

  /** 拉取当前用户的数据助手历史会话（后端按 updated_at 倒序） */
  const refreshList = async () => {
    try {
      setConversations(await listMyConversations(50));
    } catch { /* 会话列表拉取失败不打断问答 */ }
  };

  /** 新建会话：中止在途请求，回到空白新会话（首问发送时才真正落库） */
  const newConversation = () => {
    abortPending();
    sendingRef.current = false;
    setConvId(null);
    convIdRef.current = null;
    setMessages([{ ...welcomeMsg, id: uid() }]);
    setInput('');
    setHistoryOpen(false);
    const t = inputRef.current;
    if (t) t.style.height = '';
  };

  /** 切换到历史会话：中止在途请求，从 DB 恢复完整消息记录 */
  const openConversation = async (id: number) => {
    if (id === convIdRef.current) { setHistoryOpen(false); return; }
    abortPending();
    sendingRef.current = false;
    setHistoryOpen(false);
    try {
      const conv = await getConversation(id);
      const restored: AdaMessage[] = (conv.messages ?? [])
        .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim())
        .map((m) => {
          let mode: 'chat' | 'analysis' | undefined;
          if (m.metadata_) {
            try { const meta = JSON.parse(m.metadata_); mode = meta.mode; } catch { /* 元数据损坏忽略 */ }
          }
          return { id: uid(), role: (m.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant', content: m.content, mode };
        });
      setConvId(id);
      convIdRef.current = id;
      setMessages(restored.length ? restored : [{ ...welcomeMsg, id: uid() }]);
      setInput('');
    } catch {
      Toast({ message: '会话加载失败，请稍后重试', theme: 'error' });
    }
  };

  /** 删除会话（底部弹窗确认）；删的是当前会话则回到空白新会话 */
  const handleDeleteConfirm = async () => {
    if (!deleting) return;
    const target = deleting;
    setDeleting(null);
    try {
      await deleteConversation(target.id);
      Toast({ message: '会话已删除', theme: 'success' });
      if (convIdRef.current === target.id) newConversation();
      void refreshList();
    } catch {
      Toast({ message: '删除失败，请稍后重试', theme: 'error' });
    }
  };

  // 打开抽屉后聚焦输入框（等位移动画结束），并刷新历史会话列表
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus({ preventScroll: true }), 380);
    void refreshList();
    return () => window.clearTimeout(t);
  }, [open]);

  // 关抽屉 / 卸载：中断在途请求
  useEffect(() => {
    if (!open) abortPending();
  }, [open]);
  useEffect(() => () => abortPending(), []);

  // 新消息 → 滚到底
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open]);

  /** 发送问题：思考占位 → 持久化用户消息 → POST /api/ai/analysis/chat → 定稿并持久化回答 */
  const ask = async (raw: string) => {
    const text = raw.trim();
    if (!text || thinking || sendingRef.current) return;
    sendingRef.current = true;
    const thinkingId = uid();
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: 'user', content: text },
      { id: thinkingId, role: 'assistant', content: '', typing: true },
    ]);
    setInput('');
    const t = inputRef.current;
    if (t) t.style.height = '';

    const controller = new AbortController();
    abortPending();
    pendingRef.current = controller;

    // 首问自动建会话（标题=首问截断）；创建失败不阻断问答，仅本轮不落库
    let cid = convIdRef.current;
    const isNewConv = cid === null;
    if (isNewConv) {
      try {
        const conv = await createConversation({ title: text.slice(0, 30) });
        cid = conv.id;
        setConvId(cid);
        convIdRef.current = cid;
      } catch {
        cid = null;
      }
    }
    if (cid !== null) {
      try { await appendMessage(cid, 'user', text); } catch { /* 落库失败不阻断问答 */ }
    }

    try {
      const { answer, mode } = await analysisChat(
        {
          question: text,
          user_id: userId || undefined,
          context_meta: pageProjectCode
            ? { project_code: pageProjectCode, scene: 'admin' }
            : undefined,
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setMessages((prev) => prev.map((m) =>
        m.id === thinkingId ? { id: m.id, role: 'assistant' as const, content: answer, mode: mode as 'chat' | 'analysis' } : m));
      if (cid !== null) {
        try { await appendMessage(cid, 'assistant', answer, JSON.stringify({ mode })); } catch { /* 落库失败不阻断问答 */ }
      }
      if (isNewConv) void refreshList();
    } catch (err) {
      if (controller.signal.aborted || isKickingToLogin()) {
        // 主动中止（切换会话/新建/关抽屉）：撤下思考占位气泡
        setMessages((prev) => prev.filter((m) => m.id !== thinkingId));
        return;
      }
      const reason = err instanceof Error ? err.message : '未知错误';
      // 401/403：统一走登录流程；其余错误在气泡内给出原因，提示重发
      if (/(401|403)/.test(reason)) {
        kickToLogin('登录已过期，请重新登录');
        setMessages((prev) => prev.filter((m) => m.id !== thinkingId));
        return;
      }
      Toast({ message: `回答失败：${reason}`, theme: 'error' });
      setMessages((prev) => prev.map((m) =>
        m.id === thinkingId
          ? { id: m.id, role: 'assistant' as const, content: `⚠️ 回答失败：${reason}\n\n请稍后重新提问。` }
          : m));
    } finally {
      if (pendingRef.current === controller) pendingRef.current = null;
      sendingRef.current = false;
    }
  };

  const onSend = () => { void ask(input); };
  const onInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const t = e.target;
    setInput(t.value);
    // 单行自动增高，最高 ~5 行，超高出现内滚
    t.style.height = 'auto';
    t.style.height = `${Math.min(t.scrollHeight, 120)}px`;
  };
  const onInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void ask(input);
    }
  };

  // 离开后台管理区域（切到别的 Tab）或角色无权限（非超级管理员/开发者/部门负责人）不渲染
  if (!isAdminPath || !canAskData) return null;

  return (
    <>
      {/* 悬浮入口球：形制照抄转工单悬浮球，色相换深一号蓝；缓慢呼吸闪烁 */}
      <div
        className="ada-fab"
        title="问服务号数据"
        style={fabPos ? { left: fabPos.x, top: fabPos.y, right: 'auto', bottom: 'auto' } : undefined}
      >
        <button
          ref={fabRef}
          type="button"
          className="ada-fab__btn"
          aria-label="AI 问数据"
          onPointerDown={onFabPointerDown}
          onPointerMove={onFabPointerMove}
          onPointerUp={onFabPointerUp}
          onPointerCancel={onFabPointerUp}
          onClick={() => {
            if (fabDragRef.current.justDragged) { fabDragRef.current.justDragged = false; return; }
            setOpen(true);
          }}
        >
          <Bot size={20} strokeWidth={2} />
        </button>
        <span className="ada-fab__label">问数据</span>
      </div>

      {/* 聊天抽屉：桌面靠右 430px，窄屏自动全宽 */}
      <Popup
        visible={open}
        onClose={() => setOpen(false)}
        placement="right"
        showOverlay
        closeOnOverlayClick
        className="ada-pop"
        style={{ width: 'min(430px, 100vw)', height: '100%' }}
      >
        <div className="ada-drawer">
          {/* 头部 */}
          <div className="ada-head">
            <div className="ada-head__avatar">
              <Bot size={18} strokeWidth={2} />
            </div>
            <div className="ada-head__info">
              <div className="ada-head__title">AI 数据问答</div>
              <div className="ada-head__sub">服务号运营数据 · 报障 / 时效 / 用户 / 项目</div>
            </div>
            <button
              type="button"
              className={`ada-head__act${historyOpen ? ' is-active' : ''}`}
              title="历史会话"
              aria-label="历史会话"
              onClick={() => {
                setHistoryOpen(true);
                void refreshList();
              }}
            >
              <History size={17} strokeWidth={2} />
            </button>
            <button
              type="button"
              className="ada-head__act"
              title="新建会话"
              aria-label="新建会话"
              onClick={newConversation}
            >
              <MessageSquarePlus size={17} strokeWidth={2} />
            </button>
            <button
              type="button"
              className="ada-head__act"
              title="关闭"
              aria-label="关闭"
              onClick={() => setOpen(false)}
            >
              <X size={18} strokeWidth={2} />
            </button>
          </div>

          {/* 历史会话视图：列表（切换/删除）+ 底部新建；点击条目即恢复完整记录 */}
          {historyOpen ? (
            <div className="ada-history">
              <div className="ada-history__head">
                <span className="ada-history__title">历史会话</span>
                <button
                  type="button"
                  className="ada-history__back"
                  onClick={() => setHistoryOpen(false)}
                  aria-label="返回对话"
                >
                  返回
                </button>
              </div>
              <div className="ada-history__list">
                {conversations.length === 0 ? (
                  <div className="ada-history__empty">暂无历史会话</div>
                ) : conversations.map((conv) => (
                  <div
                    key={conv.id}
                    className={`ada-history__item ${conv.id === convId ? 'is-active' : ''}`}
                    onClick={() => void openConversation(conv.id)}
                  >
                    <div className="ada-history__main">
                      <div className="ada-history__title">{conv.title || '未命名会话'}</div>
                      <div className="ada-history__time">{formatDateTimeShort(conv.updated_at || conv.created_at)}</div>
                    </div>
                    <button
                      type="button"
                      className="ada-history__del"
                      aria-label="删除会话"
                      onClick={(e) => { e.stopPropagation(); setDeleting(conv); }}
                    >
                      <Trash2 size={14} strokeWidth={2} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="ada-history__footer">
                <button type="button" className="ada-history__new" onClick={newConversation}>
                  <MessageSquarePlus size={16} strokeWidth={2} />
                  新建会话
                </button>
              </div>
            </div>
          ) : (
            <>

          {/* 消息区：气泡复用摇人对话的 .chat-bubble 体系 */}
          <div className="ada-msgs" ref={listRef}>
            {messages.map((m) => (
              <div key={m.id} className={`chat-bubble-wrap is-${m.role === 'user' ? 'right' : 'left'}`}>
                <div className={`chat-bubble ${m.role === 'user' ? 'is-user' : 'is-ai'}`}>
                  {m.typing ? (
                    <span className="ada-typing" aria-label="AI 正在思考">
                      <i /><i /><i />
                    </span>
                  ) : m.role === 'user' ? (
                    <div className="chat-bubble__text">{m.content}</div>
                  ) : (
                    <>
                      {m.mode === 'analysis' && (
                        <div className="ada-bubble__tag">📊 数据分析</div>
                      )}
                      <MarkdownRenderer content={m.content} compact />
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* 空态推荐问题（用户提问一次后隐藏） */}
          {userTurnCount === 0 && (
            <div className="ada-chips">
              {CHIP_QUESTIONS.map((q) => (
                <button key={q} type="button" className="ada-chip" onClick={() => void ask(q)}>
                  <Sparkles size={12} strokeWidth={2} />
                  {q}
                </button>
              ))}
            </div>
          )}

          {/* 输入区 */}
          <div className="ada-input">
            <div className="ada-input__box">
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                placeholder="问点服务号数据，如：今天有多少新报障？"
                onChange={onInputChange}
                onKeyDown={onInputKeyDown}
              />
            </div>
            <button
              type="button"
              className="ada-send"
              aria-label="发送"
              disabled={thinking || !input.trim()}
              onClick={onSend}
            >
              <Send size={16} strokeWidth={2} />
            </button>
          </div>
            </>
          )}
        </div>
      </Popup>

      {/* 删除会话确认弹窗（底部，复用全局 .conv-dialog 样式） */}
      <Popup visible={!!deleting} onClose={() => setDeleting(null)} placement="bottom" showOverlay>
        <div className="conv-dialog">
          <p className="conv-dialog__msg">确定删除会话「{deleting?.title || '未命名'}」吗？删除后不可恢复。</p>
          <div className="conv-dialog__btns">
            <Button block theme="default" onClick={() => setDeleting(null)}>取消</Button>
            <Button block className="conv-delete-confirm-btn" onClick={() => void handleDeleteConfirm()}>删除</Button>
          </div>
        </div>
      </Popup>
    </>
  );
}
