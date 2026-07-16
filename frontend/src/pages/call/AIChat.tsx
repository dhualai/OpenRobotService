// AI 智能对话页面 - 对接 /api/ai/qa/ask/stream（SSE 流式诊断）
import { useState, useEffect, useRef, useCallback } from 'react';
import { Navbar, Textarea, Button, Toast } from 'tdesign-mobile-react';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/stores/auth';
import { qaAskStream, generateSessionId, trackSession } from '@/api/ai';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';

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
  const [sessionId, setSessionId] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  /** 确保 sessionId 存在——新对话自动生成，无需调创建接口 */
  const ensureSessionId = useCallback((): string => {
    if (!sessionId) {
      const id = generateSessionId();
      setSessionId(id);
      trackSession(id);
      return id;
    }
    return sessionId;
  }, [sessionId]);

  const sendMessage = async () => {
    if (!input.trim() || !token) return;
    const userMessage: Message = {
      id: Date.now().toString(),
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    const currentQuery = input;
    setInput('');
    setLoading(true);

    try {
      const sid = ensureSessionId();

      const response = await qaAskStream({
        session_id: sid,
        query: currentQuery,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';

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
          // 新版 SSE 格式：先 data: {"token":"..."}  后 event: done
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.token) {
                // 增量 token（新版 AI 诊断）
                assistantContent += data.token;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMessage.id ? { ...m, content: assistantContent } : m
                  )
                );
              } else if (data.content) {
                // 兼容旧版 message 事件
                assistantContent += data.content;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantMessage.id ? { ...m, content: assistantContent } : m
                  )
                );
              }
            } catch { /* JSON 行解析出错则跳过 */ }
          }
          // event: first_token / result / done / error 由后端发出
          if (line.startsWith('event: ') && line.includes('error')) {
            const dataLine = lines.find((l) => l.startsWith('data: '));
            if (dataLine) {
              try {
                const err = JSON.parse(dataLine.slice(6));
                Toast({ message: `AI 错误: ${err.error || '未知错误'}`, theme: 'error' });
              } catch { /* ignore */ }
            }
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
                padding: msg.role === 'user' ? '10px 16px' : '10px 16px',
                borderRadius: 12,
                background: msg.role === 'user' ? '#0052d9' : '#fff',
                color: msg.role === 'user' ? '#fff' : '#333',
                boxShadow: '0 1px 3px rgba(0,0,0,0.1)',
                whiteSpace: msg.role === 'user' ? 'pre-wrap' : 'normal',
                wordBreak: 'break-word',
              }}
            >
              {msg.role === 'assistant' ? (
                msg.content ? (
                  <MarkdownRenderer content={msg.content} />
                ) : (
                  loading ? '思考中...' : ''
                )
              ) : (
                msg.content
              )}
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
