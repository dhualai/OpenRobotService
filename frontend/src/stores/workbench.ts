// 工作台跨视图数据流转中枢
// 三视图（我要摇人 / 系统任务 / 后台管理）之间的联动状态都集中在这里，
// 通过 goToTab 统一入口携带 payload，实现"用户交互驱动数据流转"。
import { create } from 'zustand';

export type WorkbenchTab = 'call' | 'tasks' | 'admin';

/** AI 打包生成、待写入工单的草稿（CallView → TasksView） */
export interface TicketDraft {
  title: string;
  description: string;
  ticket_type?: string;
  priority?: string;
  project_id?: string;
  /** 来源对话 id，便于回溯（支持新版字符串 session_id 和旧版数字 id） */
  source_conversation_id?: string | number;
}

/** 工单讨论带入对话的上下文（TasksView → CallView） */
export interface ChatContext {
  ticketId: string;
  title: string;
  description?: string;
}

export interface WorkbenchState {
  /** 当前激活的视图 */
  activeTab: WorkbenchTab;
  /** 待预填的工单草稿，TasksView 消费后置空 */
  ticketDraft: TicketDraft | null;
  /** 待注入对话的工单上下文，CallView 消费后置空 */
  chatContext: ChatContext | null;
  /** 工单列表刷新触发器，+1 即触发 TasksView 重新拉取 */
  tasksRefreshKey: number;
  /** 联动选中的工单 id，建单/跳转后自动展开详情 */
  selectedTicketId: string | null;

  setActiveTab: (tab: WorkbenchTab) => void;
  goToTab: (tab: WorkbenchTab, payload?: {
    ticketDraft?: TicketDraft;
    chatContext?: ChatContext;
    selectedTicketId?: string;
  }) => void;

  consumeTicketDraft: () => TicketDraft | null;
  consumeChatContext: () => ChatContext | null;
  setTicketDraft: (draft: TicketDraft | null) => void;
  setChatContext: (ctx: ChatContext | null) => void;
  setSelectedTicketId: (id: string | null) => void;
  refreshTasks: () => void;
}

export const useWorkbenchStore = create<WorkbenchState>((set, get) => ({
  activeTab: 'call',
  ticketDraft: null,
  chatContext: null,
  tasksRefreshKey: 0,
  selectedTicketId: null,

  setActiveTab: (tab) => set({ activeTab: tab }),

  goToTab: (tab, payload) =>
    set({
      activeTab: tab,
      ...(payload?.ticketDraft !== undefined ? { ticketDraft: payload.ticketDraft } : {}),
      ...(payload?.chatContext !== undefined ? { chatContext: payload.chatContext } : {}),
      ...(payload?.selectedTicketId !== undefined
        ? { selectedTicketId: payload.selectedTicketId }
        : {}),
    }),

  consumeTicketDraft: () => {
    const draft = get().ticketDraft;
    if (draft) set({ ticketDraft: null });
    return draft;
  },

  consumeChatContext: () => {
    const ctx = get().chatContext;
    if (ctx) set({ chatContext: null });
    return ctx;
  },

  setTicketDraft: (draft) => set({ ticketDraft: draft }),
  setChatContext: (ctx) => set({ chatContext: ctx }),
  setSelectedTicketId: (id) => set({ selectedTicketId: id }),
  refreshTasks: () => set((s) => ({ tasksRefreshKey: s.tasksRefreshKey + 1 })),
}));
