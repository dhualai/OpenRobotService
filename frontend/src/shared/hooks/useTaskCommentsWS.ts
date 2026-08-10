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
import { TaskRoomSocket, type CommentPayload, type WsEvent } from '@/api/ws';
import type { DiscussionComment } from '@/shared/components/DiscussionPanel';

export interface TaskUpdatedPatch {
  status?: string;
  assigned_to?: string | null;
  assigned_to_name?: string | null;
}

interface Options {
  currentUser?: string;
  onTaskUpdated?: (patch: TaskUpdatedPatch) => void;
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
  online: string[];
  typingUser: string | null;
  readMap: Record<string, number>;
  sendTyping: (value: boolean) => void;
  sendRead: (lastReadCommentId: number) => void;
} {
  const [displayComments, setDisplayComments] = useState<DiscussionComment[]>(baseComments);
  const [online, setOnline] = useState<string[]>([]);
  const [typingUser, setTypingUser] = useState<string | null>(null);
  const [readMap, setReadMap] = useState<Record<string, number>>({});

  const socketRef = useRef<TaskRoomSocket | null>(null);
  const currentUserRef = useRef(options?.currentUser);
  currentUserRef.current = options?.currentUser;
  const onTaskUpdatedRef = useRef(options?.onTaskUpdated);
  onTaskUpdatedRef.current = options?.onTaskUpdated;

  // 父级基线变化（GET/POST 乐观更新）→ 合并 WS 增量（按 id 去重），保持排序
  useEffect(() => {
    setDisplayComments((prev) => {
      const map = new Map<string, DiscussionComment>();
      for (const c of baseComments) map.set(String(c.id), c);
      for (const c of prev) {
        if (!map.has(String(c.id))) map.set(String(c.id), c);
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
        case 'comment.deleted':
          setDisplayComments((prev) => prev.filter((c) => String(c.id) !== String(e.id)));
          break;
        case 'typing':
          if (e.username && e.username !== currentUserRef.current) {
            setTypingUser(e.value ? e.username : null);
          }
          break;
        case 'read_receipt':
          setReadMap((r) => ({ ...r, [e.username]: e.last_read_comment_id }));
          break;
        case 'task.updated':
          onTaskUpdatedRef.current?.({
            status: e.status,
            assigned_to: e.assigned_to,
            assigned_to_name: e.assigned_to_name,
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

  const sendRead = useCallback((lastReadCommentId: number) => {
    socketRef.current?.sendRead(lastReadCommentId);
  }, []);

  return { displayComments, online, typingUser, readMap, sendTyping, sendRead };
}
