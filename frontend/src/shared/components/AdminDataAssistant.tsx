// 后台管理「AI 数据助手」入口 —— UI 原型（本地演示假数据，未接任何真实接口）
//
// 设计对照：
//  - 悬浮球形制完全模仿「我要摇人」聊天页的转工单悬浮球（ChatPanel 内 .chat-panel__ticket-fab）：
//    52px 液态玻璃圆钮 + 常显小标签 + 可拖拽自由定位；差异点是色相换为深一号蓝（--blue-2）、呼吸闪烁放慢至 3.6s。
//  - 点开为右侧抽屉式聊天对话框（窄屏自动全宽），气泡样式复用全局 .chat-bubble 体系，与摇人对话观感一致。
//  - 问答内容为本地 canned 演示数据（标注「演示」），仅用于评审 UI 形态，后续接真实数据时替换 send 逻辑即可。
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Popup } from 'tdesign-mobile-react';
import { Bot, RotateCcw, Send, Sparkles, X } from 'lucide-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import './AdminDataAssistant.css';

/** 气泡 id 生成（纯本地，无需落库） */
const uid = (() => {
  let n = 0;
  return () => `ada-msg-${++n}-${Date.now().toString(36)}`;
})();

interface AdaMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  /** true = AI 正在「思考」（打字占位），内容定稿前不渲染 Markdown */
  typing?: boolean;
}

/** 演示问答库：keywords 与用户输入做包含匹配，命中数最多者胜出 */
interface DemoQA { id: string; keywords: string[]; answer: string }

const DEMO_QA: DemoQA[] = [
  {
    id: 'new-today',
    keywords: ['今天', '新增', '报障', '新单', '今日', '多少单'],
    answer: `截至今日 **17:30**，服务号收到客户报障 **12 单**：

| 环节 | 数量 |
| --- | --- |
| 已转工单 | 9 |
| 待接单 | 2 |
| 已解决 | 6 |

📈 报障高峰集中在 **10:00–11:00**（3 单），车型集中在 AGV-800 系列（5 单）。`,
  },
  {
    id: 'week-timely',
    keywords: ['本周', '超时', '处理', '时效', '响应', '积压', '进度'],
    answer: `本周（09-01 ~ 09-03）工单 **47 单**：已完成 31 · 进行中 13 · 挂起 3。

⏰ **超时预警 2 单**：
1. **#T20260903-018**「AGV 激光停障误报」—— 最晚解决时间已过 2h
2. **#T20260902-011**「货叉下降抖动」—— 今晚 20:00 前到期

⚡ 平均首响 **18 分钟**，平均解决 **6.2 小时**，整体时效较上周提升 12%。`,
  },
  {
    id: 'users',
    keywords: ['用户', '增长', '关注', '粉丝', '取关', '新增'],
    answer: `微信关注用户（T+1 口径，最新为昨日）：
- 昨日**净增 +23**（新增 28 / 取关 5）
- 近 7 天**净增 +141**，累计关注 **3,286** 人

菜单点击 Top3：**我要摇人** 38% · **历史工单** 24% · **项目交付** 17%。`,
  },
  {
    id: 'distribution',
    keywords: ['分布', '车型', '项目', '汇总', '分类', '哪类', 'top'],
    answer: `本月报障分布（按车型）：

| 车型 | 单量 | 占比 |
| --- | --- | --- |
| AGV-800 | 24 | 41% |
| AGV-500 | 17 | 29% |
| 叉车 AMR | 12 | 21% |
| 其他 | 5 | 9% |

建议关注 **AGV-800 激光导航** 类问题（占其故障 60%）。`,
  },
];

/** 空态推荐问题（与上方 demo 库一一对应） */
const CHIP_QUESTIONS = [
  '今天服务号有多少新报障？',
  '本周工单处理情况怎么样？',
  '服务号最近用户增长如何？',
  '本月报障集中在哪些车型？',
];

const WELCOME_TEXT = `你好，我是**后台数据助手** 👋 可以问我服务号的运营数据：报障工单、处理时效、用户增长、项目进展……

> 💡 当前为 **UI 原型**，回答为本地演示数据，暂未接真实数据源。`;

const FALLBACK_TEXT = `这个问题演示库暂时没有准备 😅
当前为 **UI 原型**（未接真实数据），可以先点点下面的问题体验交互形态；接真实数据后这里会变成任意问。`;

/** 匹配问答：返回命中最多的演示条目，无命中返回 null（走兜底话术） */
function pickDemoAnswer(text: string): DemoQA | null {
  let best: DemoQA | null = null;
  let bestHits = 0;
  for (const qa of DEMO_QA) {
    const hits = qa.keywords.reduce((n, k) => n + (text.includes(k) ? 1 : 0), 0);
    if (hits > bestHits) { bestHits = hits; best = qa; }
  }
  return bestHits > 0 ? best : null;
}

export default function AdminDataAssistant() {
  const { pathname } = useLocation();
  const isAdmin = pathname.startsWith('/admin');

  // ── 悬浮球（转工单同款拖拽：pointer 捕获，位移 >8px 视为移动并抑制点击） ──
  const fabRef = useRef<HTMLButtonElement>(null);
  const [fabPos, setFabPos] = useState<{ x: number; y: number } | null>(null);
  const fabDragRef = useRef({ active: false, moved: false, justDragged: false, startX: 0, startY: 0, baseX: 0, baseY: 0 });
  const FAB_SIZE = 52;
  const clampFabPos = (x: number, y: number) => ({
    x: Math.min(Math.max(8, x), window.innerWidth - FAB_SIZE - 8),
    // 底部避开三 Tab 导航（留 ~88px）
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
  const answerTimerRef = useRef<number | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 打开抽屉后聚焦输入框（等位移动画结束）
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => inputRef.current?.focus({ preventScroll: true }), 380);
    return () => window.clearTimeout(t);
  }, [open]);

  // 卸载清理未到期的「思考→回答」定时器
  useEffect(() => () => {
    if (answerTimerRef.current) window.clearTimeout(answerTimerRef.current);
  }, []);

  // 新消息 → 滚到底
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open]);

  const resetConversation = () => {
    if (answerTimerRef.current) window.clearTimeout(answerTimerRef.current);
    setMessages([{ ...welcomeMsg, id: uid() }]);
    setInput('');
    const t = inputRef.current;
    if (t) t.style.height = '';
  };

  /** 发送问题（本地演示：思考占位 ~1s 后给出 canned 回答） */
  const ask = (raw: string) => {
    const text = raw.trim();
    if (!text || thinking) return;
    const thinkingId = uid();
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: 'user', content: text },
      { id: thinkingId, role: 'assistant', content: '', typing: true },
    ]);
    setInput('');
    const t = inputRef.current;
    if (t) t.style.height = '';
    const qa = pickDemoAnswer(text);
    // 1s ~ 1.7s 随机延迟，模拟思考耗时，方便评审「思考中」占位 UI
    answerTimerRef.current = window.setTimeout(() => {
      setMessages((prev) => prev.map((m) => (m.id === thinkingId
        ? { id: m.id, role: 'assistant' as const, content: qa ? qa.answer : FALLBACK_TEXT }
        : m)));
    }, 900 + Math.random() * 700);
  };

  const onSend = () => ask(input);
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
      ask(input);
    }
  };

  // 离开后台管理区域（切到别的 Tab）不渲染
  if (!isAdmin) return null;

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
              <div className="ada-head__title">
                AI 数据问答
                <span className="ada-head__demo">UI 原型</span>
              </div>
              <div className="ada-head__sub">服务号运营数据 · 报障 / 时效 / 用户 / 项目</div>
            </div>
            <button
              type="button"
              className="ada-head__act"
              title="清空对话"
              aria-label="清空对话"
              onClick={resetConversation}
            >
              <RotateCcw size={16} strokeWidth={2} />
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
                    <MarkdownRenderer content={m.content} compact />
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* 空态推荐问题（用户提问一次后隐藏） */}
          {userTurnCount === 0 && (
            <div className="ada-chips">
              {CHIP_QUESTIONS.map((q) => (
                <button key={q} type="button" className="ada-chip" onClick={() => ask(q)}>
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
        </div>
      </Popup>
    </>
  );
}
