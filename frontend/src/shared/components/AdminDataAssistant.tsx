// 后台管理「AI 数据助手」入口 —— 悬浮球 + 聊天抽屉（含会话管理：新建 / 历史 / 删除）
//
// 设计对照：
//  - 悬浮球形制模仿「我要摇人」聊天页的转工单悬浮球（ChatPanel 内 .chat-panel__ticket-fab）：
//    52px 液态玻璃圆钮 + 常显小标签 + 可拖拽自由定位；差异点是色相换为深一号蓝（--blue-2）、
//    呼吸闪烁放慢至 3.6s。
//  - 点开为右侧抽屉式聊天对话框（窄屏自动全宽），气泡样式复用全局 .chat-bubble 体系，与摇人对话观感一致。
//  - 问答走真实接口：POST /api/ai/analysis/chat/agentic/stream（Agentic 自由对话，LLM 主导+工具调用，流式 SSE），
//    兼容三种模式：chat（普通聊天）、analysis（数据分析+图表/卡片）、clarify（澄清追问+候选按钮），
//    澄清多轮自动携带 conversation_id 关联上下文；流式协议：reasoning（思考过程，可选）→ meta（模式/图表/卡片先行）→ delta（逐块文本）→ done（附追问建议）。
//    后端无工具能力或异常时自动降级到原 /chat/stream 流程，前端无需感知。
//  - 会话持久化：独立表 dataqa_conversations/messages（/api/dataqa/*），与摇人对话库表完全隔离：
//    首问自动建会话（标题=首问截断）并逐轮落库；头部可新建会话、查看历史会话列表（恢复完整记录）
//    并可删除历史会话。
//  - 权限：入口可见性由权限码 frontend:dataqa:view（「后台管理-问数据按钮可见性」）控制——
//    在后台管理-其他-权限管理里定义、按角色在角色管理/分配角色里勾选：
//    授予该权限的角色可见，未授予（且非 admin 通配权限）整组件不渲染。
import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { Popup, Button, Toast } from 'tdesign-mobile-react';
import { Bot, Calendar, Hash, History, MessageSquarePlus, RotateCcw, Send, Sparkles, Square, Target, Trash2, X } from 'lucide-react';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import ReactECharts from '@/shared/components/ReactECharts';
import { analysisAgenticChatStream, type AnalysisCard, type AnalysisChart, type AnalysisPlan } from '@/api/analysis';
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
  /** 响应模式：chat / analysis / clarify */
  mode?: string;
  /** 解析出的分析计划（口径回显，analysis/clarify 模式均有值） */
  plan?: AnalysisPlan | null;
  /** clarify 模式下的候选选项，前端渲染为可点按钮 */
  suggestions?: string[];
  /** 用户主动停止思考后置位：clarify 候选按钮不再可点 */
  stopped?: boolean;
  /** 图表列表（analysis 模式，后端采集数据生成） */
  charts?: AnalysisChart[] | null;
  /** 单值指标卡片（analysis 模式，后端采集数据生成） */
  cards?: AnalysisCard[] | null;
  /** 统计范围标题（单项目时为项目名，作数据卡大标题） */
  scopeTitle?: string | null;
  /** 数据日期（具体年月日范围） */
  dateRange?: string | null;
  /** 思考过程（agentic 端点 reasoning 事件，可折叠展示，不落库） */
  reasoning?: string | null;
  /** 追问建议（agentic done 事件，渲染为「猜你想问」快捷按钮，不落库） */
  suggestQuestions?: string[];
}

/** 空态推荐问题（覆盖五大维度常用问法） */
const CHIP_QUESTIONS = [
  '本周工单处理情况',
  '近7天哪些项目的搬运效率为空',
  '项目信息填写率怎么样',
];

/** 指标 key → 中文标签映射（与后端 metric_registry.py 对齐，49 个指标） */
const METRIC_LABEL_MAP: Record<string, string> = {
  'ticket.total': '工单总数',
  'ticket.new_count': '新增工单数',
  'ticket.resolved_count': '已解决工单数',
  'ticket.closed_count': '已关闭工单数',
  'ticket.resolve_rate': '工单解决率',
  'ticket.overdue_count': '逾期工单数',
  'ticket.by_status': '工单状态分布',
  'ticket.by_priority': '工单优先级分布',
  'ticket.by_type': '工单类型分布',
  'ticket.new_by_day': '新增工单趋势',
  'ticket.overdue_list': '逾期工单明细',
  'ticket.items': '工单明细',
  'project.total': '项目总数',
  'project.active_count': '活跃项目数',
  'project.completed_count': '已完成项目数',
  'project.on_hold_count': '暂停项目数',
  'project.by_status': '项目状态分布',
  'project.items': '项目明细',
  'project.no_data_items': '无数据项目',
  'risk.assessment': '风险评估明细',
  'risk.by_level': '风险等级分布',
  'risk.high_risk_count': '高风险项目数',
  'collection.total_tasks': '总任务数',
  'collection.carry_task_count': '搬运任务数量',
  'collection.effective_work_hours': '有效工作时长',
  'collection.fault_hours': '机器人故障时长',
  'collection.idle_hours': '空闲无任务时间',
  'collection.avg_error_count': '平均错误次数',
  'collection.avg_fault_duration_minutes': '平均单次故障时间',
  'collection.avg_carry_duration_minutes': '平均单次搬运任务时间',
  'collection.avg_manual_switch_count': '平均切手动次数',
  'collection.manual_intervention_rate': '人工干预率',
  'collection.robot_group_compare': '各组数据对比',
  'collection.items': '采集数据明细',
  'project_info.node_total': '信息节点总数',
  'project_info.global_node_count': '全局模板节点数',
  'project_info.custom_node_count': '项目自定义节点数',
  'project_info.by_value_type': '字段值类型分布',
  'project_info.fill_rate': '信息填写率',
  'project_info.change_count': '信息变更次数',
  'project_info.change_by_day': '信息变更趋势',
  'project_info.change_by_type': '变更类型分布',
  'project_info.top_marked_nodes': '被关注最多的节点',
  'project_info.value_items': '已填字段值明细',
  'project_info.items': '项目信息完整度明细',
};

/** 指标 key 转中文标签；未注册的 key 直接返回原值 */
const metricLabel = (key: string) => METRIC_LABEL_MAP[key] || key;

/** 时间范围类型 → 中文 */
const TIME_RANGE_LABELS: Record<string, string> = {
  today: '今天',
  yesterday: '昨天',
  recent_days: '最近',
  this_week: '本周',
  last_week: '上周',
  this_month: '本月',
  last_month: '上月',
  custom: '自定义',
};

const WELCOME_TEXT = `你好，我是**后台数据助手** 👋 工单、项目、风险、搬运效率和项目信息都可以问我，例如：`;

/** 「问数据」按钮可见性权限码（后台管理-权限管理里维护，按角色勾选授予） */
const DATA_ASSISTANT_PERMISSION = 'frontend:dataqa:view';

export default function AdminDataAssistant() {
  const { pathname } = useLocation();
  const isAdminPath = pathname.startsWith('/admin');
  const permissions = useAuthStore((s) => s.permissions);
  const isAdmin = useAuthStore((s) => s.isAdmin);
  // 用户ID（users.id）：传给分析接口，分析意图时后端按该用户关联项目自动查库
  const userId = useAuthStore((s) => s.userId);
  // 页面上下文中的项目代码：从后台管理项目详情子页面路由中提取
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
  const [conversationId, setConversationId] = useState<string | null>(null);
  const thinking = messages.some((m) => m.typing);
  const lastMsg = messages[messages.length - 1];
  // 思考中出现澄清追问：视为同一思考过程（停止按钮仍可用，点候选继续思考链）
  const awaitingClarify = !!lastMsg && lastMsg.role === 'assistant' && lastMsg.mode === 'clarify' && !lastMsg.stopped;
  const inThought = thinking || awaitingClarify;
  const userTurnCount = messages.filter((m) => m.role === 'user').length;
  // 在途请求：发新问题 / 清空 / 关抽屉 / 卸载时 abort，杜绝迟到响应回写已关闭的对话框
  const pendingRef = useRef<AbortController | null>(null);
  const sendingRef = useRef(false); // 防双发（Enter + click 竞态）
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const abortPending = () => {
    if (pendingRef.current) {
      pendingRef.current.abort();
      pendingRef.current = null;
    }
  };

  // ── 会话管理状态（独立表 dataqa_conversations/messages，与摇人对话隔离） ──
  const [convId, setConvId] = useState<number | null>(null);
  const convIdRef = useRef<number | null>(null);
  const [conversations, setConversations] = useState<DataqaConversation[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [deleting, setDeleting] = useState<DataqaConversation | null>(null);

  /** 拉取当前用户的数据助手历史会话（后端按 updated_at 倒序） */
  const refreshList = async () => {
    try {
      setConversations(await listMyConversations(50));
    } catch { /* 会话列表拉取失败不打断问答 */ }
  };

  /** 新建会话：中断在途请求，回到空白新会话（首问发送时才真正落库） */
  const newConversation = () => {
    abortPending();
    sendingRef.current = false;
    setConvId(null);
    convIdRef.current = null;
    setMessages([{ ...welcomeMsg, id: uid() }]);
    setInput('');
    setHistoryOpen(false);
    setConversationId(null);
    const t = inputRef.current;
    if (t) t.style.height = '';
  };

  /** 切换到历史会话：中断在途请求，从 DB 恢复完整消息记录 */
  const openConversation = async (id: number) => {
    if (id === convIdRef.current) { setHistoryOpen(false); return; }
    abortPending();
    sendingRef.current = false;
    setHistoryOpen(false);
    // 切换会话时清空后端澄清会话上下文，避免旧 plan 污染新会话
    setConversationId(null);
    try {
      const conv = await getConversation(id);
      const restored: AdaMessage[] = (conv.messages ?? [])
        .filter((m) => (m.role === 'user' || m.role === 'assistant') && m.content.trim())
        .map((m) => {
          let mode: string | undefined;
          let charts: AnalysisChart[] | null = null;
          let cards: AnalysisCard[] | null = null;
          let scopeTitle: string | null = null;
          let dateRange: string | null = null;
          if (m.metadata_) {
            try {
              // metadata_ 可能被后端二次 JSON 编码（历史双重编码数据）：首次 parse
              // 得到字符串时再 parse 一次得到对象，否则 mode/charts/cards 全部丢失
              let meta: unknown = JSON.parse(m.metadata_);
              if (typeof meta === 'string') meta = JSON.parse(meta);
              if (meta && typeof meta === 'object') {
                const obj = meta as Record<string, unknown>;
                mode = typeof obj.mode === 'string' ? obj.mode : undefined;
                if (Array.isArray(obj.charts)) charts = obj.charts as AnalysisChart[];
                if (Array.isArray(obj.cards)) cards = obj.cards as AnalysisCard[];
                scopeTitle = typeof obj.scope_title === 'string' ? obj.scope_title : null;
                dateRange = typeof obj.date_range === 'string' ? obj.date_range : null;
              }
            } catch { /* 元数据损坏忽略 */ }
          }
          return { id: uid(), role: (m.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant', content: m.content, mode, charts, cards, scopeTitle, dateRange };
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

  const resetConversation = () => {
    abortPending();
    sendingRef.current = false;
    setMessages([{ ...welcomeMsg, id: uid() }]);
    setInput('');
    setConversationId(null);
    const t = inputRef.current;
    if (t) t.style.height = '';
  };

  /** 发送问题：思考占位 → 持久化用户消息 → POST /api/ai/analysis/chat/agentic/stream → 定稿并持久化回答
   *  - 兼容三种模式：chat（普通聊天）、analysis（数据分析+图表/卡片）、clarify（澄清追问+候选按钮）
   *  - 澄清多轮时自动携带 conversation_id 关联上下文；reasoning 思考过程折叠展示，done 追问建议渲染「猜你想问」 */
  const ask = async (raw: string, opts: { force?: boolean } = {}) => {
    const text = raw.trim();
    if (!text) return;
    // 上一条问题还在处理中：给出提示而不是静默丢弃（否则用户会以为“点了没反应”）
    // force：澄清候选续问（同一思考链，思考中出现澄清时允许点击继续）
    if (!opts.force && (thinking || sendingRef.current)) {
      Toast({ message: '上一条问题正在处理中，请稍候', theme: 'warning' });
      return;
    }
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
      // 流式问答：reasoning 先行（思考过程，可选）→ meta（模式/图表/卡片/口径）→ delta 逐块追加
      let answerAcc = '';
      let modeAcc: 'chat' | 'analysis' | 'clarify' = 'analysis';
      let planAcc: AnalysisPlan | null = null;
      let suggestionsAcc: string[] | undefined;
      let chartsAcc: AnalysisChart[] | null = null;
      let cardsAcc: AnalysisCard[] | null = null;
      let reasoningAcc: string | null = null;
      let suggestQuestionsAcc: string[] | undefined;
      let scopeTitleAcc: string | null = null;
      let dateRangeAcc: string | null = null;

      await analysisAgenticChatStream(
        {
          question: text,
          user_id: userId || undefined,
          context_meta: pageProjectCode
            ? { project_code: pageProjectCode, scene: 'admin' }
            : undefined,
          conversation_id: conversationId ?? undefined,
        },
        {
          onReasoning: (content) => {
            if (controller.signal.aborted) return;
            reasoningAcc = (reasoningAcc || '') + content;
            setMessages((prev) => prev.map((m) =>
              m.id === thinkingId ? { ...m, reasoning: reasoningAcc } : m));
          },
          onMeta: (meta) => {
            if (controller.signal.aborted) return;
            modeAcc = meta.mode;
            planAcc = meta.plan ?? null;
            suggestionsAcc = meta.suggestions ?? undefined;
            chartsAcc = meta.charts ?? null;
            cardsAcc = meta.cards ?? null;
            scopeTitleAcc = meta.scope_title ?? null;
            dateRangeAcc = meta.date_range ?? null;
            // 更新会话ID（首问/澄清时后端生成，后续轮次带上）
            if (meta.conversation_id) {
              setConversationId(meta.conversation_id);
            }
            setMessages((prev) => prev.map((m) =>
              m.id === thinkingId ? {
                ...m,
                mode: meta.mode,
                plan: meta.plan ?? null,
                suggestions: meta.suggestions ?? undefined,
                charts: meta.charts ?? null,
                cards: meta.cards ?? null,
                scopeTitle: meta.scope_title ?? null,
                dateRange: meta.date_range ?? null,
              } : m));
          },
          onDelta: (content) => {
            if (controller.signal.aborted) return;
            answerAcc += content;
            setMessages((prev) => prev.map((m) =>
              m.id === thinkingId ? { ...m, content: answerAcc, typing: false } : m));
          },
          onDone: (payload) => {
            // 追问建议（agentic 可选下发）：定稿时写入消息，渲染「猜你想问」
            if (!controller.signal.aborted && Array.isArray(payload.suggest_questions)
                && payload.suggest_questions.length > 0) {
              suggestQuestionsAcc = payload.suggest_questions;
            }
          },
        },
        controller.signal,
      );
      if (controller.signal.aborted) return;

      setMessages((prev) => prev.map((m) =>
        m.id === thinkingId ? {
          id: m.id,
          role: 'assistant' as const,
          content: answerAcc,
          mode: modeAcc,
          plan: planAcc,
          suggestions: suggestionsAcc,
          charts: chartsAcc,
          cards: cardsAcc,
          reasoning: reasoningAcc,
          suggestQuestions: suggestQuestionsAcc,
          scopeTitle: scopeTitleAcc,
          dateRange: dateRangeAcc,
        } : m));
      if (cid !== null) {
        try {
          await appendMessage(cid, 'assistant', answerAcc, JSON.stringify({
            mode: modeAcc,
            charts: chartsAcc,
            cards: cardsAcc,
            scope_title: scopeTitleAcc,
            date_range: dateRangeAcc,
          }));
        } catch { /* 落库失败不阻断问答 */ }
      }
      if (isNewConv) void refreshList();
    } catch (err) {
      if (controller.signal.aborted || isKickingToLogin()) {
        // 主动中止（切换会话/新建/关抽屉/停止思考/候选续问）：撤下思考占位气泡
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
      // 仅在本次请求仍持有在途标记时清理；若已被新请求接管（停止后重问/候选续问），不动共享状态
      if (pendingRef.current === controller) {
        pendingRef.current = null;
        sendingRef.current = false;
      }
    }
  };

  /** 停止思考：中止在途流；若停留在澄清环节则禁用候选按钮，允许重新提问 */
  const stopThought = () => {
    abortPending();
    sendingRef.current = false;
    setMessages((prev) => {
      const next = prev.filter((m) => !m.typing);
      const last = next[next.length - 1];
      if (last && last.role === 'assistant' && last.mode === 'clarify' && !last.stopped) {
        next[next.length - 1] = { ...last, stopped: true };
      }
      return next;
    });
    Toast({ message: '已停止思考，可以重新提问', theme: 'success' });
  };

  /** 思考中出现澄清追问时点击候选：中止当前流，立即以候选值继续同一思考链 */
  const continueThought = (s: string) => {
    abortPending();
    sendingRef.current = false;
    void ask(s, { force: true });
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
                      {/* 分析口径回显：analysis 模式且 plan 有值时显示 */}
                      {m.mode === 'analysis' && m.plan && m.plan.metric_keys.length > 0 && (
                        <div className="ada-plan-badge">
                          <div className="ada-plan-badge__row">
                            <Hash size={12} strokeWidth={2} />
                            <span>{m.plan.metric_keys.map(metricLabel).join(' · ')}</span>
                          </div>
                          <div className="ada-plan-badge__row">
                            <Calendar size={12} strokeWidth={2} />
                            <span>{m.plan.time_range.label || TIME_RANGE_LABELS[m.plan.time_range.type] || '全部时间'}</span>
                          </div>
                          <div className="ada-plan-badge__row">
                            <Target size={12} strokeWidth={2} />
                            <span>
                              {m.plan.scope.type === 'global' ? '全局范围' :
                               m.plan.scope.type === 'single_project' ? (m.plan.scope.project_name || m.plan.scope.project_code || '指定项目') :
                               '用户关联项目'}
                            </span>
                          </div>
                        </div>
                      )}
                      {/* 分析模式兜底标签：无 plan 时（如 data 场景）也给出模式提示 */}
                      {m.mode === 'analysis' && !(m.plan && m.plan.metric_keys.length > 0) && (
                        <div className="ada-bubble__tag">📊 数据分析</div>
                      )}
                      {/* 数据卡头部：项目名大标题（含具体数据日期，格式「项目名（日期范围）」）；
                          无项目名时单独展示数据日期行 */}
                      {m.mode === 'analysis'
                        && ((m.cards?.length ?? 0) > 0 || (m.charts?.length ?? 0) > 0)
                        && (m.scopeTitle || m.dateRange) && (
                        <div className="ada-data-head">
                          {m.scopeTitle && (
                            <div className="ada-data-head__title">
                              {m.scopeTitle}
                              {m.dateRange && (
                                <span className="ada-data-head__range">（{m.dateRange}）</span>
                              )}
                            </div>
                          )}
                          {!m.scopeTitle && m.dateRange && (
                            <div className="ada-data-head__date">
                              <Calendar size={11} strokeWidth={2} />
                              数据日期：{m.dateRange}
                            </div>
                          )}
                        </div>
                      )}
                      {/* 指标卡片：单值指标（解决率/总数等）大号数字展示 */}
                      {m.mode === 'analysis' && m.cards && m.cards.length > 0 && (
                        <div className="ada-cards">
                          {m.cards.map((c, i) => (
                            <div key={`${c.label}-${i}`} className="ada-card">
                              <div className="ada-card__label">{c.label}</div>
                              <div className={`ada-card__value${c.kind === 'metric' ? ' is-metric' : ''}`}>
                                {c.value}
                                {c.unit ? <span className="ada-card__unit">{c.unit}</span> : null}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      {/* 图表：分布（饼/柱）与趋势（折线），由后端采集数据生成；
                          多指标趋势图（全为折线且≥2张）时两列网格展示避免纵向过长，
                          单图不分组直接铺满整行 */}
                      {m.mode === 'analysis' && m.charts && m.charts.length > 0 && (
                        <div className={`ada-charts${m.charts.length > 1 && m.charts.every((c) => c.chart_type === 'line') ? ' ada-charts--grid' : ''}`}>
                          {m.charts.map((c, i) => (
                            <div key={`${c.title}-${i}`} className="ada-chart">
                              <div className="ada-chart__title">{c.title}</div>
                              <ReactECharts
                                option={c.option}
                                notMerge
                                style={{ height: 200 }}
                              />
                            </div>
                          ))}
                        </div>
                      )}
                      {/* 思考过程（agentic reasoning 事件）：折叠展示，默认收起 */}
                      {m.reasoning && (
                        <details className="ada-reasoning">
                          <summary>思考过程</summary>
                          <div className="ada-reasoning__body">{m.reasoning}</div>
                        </details>
                      )}
                      <MarkdownRenderer content={m.content} compact />
                      {/* clarify 候选按钮：仅最新一条且未被停止的 clarify 消息可点——历史 clarify 按钮
                          点击后会以旧上下文发起新一轮请求（后端缓存已随分析完成清除），
                          造成“点了又回到澄清”的困惑；思考中出现澄清视为同一思考，点击候选即继续 */}
                      {m.mode === 'clarify' && m.suggestions && m.suggestions.length > 0
                        && !m.stopped && m.id === messages[messages.length - 1].id && (
                        <div className="ada-clarify">
                          <div className="ada-clarify__hint">
                            {m.plan?.missing_fields?.includes('time_range') ? '请选择时间范围' :
                             m.plan?.missing_fields?.includes('project_code') ? '请选择项目范围' :
                             '请补充以下信息'}
                          </div>
                          <div className="ada-clarify__chips">
                            {m.suggestions.map((s) => (
                              <button
                                key={s}
                                type="button"
                                className="ada-clarify__chip"
                                onClick={() => void continueThought(s)}
                              >
                                <Sparkles size={11} strokeWidth={2} />
                                {s}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                      {/* 追问建议（agentic done 事件）：仅最新一条定稿消息渲染，点击直接提问 */}
                      {m.suggestQuestions && m.suggestQuestions.length > 0
                        && !m.typing && m.id === messages[messages.length - 1].id && (
                        <div className="ada-suggest">
                          <div className="ada-suggest__hint">猜你想问</div>
                          <div className="ada-suggest__chips">
                            {m.suggestQuestions.map((s) => (
                              <button
                                key={s}
                                type="button"
                                className="ada-suggest__chip"
                                onClick={() => void ask(s)}
                              >
                                <Sparkles size={11} strokeWidth={2} />
                                {s}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
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
            {/* 思考中（含澄清环节）：发送钮切换为停止思考钮，点击后中止并可重新提问 */}
            {inThought ? (
              <button
                type="button"
                className="ada-stop"
                aria-label="停止思考"
                title="停止思考"
                onClick={stopThought}
              >
                <Square size={14} strokeWidth={2} />
              </button>
            ) : (
              <button
                type="button"
                className="ada-send"
                aria-label="发送"
                disabled={!input.trim()}
                onClick={onSend}
              >
                <Send size={16} strokeWidth={2} />
              </button>
            )}
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
