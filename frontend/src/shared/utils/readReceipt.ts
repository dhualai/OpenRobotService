// 讨论区已读上报器（与 React / DOM 解耦的纯逻辑，便于单测）
//
// 背景：旧实现有三个致命问题
//  ① 进入讨论区那一帧就上报，此时 WS 还在 CONNECTING，帧被静默丢弃；
//  ② 「先标记为已上报、再发送」，发送失败后这批 id 永不再试；
//  ③ 语义是「滚到底部就把全部历史消息标记已读」——既漏报（划过未到底）
//     又误报（刚进页面一条没看就全已读）。
//
// 本实现：
//  - 视口可见 + 停留满 dwellMs 才算已读（飞书/IM 语义），快速甩动不会误标记；
//  - 未确认的 id 一直留在 pending，连接就绪 / 重连后由调用方 flush 补发；
//  - 只有 send() 返回「已写入 socket」才把 id 计入 confirmed，失败自动重试。

export interface ReadReporterOptions {
  /**
   * 真实发送。返回 true 表示已写入 socket（本实现据此把 id 计入 confirmed）；
   * 返回 false 表示仅入队/丢弃，调用方稍后应再次 flush。
   */
  send: (commentIds: number[], lastReadCommentId: number | null) => boolean;
  /** 气泡需在视口内停留多久才算已读（毫秒），默认 300 */
  dwellMs?: number;
  /** 收集到新 id 后延迟多久合并发送（毫秒），默认 400 */
  flushDelayMs?: number;
  /** 单帧最多携带多少条（与后端 MAX_COMMENT_IDS_PER_REQUEST 对齐），默认 500 */
  maxBatch?: number;
}

/** 已读上报器：把「视口内停留确认过的评论」可靠地送到服务端。 */
export class ReadReporter {
  private readonly send: (commentIds: number[], lastReadCommentId: number | null) => boolean;
  private readonly dwellMs: number;
  private readonly flushDelayMs: number;
  private readonly maxBatch: number;

  /** 已成功送达服务端的评论 id（不再重复上报） */
  private confirmed = new Set<number>();
  /** 已被标记已读、但尚未送达的评论 id（连接就绪后补发） */
  private pending = new Set<number>();
  /** 进入视口后等待 dwell 确认的定时器 */
  private dwellTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  /** 是否已被销毁（destroy 后不再触发任何回调） */
  private destroyed = false;

  constructor(options: ReadReporterOptions) {
    this.send = options.send;
    this.dwellMs = options.dwellMs ?? 300;
    this.flushDelayMs = options.flushDelayMs ?? 400;
    this.maxBatch = options.maxBatch ?? 500;
  }

  /** 评论气泡进入视口：启动停留计时，满 dwellMs 才算已读 */
  enterViewport(id: number): void {
    if (this.destroyed || !this.isValidId(id)) return;
    if (this.confirmed.has(id) || this.dwellTimers.has(id)) return;
    const timer = setTimeout(() => {
      this.dwellTimers.delete(id);
      this.addPending(id);
    }, this.dwellMs);
    this.dwellTimers.set(id, timer);
  }

  /** 评论气泡离开视口：撤销停留计时（停留时长不够，不算已读） */
  leaveViewport(id: number): void {
    const timer = this.dwellTimers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      this.dwellTimers.delete(id);
    }
  }

  /** 直接标记一组评论为已读（跳过停留判定），用于「跳到底部」等明确语义的场景 */
  markRead(ids: readonly number[]): void {
    if (this.destroyed) return;
    let dirty = false;
    for (const id of ids) {
      if (this.isValidId(id) && !this.confirmed.has(id)) {
        this.pending.add(id);
        dirty = true;
      }
    }
    if (dirty) this.scheduleFlush();
  }

  /** 把未送达的已读 id 发送出去；发送成功才计入 confirmed。 */
  flush(): void {
    if (this.destroyed) return;
    this.clearFlushTimer();

    const toSend = Array.from(this.pending)
      .filter((id) => !this.confirmed.has(id))
      .sort((a, b) => a - b);
    if (toSend.length === 0) return;

    const batch = toSend.slice(0, this.maxBatch);
    const lastReadCommentId = batch[batch.length - 1] ?? null;

    let ok = false;
    try {
      ok = this.send(batch, lastReadCommentId);
    } catch {
      ok = false;
    }
    if (!ok) {
      // 未送达：留在 pending，等连接就绪后由调用方再次 flush
      return;
    }
    for (const id of batch) {
      this.confirmed.add(id);
      this.pending.delete(id);
    }
    // 还有剩余（超过单帧上限）→ 继续排下一批
    if (toSend.length > batch.length) this.scheduleFlush();
  }

  /**
   * 外部确认已送达（REST 兜底通道异步成功后调用）。
   * 计入 confirmed 并移出 pending，避免 WS 恢复后重复上报。
   */
  confirm(ids: readonly number[]): void {
    if (this.destroyed) return;
    for (const id of ids) {
      if (!this.isValidId(id)) continue;
      this.confirmed.add(id);
      this.pending.delete(id);
      const timer = this.dwellTimers.get(id);
      if (timer !== undefined) {
        clearTimeout(timer);
        this.dwellTimers.delete(id);
      }
    }
  }

  /** 待发送条数（pending 中尚未确认的） */
  pendingCount(): number {
    let n = 0;
    for (const id of this.pending) {
      if (!this.confirmed.has(id)) n += 1;
    }
    return n;
  }

  /** 已确认送达条数 */
  confirmedCount(): number {
    return this.confirmed.size;
  }

  /** 是否曾确认送达过指定评论（测试/诊断用） */
  hasConfirmed(id: number): boolean {
    return this.confirmed.has(id);
  }

  /** 销毁：清理所有定时器。切换会话（taskId）时调用，避免跨工单串数据。 */
  destroy(): void {
    this.destroyed = true;
    this.clearFlushTimer();
    for (const timer of this.dwellTimers.values()) clearTimeout(timer);
    this.dwellTimers.clear();
    this.pending.clear();
    this.confirmed.clear();
  }

  private isValidId(id: unknown): id is number {
    return typeof id === 'number' && Number.isFinite(id) && id > 0;
  }

  private addPending(id: number): void {
    if (this.confirmed.has(id)) return;
    this.pending.add(id);
    this.scheduleFlush();
  }

  private scheduleFlush(): void {
    if (this.destroyed || this.flushTimer !== null) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this.flush();
    }, this.flushDelayMs);
  }

  private clearFlushTimer(): void {
    if (this.flushTimer !== null) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
  }
}
