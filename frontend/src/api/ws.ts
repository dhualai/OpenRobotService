// 工单/任务评论区 WebSocket 客户端封装（轻量 IM 模式）
//
// 端点：/api/tasks/{task_id}/ws?token=<JWT>（同 REST 前缀，仅协议换 ws）
// 鉴权：token 走 query（浏览器原生 WebSocket 不支持自定义 Header），全程 wss。
// 能力：评论 CRUD 实时推送、在线状态(presence)、输入中(typing)、已读回执(read_receipt)、
//       工单状态变更(task.updated)。
// 设计：自动重连（指数退避）+ 心跳 ping/pong；断线期间消息由重连后的全量 GET 对齐兜底。

import { getToken } from '@/api/client';
import API_CONFIG from '@/config/api';

/** 由 REST base（/api/tasks 等）推导 WS URL（协议 http→ws，https→wss） */
export function buildWsUrl(path: string): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const token = getToken() || '';
  const sep = path.includes('?') ? '&' : '?';
  return `${proto}://${location.host}${path}${sep}token=${encodeURIComponent(token)}`;
}

export interface OnlineMember {
  username: string;
  name?: string | null;
  avatar_resource_id?: number | null;
}

/** 单条评论的一条已读明细（飞书式名单成员） */
export interface ReadRecord {
  username: string;
  name?: string | null;
  avatar_resource_id?: number | null;
  read_at?: string | null;
}

/** read_receipt 增量广播里的单条已读明细（含所属评论 id） */
export interface ReadRecordDelta extends ReadRecord {
  comment_id: number;
}

export type WsEvent =
  | { type: 'welcome'; you: string; online: OnlineMember[]; read_map: Record<string, number>; read_records?: Record<string, ReadRecord[]> }
  | { type: 'comment.created'; comment: CommentPayload }
  | { type: 'comment.updated'; comment: CommentPayload }
  | { type: 'comment.deleted'; id: number }
  | { type: 'presence'; online: OnlineMember[] }
  | { type: 'typing'; username: string; value: boolean }
  | { type: 'read_receipt'; username: string; last_read_comment_id: number | null; comment_ids?: number[]; records?: ReadRecordDelta[] }
  | { type: 'task.updated'; task_id: number; status?: string; assigned_to?: string | null; assigned_to_name?: string | null; updated_at?: string | null }
  | { type: 'ai.progress'; run_id?: string; phase: 'running' | 'done'; todos: AiProgressTodo[] }
  | { type: 'pong' }
  | { type: 'error'; code?: number; message?: string };

/** AI 执行过程单项（Supervisor 派发能力时逐项推送，Claude Code 式动态展示） */
export interface AiProgressTodo {
  id?: number;
  capability?: string;
  description?: string;
  status?: 'pending' | 'in_progress' | 'completed' | string;
  phase?: 'running' | 'done' | string;
}

export interface CommentPayload {
  id: number;
  ticket_id?: number;
  content: string;
  is_public?: boolean;
  attachments?: unknown[];
  created_by: string;
  created_by_name?: string;
  created_at: string;
  updated_at?: string;
  reply_to?: number | null;
  quoted?: { id: number | string; content: string; created_by_name?: string } | null;
}

type Handler = (e: WsEvent) => void;

export class TaskRoomSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers = new Set<Handler>();
  private closedByUser = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectAttempts = 0;
  private pingDeadline: ReturnType<typeof setTimeout> | null = null;
  /** 待发队列：连接未就绪时用 sendReliable 投递的帧，onopen 后按序补发。
   *  已读回执必须可靠送达 —— 旧实现在 WS CONNECTING 时静默丢弃，导致
   *  「进入讨论区那一帧的已读上报永久丢失」。 */
  private outbox: string[] = [];
  /** onopen 回调（区别于 on()：后者是服务端下发的消息） */
  private openHandlers = new Set<() => void>();
  /** 待发队列上限：长期断网时避免无限堆积（已读帧会被节流合并，正常远小于此） */
  private static readonly MAX_OUTBOX = 50;

  constructor(taskId: string | number) {
    // 后端路由：/api/tasks/{task_id}/ws（task_router 在 /api/tasks 前缀下）
    this.url = buildWsUrl(`${API_CONFIG.TASKS.BASE_URL}/${taskId}/ws`);
  }

  connect(): void {
    this.closedByUser = false;
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.startHeartbeat();
      this.flushOutbox();
      this.openHandlers.forEach((h) => h());
    };
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as WsEvent;
        if (data.type === 'pong') {
          // 收到 pong → 清除等待定时器，避免掉线误判与定时器泄漏
          if (this.pingDeadline) {
            clearTimeout(this.pingDeadline);
            this.pingDeadline = null;
          }
          return;
        }
        this.handlers.forEach((h) => h(data));
      } catch {
        /* 忽略非法帧 */
      }
    };
    this.ws.onclose = () => {
      this.stopHeartbeat();
      if (!this.closedByUser) this.scheduleReconnect();
    };
    this.ws.onerror = () => {
      // onclose 会随后触发，统一在 onclose 处理重连
      this.ws?.close();
    };
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' });
      // 清除上一轮未触发的 pingDeadline，避免定时器泄漏堆积
      if (this.pingDeadline) clearTimeout(this.pingDeadline);
      // 60s 未收到 pong 视为掉线，主动关闭触发重连
      this.pingDeadline = setTimeout(() => {
        this.ws?.close();
      }, 60000);
    }, 25000);
  }

  private stopHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    if (this.pingDeadline) clearTimeout(this.pingDeadline);
    this.heartbeatTimer = null;
    this.pingDeadline = null;
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 10000);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  on(handler: Handler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** 注册「连接已就绪」回调（每次 onopen 都会触发，含断线重连） */
  onOpen(handler: () => void): () => void {
    this.openHandlers.add(handler);
    return () => this.openHandlers.delete(handler);
  }

  /** 连接是否已可写 */
  isOpen(): boolean {
    return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
  }

  /** 尽力而为发送，返回是否真的写入了 socket（未连接则丢弃，不入队）。
   *  适用于 typing / ping 这类「过期即无意义」的帧。 */
  send(obj: unknown): boolean {
    if (this.isOpen()) {
      this.ws!.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  /** 可靠发送：未连接时入队，onopen 后按序补发。用于已读回执等必须送达的帧。
   *  返回 true 表示已写入 socket；false 表示已入队（稍后自动补发）或队列已满被丢弃。 */
  sendReliable(obj: unknown): boolean {
    if (this.isOpen()) {
      this.ws!.send(JSON.stringify(obj));
      return true;
    }
    if (this.closedByUser) return false;
    // 队列上限保护：极端断网场景避免无限堆积
    if (this.outbox.length >= TaskRoomSocket.MAX_OUTBOX) {
      this.outbox.shift();
    }
    this.outbox.push(JSON.stringify(obj));
    return false;
  }

  /** 待发队列长度（测试/诊断用） */
  pendingCount(): number {
    return this.outbox.length;
  }

  private flushOutbox(): void {
    if (!this.outbox.length || !this.isOpen()) return;
    const frames = this.outbox;
    this.outbox = [];
    for (const frame of frames) {
      try {
        this.ws!.send(frame);
      } catch {
        // socket 在补发过程中被关闭：剩余帧回队，等下次重连再发
        this.outbox.push(frame);
      }
    }
  }

  sendTyping(value: boolean): boolean {
    return this.send({ type: 'typing', value });
  }

  /** 已读上报：可靠投递（未连接时入队，建连/重连后自动补发）。
   *  返回 true 表示已写入 socket，false 表示已入队待补发。 */
  sendRead(lastReadCommentId: number, commentIds?: number[]): boolean {
    return this.sendReliable({
      type: 'read',
      last_read_comment_id: lastReadCommentId,
      comment_ids: commentIds,
    });
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.stopHeartbeat();
    this.outbox = [];
    this.ws?.close();
    this.handlers.clear();
    this.openHandlers.clear();
  }
}
