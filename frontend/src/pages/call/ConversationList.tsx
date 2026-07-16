// 会话历史列表 - 对接 /api/ai/memory/*（新版 AI 模块）
import { useState, useEffect, useCallback } from 'react';
import { Loading, Toast, Dialog } from 'tdesign-mobile-react';
import { memoryHistory, memoryClear } from '@/api/ai';
import { formatDateTime } from '@/shared/utils/url';

interface Turn {
  role: string;
  content: string;
  timestamp?: string;
}

interface Session {
  session_id: string;
  title?: string;
  turns: Turn[];
  count: number;
  updated_at?: string;
}

interface ConversationListProps {
  onSelectConversation?: (sessionId: string) => void;
}

export default function ConversationList({ onSelectConversation }: ConversationListProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    try {
      // 会话历史需要传入 session_id——前端自行维护会话列表
      // 这里改为从 localStorage 获取已知 session 列表并逐个拉取历史
      const knownIds: string[] = JSON.parse(localStorage.getItem('ai_sessions') || '[]');
      const results: Session[] = [];
      for (const sid of knownIds) {
        try {
          const res = await memoryHistory(sid);
          if (res.code === 0 && res.data) {
            const { turns, count } = res.data;
            // 用第一条用户消息作为标题
            const firstUser = turns.find((t) => t.role === 'user');
            results.push({
              session_id: sid,
              title: firstUser?.content?.slice(0, 30) || '新会话',
              turns,
              count,
              updated_at: turns[turns.length - 1]?.timestamp || new Date().toISOString(),
            });
          }
        } catch { /* 单个会话拉取失败则跳过 */ }
      }
      // 按更新时间倒序
      results.sort((a, b) => {
        const da = a.updated_at ? new Date(a.updated_at).getTime() : 0;
        const db = b.updated_at ? new Date(b.updated_at).getTime() : 0;
        return db - da;
      });
      setSessions(results);
    } catch (err) {
      Toast({ message: `加载会话失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const handleDelete = (session: Session) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除会话「${session.title || '未命名'}」吗？`,
      onConfirm: async () => {
        try {
          await memoryClear(session.session_id);
          Toast({ message: '会话已删除', theme: 'success' });
          // 从 localStorage 移除
          const knownIds: string[] = JSON.parse(localStorage.getItem('ai_sessions') || '[]');
          const updated = knownIds.filter((id) => id !== session.session_id);
          localStorage.setItem('ai_sessions', JSON.stringify(updated));
          fetchSessions();
        } catch (err) {
          Toast({ message: `删除失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
        }
      },
    });
  };

  if (loading) return <Loading text="加载会话..." />;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 12, color: '#333' }}>
        会话历史 ({sessions.length})
      </div>

      {sessions.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无会话记录</div>
      ) : (
        sessions.map((session) => (
          <div
            key={session.session_id}
            onClick={() => onSelectConversation?.(session.session_id)}
            style={{
              background: '#fff',
              borderRadius: 8,
              padding: '12px 14px',
              marginBottom: 8,
              boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
              cursor: 'pointer',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 500, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {session.title || '未命名会话'}
              </div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                {session.count} 条对话
                {session.updated_at && <> · {formatDateTime(session.updated_at)}</>}
              </div>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); handleDelete(session); }}
              style={{
                marginLeft: 12,
                padding: '4px 10px',
                border: '1px solid #e34d59',
                borderRadius: 4,
                background: 'transparent',
                color: '#e34d59',
                fontSize: 12,
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              删除
            </button>
          </div>
        ))
      )}
    </div>
  );
}
