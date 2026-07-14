// AI 智能对话页面 - 从 HelpDesk ChatContainer 迁移
import { useState, useEffect, useRef, useCallback } from 'react';
import { Navbar, Textarea, Button, Toast } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

export default function AIChat() {
  const navigate = useNavigate();
  const { token, username } = useAuthStore();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  /** 创建新会话，返回 conversation_id */
  const ensureConversation = useCallback(async (): Promise<number> => {
    if (conversationId) return conversationId;
    const request = createRequest(API_CONFIG.CALL.BASE_URL, 'Call');
    const conv = await request<{ id: number }>('/conversations', {
      method: 'POST',
      body: JSON.stringify({
        title: `对话 ${new Date().toLocaleString('zh-CN')}`,
        user_id: username || 'anonymous',
        service_ticket_id: `chat_${Date.now()}`,
        scene_type: 'chat',
      }),
    });
    setConversationId(conv.id);
    return conv.id;
  }, [conversationId, username]);

  const sendMessage = async () => {
    if (!input.trim() || !token) return;
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      // 确保会话已创建
      const cid = await ensureConversation();

      const response = await fetch(`${API_CONFIG.CALL.BASE_URL}/qa/ask/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          question: userMessage.content,
          conversation_id: cid,
          include_history: true,
        }),
      });

      if (!response.ok) throw new Error('请求失败');

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';
      let serverConversationId: number | null = cid;

      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, assistantMessage]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        const lines = text.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.event === 'start') {
                serverConversationId = Number(data.conversation_id);
                setConversationId(Number(data.conversation_id));
              } else if (data.event === 'message') {
                assistantContent += data.content;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMessage.id ? { ...m, content: assistantContent } : m
                  )
                );
              } else if (data.event === 'done') {
                setConversationId(Number(data.conversation_id) || serverConversationId);
              }
            } catch { /* ignore */ }
          }
        }
      }
    } catch (err) {
      Toast({ message: `发送失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <Navbar title="AI 智能助手" fixed leftArrow onLeftClick={() => navigate(-1)} />
      <div style={{ flex: 1, overflow: 'auto', padding: '16px', paddingTop: 56, paddingBottom: 80 }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#999', marginTop: 80 }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>🤖</div>
            <p>你好{username ? `，${username}` : ''}！</p>
            <p style={{ fontSize: 14, marginTop: 8 }}>有什么可以帮助你的？</p>
          </div>
        )}
        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              display: 'flex',
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
              marginBottom: 16,
            }}
          >
            <div
              style={{
                maxWidth: '80%',
                padding: '10px 16px',
                borderRadius: 12,
                background: msg.role === 'user' ? '#0052d9' : '#fff',
                color: msg.role === 'user' ? '#fff' : '#333',
                boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {msg.content || (msg.role === 'assistant' && loading ? '思考中...' : '')}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>
      <div
        style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          padding: '8px 16px 16px',
          background: '#fff',
          borderTop: '1px solid #eee',
          display: 'flex',
          gap: 8,
          alignItems: 'flex-end',
        }}
      >
        <Textarea
          value={input}
          onChange={(val) => setInput(String(val))}
          placeholder="输入你的问题..."
          autosize={{ minRows: 1, maxRows: 4 }}
          style={{ flex: 1 }}
        />
        <Button theme="primary" onClick={sendMessage} loading={loading} disabled={!input.trim()}>
          发送
        </Button>
      </div>
    </div>
  );
}
