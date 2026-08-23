// useTaskCommentsWS —— 工单/任务评论区实时订阅 hook
//
// 职责：
//  - 建立并维护 TaskRoomSocket（自动重连 + 心跳）；
//  - 把「父级基线 comments（GET/POST 乐观更新）」与「WS 增量事件」合并为 displayComments，按 id 去重；
//  - 暴露在线成员(online)、输入中用户(typingUser)、已读游标(readMap)；
//  - 透传 task.updated 事件给父级 onTaskUpdated（用于替代派单轮询）。
//
// 去重原则：评论以服务端 id 为唯一键，自己发评论（乐观更新用真实 id）与 WS 广播到达时
// 因 id 已存在而被忽略/更新，天然不重复。

import { useCallback, useEffect, useRef, useState } from 'react';
import { TaskRoomSocket, type CommentPayload, type WsEvent, type AiProgressTodo, type ReadRecord } from '@/api/ws';
import type { DiscussionComment } from '@/shared/components/DiscussionPanel';

export interface TaskUpdatedPatch {
  status?: string;
  assigned_to?: string | null;
  assigned_to_name?: string | null;
}

/** 在线成员（按用户去重，含头像） */
export interface OnlineMember {
  username: string;
  name?: string | null;
  avatar_resource_id?: number | null;
}

interface Options {
  currentUser?: string;
  onTaskUpdated?: (patch: TaskUpdatedPatch) => void;
  /** AI 执行过程实时进度（Claude Code 式动态展示）；phase=done 收尾时前端收起过程区 */
  onAiProgress?: (ev: { run_id?: string; phase: 'running' | 'done'; todos: AiProgressTodo[] }) => void;
}

const sortComments = (list: DiscussionComment[]): DiscussionComment[] =>
  [...list].sort((a, b) => {
    const na = Number(a.id);
    const nb = Number(b.id);
    if (!Number.isNaN(na) && !Number.isNaN(nb)) return na - nb;
    return String(a.created_at).localeCompare(String(b.created_at));
  });

export function useTaskCommentsWS(
  taskId: string | number | undefined,
  baseComments: DiscussionComment[],
  options?: Options,
): {
  displayComments: DiscussionComment[];
  online: OnlineMember[];
  typingUser: string | null;
  readMap: Record<string, number>;
  sendTyping: (value: boolean) => void;
  sendRead: (lastReadCommentId: number, commentIds?: number[]) => void;
  readRecords: Record<string, ReadRecord[]>;
  deletedIds: Set<string>;
} {
  const [displayComments, setDisplayComments] = useState<DiscussionComment[]>(baseComments);
  const [online, setOnline] = useState<OnlineMember[]>([]);
  const [typingUser, setTypingUser] = useState<string | null>(null);
  const [readMap, setReadMap] = useState<Record<string, number>>({});
  // 每条评论的已读名单（飞书式）：comment_id -> [{username,name,avatar_resource_id,read_at}]
  const [readRecords, setReadRecords] = useState<Record<string, ReadRecord[]>>({});
  // 已删除评论 id 集合（WS comment.deleted / 本地删除记录）。
  // 同步维护 ref（合并基线时用，避免闭包旧值）+ state（驱动引用块「已删除」展示重渲染）。
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set());
  const deletedIdsRef = useRef<Set<string>>(new Set());

  const socketRef = useRef<TaskRoomSocket | null>(null);
  const currentUserRef = useRef(options?.currentUser);
  currentUserRef.current = options?.currentUser;
  const onTaskUpdatedRef = useRef(options?.onTaskUpdated);
  const onAiProgressRef = useRef(options?.onAiProgress);
  onAiProgressRef.current = options?.onAiProgress;

  // 父级基线变化（GET/POST/DELETE 乐观更新）→ 以基线为权威源合并 WS 增量：
  // 基线已有的按基线更新；基线没有但 WS 新增的保留；已删除的（deletedIdsRef）一律排除。
  useEffect(() => {
    setDisplayComments((prev) => {
      const map = new Map<string, DiscussionComment>();
      for (const c of baseComments) {
        const id = String(c.id);
        if (!deletedIdsRef.current.has(id)) map.set(id, c);
      }
      // 补充 WS 增量新增的（基线没有的），排除已删除
      for (const c of prev) {
        const id = String(c.id);
        if (!map.has(id) && !deletedIdsRef.current.has(id)) map.set(id, c);
      }
      return sortComments(Array.from(map.values()));
    });
  }, [baseComments]);

  useEffect(() => {
    if (!taskId) {
      // 未启用 WS：直接以基线展示
      setDisplayComments(baseComments);
      return;
    }
    const sock = new TaskRoomSocket(taskId);
    socketRef.current = sock;

    const off = sock.on((e: WsEvent) => {
      switch (e.type) {
        case 'welcome':
          setOnline(e.online || []);
          setReadMap(e.read_map || {});
          setReadRecords(e.read_records || {});
          break;
        case 'presence':
          setOnline(e.online || []);
          break;
        case 'comment.created':
        case 'comment.updated': {
          const incoming = e.comment as unknown as DiscussionComment;
          const id = String(incoming.id);
          setDisplayComments((prev) => {
            const exists = prev.some((c) => String(c.id) === id);
            const next = exists
              ? prev.map((c) => (String(c.id) === id ? ({ ...c, ...incoming } as DiscussionComment) : c))
              : [...prev, incoming];
            return sortComments(next);
          });
          break;
        }
        case 'comment.deleted': {
          const delId = String(e.id);
          deletedIdsRef.current.add(delId);
          setDeletedIds((prev) => (prev.has(delId) ? prev : new Set(prev).add(delId)));
          setDisplayComments((prev) => prev.filter((c) => String(c.id) !== delId));
          break;
        }
        case 'typing':
          if (e.username && e.username !== currentUserRef.current) {
            setTypingUser(e.value ? e.username : null);
          }
          break;
        case 'read_receipt':
          if (typeof e.last_read_comment_id === 'number') {
            setReadMap((r) => ({ ...r, [e.username]: e.last_read_comment_id as number }));
          }
          // 名单增量：将本次广播的 records 合并进对应 comment_id 的名单（按 username 去重）
          if (e.records && e.records.length > 0) {
            setReadRecords((prev) => {
              const next: Record<string, ReadRecord[]> = {};
              for (const key of Object.keys(prev)) next[key] = prev[key];
              for (const rec of e.records!) {
                const cid = String(rec.comment_id);
                const existing = next[cid] || [];
                if (existing.some((x) => x.username === rec.username)) continue;
                next[cid] = [...existing, {
                  username: rec.username,
                  name: rec.name,
                  avatar_resource_id: rec.avatar_resource_id,
                  read_at: rec.read_at ?? new Date().toISOString(),
                }];
              }
              return next;
            });
          }
          break;
        case 'task.updated':
          onTaskUpdatedRef.current?.({
            status: e.status,
            assigned_to: e.assigned_to,
            assigned_to_name: e.assigned_to_name,
          });
          break;
        case 'ai.progress':
          onAiProgressRef.current?.({
            run_id: e.run_id,
            phase: e.phase,
            todos: e.todos || [],
          });
          break;
        default:
          break;
      }
    });

    sock.connect();
    return () => {
      off();
      sock.close();
    };
  }, [taskId]);

  const sendTyping = useCallback((value: boolean) => {
    socketRef.current?.sendTyping(value);
  }, []);

  const sendRead = useCallback((lastReadCommentId: number, commentIds?: number[]) => {
    socketRef.current?.sendRead(lastReadCommentId, commentIds);
  }, []);

  return { displayComments, online, typingUser, readMap, sendTyping, sendRead, readRecords, deletedIds };
}
