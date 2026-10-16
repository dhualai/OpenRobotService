// 工单点赞状态（详情页点赞按钮的数据层）
//
// 「精品工单 TOP 榜」按 task_likes 计数排序，详情页点赞即其计数来源。
//   - 进入详情拉一次「总数 + 我是否点过」；拉取失败静默（点赞是增强项，不阻塞详情阅读）
//   - toggle 乐观更新，失败回滚；pending 期间不允许再次提交（防连点）
// 抽成 hook 而非写在 TaskDetailPage 里：详情页依赖过重不便单测，数据层单独可测。
import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchTaskLike, toggleTaskLike, type TaskLikeState } from '@/api/taskLike';

export interface UseTaskLikeResult {
  /** null = 尚未取到（任务不存在 / 首次加载中 / 拉取失败），此时不渲染点赞按钮 */
  state: TaskLikeState | null;
  pending: boolean;
  /** 返回操作后的期望状态（true=已点赞 / false=已取消）；失败返回 null 且状态已回滚 */
  toggle: () => Promise<boolean | null>;
}

export function useTaskLike(taskId: string | number | null | undefined): UseTaskLikeResult {
  const [state, setState] = useState<TaskLikeState | null>(null);
  const [pending, setPending] = useState(false);

  // state/pending 用 ref 镜像：toggle 里 await 之后不能再读闭包里的旧值
  const stateRef = useRef<TaskLikeState | null>(null);
  const pendingRef = useRef(false);
  stateRef.current = state;

  useEffect(() => {
    const id = taskId ?? '';
    if (id === '') { setState(null); return; }
    let alive = true;
    fetchTaskLike(id)
      .then((s) => { if (alive) setState(s); })
      .catch(() => { /* 静默降级：失败则不渲染点赞按钮 */ });
    return () => { alive = false; };
  }, [taskId]);

  const toggle = useCallback(async (): Promise<boolean | null> => {
    const id = taskId ?? '';
    const current = stateRef.current;
    if (id === '' || current === null || pendingRef.current) return null;

    pendingRef.current = true;
    setPending(true);

    const nextLiked = !current.liked;
    setState({ liked: nextLiked, like_count: Math.max(0, current.like_count + (nextLiked ? 1 : -1)) });

    const res = await toggleTaskLike(id, nextLiked);
    setState(res ?? current);

    pendingRef.current = false;
    setPending(false);
    return res ? res.liked : null;
  }, [taskId]);

  return { state, pending, toggle };
}
