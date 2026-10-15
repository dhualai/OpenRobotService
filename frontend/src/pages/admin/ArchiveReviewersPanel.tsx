/** 开发者模式 · 归档审核人：问题领域 → 多人 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

interface ReviewerRow {
  id: string;
  name: string;
}

interface ArchiveReviewersData {
  domains: Record<string, string[]>;
  reviewers?: Record<string, ReviewerRow[]>;
  domain_order?: string[];
  on_empty?: string;
  source?: string;
}

export default function ArchiveReviewersPanel() {
  const [domains, setDomains] = useState<Record<string, string[]>>({});
  const [labels, setLabels] = useState<Record<string, ReviewerRow[]>>({});
  const [order, setOrder] = useState<string[]>([]);
  const [source, setSource] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = unwrap<ArchiveReviewersData>(
        await request('/dispatch-dev/archive-reviewers', { skipCache: true }),
      );
      const nextDomains: Record<string, string[]> = {};
      const raw = data?.domains || {};
      const ord = Array.isArray(data?.domain_order) && data.domain_order.length
        ? data.domain_order
        : Object.keys(raw);
      for (const d of ord) {
        nextDomains[d] = [...(raw[d] || [])];
      }
      setDomains(nextDomains);
      setLabels(data?.reviewers || {});
      setOrder(ord);
      setSource(data?.source || '');
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const nameOf = useMemo(() => {
    const map = new Map<string, string>();
    for (const rows of Object.values(labels)) {
      for (const row of rows || []) {
        if (row?.id) map.set(row.id, row.name || row.id);
      }
    }
    return map;
  }, [labels]);

  const addUser = (domain: string, user: UserItem) => {
    const uid = (user.id || '').trim();
    if (!uid) return;
    setDomains((prev) => {
      const cur = prev[domain] || [];
      if (cur.includes(uid)) return prev;
      return { ...prev, [domain]: [...cur, uid] };
    });
    setLabels((prev) => {
      const cur = prev[domain] || [];
      if (cur.some((r) => r.id === uid)) return prev;
      return {
        ...prev,
        [domain]: [...cur, { id: uid, name: user.name || user.username || uid }],
      };
    });
    setDirty(true);
  };

  const removeUser = (domain: string, uid: string) => {
    setDomains((prev) => ({
      ...prev,
      [domain]: (prev[domain] || []).filter((id) => id !== uid),
    }));
    setLabels((prev) => ({
      ...prev,
      [domain]: (prev[domain] || []).filter((r) => r.id !== uid),
    }));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      const data = unwrap<ArchiveReviewersData>(
        await request('/dispatch-dev/archive-reviewers', {
          method: 'PUT',
          body: JSON.stringify({ domains, on_empty: 'fail' }),
        }),
      );
      const raw = data?.domains || domains;
      const ord = Array.isArray(data?.domain_order) && data.domain_order.length
        ? data.domain_order
        : order;
      const next: Record<string, string[]> = {};
      for (const d of ord) next[d] = [...(raw[d] || [])];
      setDomains(next);
      setLabels(data?.reviewers || labels);
      setOrder(ord);
      setSource(data?.source || source);
      setDirty(false);
      Toast({ message: '已保存归档审核人', theme: 'success' });
    } catch (e) {
      Toast({
        message: `保存失败: ${e instanceof Error ? e.message : ''}`,
        theme: 'error',
      });
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="dispatch-dev__empty">
        <Loading text="加载归档审核人..." />
      </div>
    );
  }
  if (error) {
    return <div className="dispatch-dev__empty">{error}</div>;
  }

  return (
    <section className="dispatch-dev__card">
      <div className="dispatch-dev__head">
        <span className="dispatch-dev__title">归档审核人</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" className="dispatch-dev__btn dispatch-dev__btn--ghost" onClick={() => void load()} disabled={saving}>
            刷新
          </button>
          <button
            type="button"
            className="dispatch-dev__btn"
            onClick={() => void save()}
            disabled={saving || !dirty}
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
      <p className="dispatch-dev__hint">
        提交归档报告时，系统先判定问题领域，再从该领域名单按在审负荷加权抽签选人。
        名单保存在运行时覆盖文件，不改 config.yaml 正文；保存后提交归档即可生效。
        {source ? ` 当前来源：${source}` : ''}
        {dirty ? '（有未保存修改）' : ''}
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {order.map((domain) => {
          const ids = domains[domain] || [];
          return (
            <div
              key={domain}
              style={{
                border: '1px solid var(--td-border-level-1-color, #e7e7e7)',
                borderRadius: 8,
                padding: 12,
              }}
            >
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{domain}</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
                {ids.length === 0 ? (
                  <span className="dispatch-dev__hint" style={{ margin: 0 }}>
                    未配置（该领域提交会失败）
                  </span>
                ) : (
                  ids.map((uid) => (
                    <span
                      key={uid}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 6,
                        padding: '4px 8px',
                        borderRadius: 999,
                        background: 'var(--td-bg-color-secondarycontainer, #f3f3f3)',
                        fontSize: 13,
                      }}
                    >
                      {nameOf.get(uid) || uid}
                      <button
                        type="button"
                        aria-label="移除"
                        onClick={() => removeUser(domain, uid)}
                        style={{
                          border: 'none',
                          background: 'transparent',
                          cursor: 'pointer',
                          padding: 0,
                          lineHeight: 1,
                          color: 'var(--td-text-color-secondary, #999)',
                        }}
                      >
                        ×
                      </button>
                    </span>
                  ))
                )}
              </div>
              <UserSelect
                placeholder="添加审核人"
                title={`为「${domain}」添加审核人`}
                onChange={(u) => addUser(domain, u)}
              />
            </div>
          );
        })}
      </div>
    </section>
  );
}
