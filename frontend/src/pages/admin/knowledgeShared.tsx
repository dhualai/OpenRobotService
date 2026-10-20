import { useEffect, useRef, useState } from 'react';
import { useAuthStore } from '@/stores/auth';
import AvatarImg from '@/shared/components/AvatarImg';
import { avatarUrl } from '@/api/profile';
import { ApiError } from '@/api/client';
import type { KnowledgeUser } from '@/api/knowledge';
import '@/shared/styles/knowledge.css';

export const knowledgeLikeRule = '每人每天最多点赞5个不同问题；北京时间每日0点重置，取消点赞不返还当日名额。';
export const knowledgeCampaignMonths = ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12'];

export function useKnowledgeResource<Value>(key: string, loader: () => Promise<Value>) {
  const username = useAuthStore((state) => state.username);
  const scopedKey = `${username}:${key}`;
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const [version, setVersion] = useState(0);
  const [state, setState] = useState<{ key: string; data?: Value; error?: string; loading: boolean }>({ key: scopedKey, loading: true });
  useEffect(() => {
    let active = true;
    setState((previous) => ({ key: scopedKey, data: previous.key === scopedKey ? previous.data : undefined, loading: true }));
    loaderRef.current().then((data) => {
      if (active) setState({ key: scopedKey, data, loading: false });
    }).catch((error: unknown) => {
      const missingEndpoint = error instanceof ApiError && error.statusCode === 404
        && typeof error.errorBody === 'object' && error.errorBody !== null
        && 'detail' in error.errorBody && error.errorBody.detail === 'Not Found';
      const message = missingEndpoint
        ? '知识贡献接口尚未启用，请更新并重启业务后端；若已更新，请检查 API 代理地址。'
        : error instanceof Error ? error.message : '加载失败，请重试';
      if (active) setState({ key: scopedKey, error: message, loading: false });
    });
    return () => { active = false; };
  }, [scopedKey, version]);
  return { data: state.key === scopedKey ? state.data : undefined,
    error: state.key === scopedKey ? state.error : undefined,
    loading: state.key !== scopedKey || state.loading,
    reload: () => setVersion((previous) => previous + 1) };
}

export function KnowledgeAvatar({ user }: { user: KnowledgeUser }) {
  return <AvatarImg className="knowledge-avatar" alt={user.name} src={user.avatar_resource_id ? avatarUrl(user.avatar_resource_id) : null} />;
}

export function KnowledgeState({ loading, error, retry }: { loading?: boolean; error?: string; retry?: () => void }) {
  if (error) return <div role="alert" className="knowledge-state"><p>{error}</p><button type="button" className="knowledge-link" onClick={retry}>重新加载</button></div>;
  if (loading) return <div role="status" className="knowledge-state">正在加载…</div>;
  return null;
}

export function KnowledgeEmpty({ children }: { children: React.ReactNode }) {
  return <div className="knowledge-state">{children}</div>;
}

export function knowledgeMoney(fen: number) {
  return `¥${(fen / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

export function knowledgeTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(new Date(value));
}
