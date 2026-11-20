/** 开发者模式 · U老师 长期记忆（查看、改写、删除） */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

interface MemoryRow {
  id: string;
  kind?: string;
  content: string;
  created_at?: string;
  source?: string;
  source_id?: string;
}

export default function MemoryPanel() {
  const [items, setItems] = useState<MemoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = unwrap<{ items?: MemoryRow[] }>(
        await request('/dispatch-dev/memories', { skipCache: true }),
      );
      setItems(Array.isArray(data?.items) ? data.items : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const startEdit = (row: MemoryRow) => {
    setEditingId(row.id);
    setDraft(row.content || '');
  };

  const save = async () => {
    if (!editingId) return;
    const content = draft.trim();
    if (!content) {
      Toast({ message: '正文不能为空', theme: 'warning' });
      return;
    }
    setSaving(true);
    try {
      await request(`/dispatch-dev/memories/${editingId}`, {
        method: 'PUT',
        body: JSON.stringify({ content }),
      });
      Toast({ message: '已保存', theme: 'success' });
      setEditingId(null);
      await load();
    } catch (e) {
      Toast({ message: `保存失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  const remove = async (row: MemoryRow) => {
    const preview = (row.content || '').slice(0, 24);
    if (!window.confirm(`删除这条记忆？\n${preview}`)) return;
    try {
      await request(`/dispatch-dev/memories/${row.id}`, { method: 'DELETE' });
      Toast({ message: '已删除', theme: 'success' });
      if (editingId === row.id) setEditingId(null);
      await load();
    } catch (e) {
      Toast({ message: `删除失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    }
  };

  return (
    <section className="dispatch-dev__card">
      <div className="dispatch-dev__head">
        <span className="dispatch-dev__title">U老师长期记忆</span>
        <div className="dispatch-dev__head-actions">
          <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void load()} disabled={loading}>
            刷新
          </button>
        </div>
      </div>
      <p className="dispatch-dev__hint">
        这里是讨论里说「记住……」之后仍生效的约定。改一条会立刻用于之后的召回；删掉后不再提起。新的记忆仍在工单讨论里对 U老师 说。
      </p>
      {error ? <p className="dispatch-dev__hint dispatch-dev__hint--error">{error}</p> : null}
      {loading ? (
        <div className="dispatch-dev__empty"><Loading text="加载记忆…" /></div>
      ) : error ? null : items.length === 0 ? (
        <div className="dispatch-dev__empty-row">还没有生效中的记忆</div>
      ) : (
        <ul className="dispatch-dev__list dispatch-dev__memory-list">
          {items.map((row) => {
            const ticketId = /^\d+$/.test(String(row.source_id || '')) ? row.source_id : '';
            const editing = editingId === row.id;
            return (
              <li key={row.id}>
                {editing ? (
                  <textarea
                    className="dispatch-dev__memory-edit"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    rows={3}
                  />
                ) : (
                  <strong>{row.content}</strong>
                )}
                <span>
                  {row.created_at || ''}
                  {ticketId ? (
                    <>
                      {' · '}
                      <Link to={`/tasks/${ticketId}`} target="_blank" rel="noopener noreferrer">
                        来自工单 #{ticketId}
                      </Link>
                    </>
                  ) : null}
                </span>
                <div className="dispatch-dev__head-actions">
                  {editing ? (
                    <>
                      <button type="button" className="dispatch-dev__btn" disabled={saving} onClick={() => void save()}>
                        保存
                      </button>
                      <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" disabled={saving} onClick={() => setEditingId(null)}>
                        取消
                      </button>
                    </>
                  ) : (
                    <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => startEdit(row)}>
                      编辑
                    </button>
                  )}
                  <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void remove(row)}>
                    删除
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
