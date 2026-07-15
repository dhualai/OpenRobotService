// 会话历史列表 - 基于接口文档 GET /api/call/conversations
import { useState, useEffect, useCallback } from 'react';
import { Loading, Toast, Dialog } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { formatDateTime } from '@/shared/utils/url';

interface Conversation {
  id: string;
  title: string;
  user_id?: string;
  scene_type?: string;
  created_at: string;
  updated_at: string;
}

interface ConversationListProps {
  onSelectConversation?: (convId: string) => void;
}

export default function ConversationList({ onSelectConversation }: ConversationListProps) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [loading, setLoading] = useState(true);
  const request = createRequest(API_CONFIG.CALL.BASE_URL, '对话服务');

  const fetchConversations = useCallback(async () => {
    setLoading(true);
    try {
      const data = await request<Conversation[]>('/conversations');
      // 按更新时间倒序
      const list = Array.isArray(data) ? data : (data as any).items || [];
      list.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      setConversations(list);
    } catch (err) {
      Toast({ message: `加载会话失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchConversations(); }, [fetchConversations]);

  const handleDelete = (conv: Conversation) => {
    Dialog.confirm?.({
      title: '确认删除',
      content: `确定要删除会话「${conv.title || '未命名'}」吗？`,
      onConfirm: async () => {
        try {
          await request(`/conversations/${conv.id}`, { method: 'DELETE' });
          Toast({ message: '会话已删除', theme: 'success' });
          fetchConversations();
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
        会话历史 ({conversations.length})
      </div>

      {conversations.length === 0 ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>暂无会话记录</div>
      ) : (
        conversations.map((conv) => (
          <div
            key={conv.id}
            onClick={() => onSelectConversation?.(conv.id)}
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
                {conv.title || '未命名会话'}
              </div>
              <div style={{ fontSize: 12, color: '#999', marginTop: 4 }}>
                {conv.scene_type && <span>{conv.scene_type} · </span>}
                {formatDateTime(conv.updated_at)}
              </div>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); handleDelete(conv); }}
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
