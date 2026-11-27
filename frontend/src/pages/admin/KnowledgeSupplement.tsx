// 知识补充（ORS-1159）—— 后台「知识库 → 待补充」列表。
// 知识点由 AI 工作台归纳后推送；这里是部门多人补答案 → AI 汇总 → 审核 → 入库的入口。

import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchSupplementList,
  SUPPLEMENT_STATUS_LABEL,
  SUPPLEMENT_STATUS_TONE,
  type SupplementQuestion,
} from '@/api/kbSupplement';

const CSS = `
.ks-page { padding: 16px 14px 40px; }
.ks-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.ks-head button { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px 12px; font-size: 14px; color: #2563eb; cursor: pointer; }
.ks-head .t { font-size: 16px; font-weight: 700; color: #111827; }
.ks-stat { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.ks-stat span { font-size: 12.5px; color: #475569; background: #fff; border: 1px solid #e5e7eb; border-radius: 999px; padding: 4px 10px; cursor: pointer; }
.ks-stat span.on { background: #2563eb; border-color: #2563eb; color: #fff; }
.ks-card { background: #fff; border-radius: 12px; padding: 14px; margin-bottom: 8px; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.ks-card:active { background: #f8fafc; }
.ks-card .ttl { font-size: 15px; font-weight: 600; color: #111827; line-height: 1.5; }
.ks-card .desc { font-size: 13px; color: #6b7280; margin-top: 4px; line-height: 1.6; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ks-card .meta { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.ks-chip { font-size: 11.5px; border-radius: 6px; padding: 2px 8px; color: #475569; background: #f1f5f9; }
.ks-chip.st { color: #fff; }
.ks-empty { text-align: center; color: #9ca3af; font-size: 14px; padding: 40px 0; }
`;

const FILTERS: { key: string; label: string }[] = [
  { key: 'open', label: '待补充' },
  { key: 'drafting', label: '补充中' },
  { key: 'merged', label: '已汇总' },
  { key: 'reviewing', label: '待审核' },
  { key: 'approved', label: '已入库' },
  { key: 'rejected', label: '已驳回' },
];

export default function KnowledgeSupplement() {
  const nav = useNavigate();
  const [items, setItems] = useState<SupplementQuestion[]>([]);
  const [summary, setSummary] = useState<Record<string, number>>({});
  const [status, setStatus] = useState('open');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const r = await fetchSupplementList({ status });
    setLoading(false);
    if (r) {
      setItems(r.data);
      setSummary(r.summary);
    }
  }, [status]);

  useEffect(() => { void load(); }, [load]);

  const total = Object.values(summary).reduce((a, b) => a + b, 0);

  return (
    <div className="ks-page">
      <style>{CSS}</style>
      <div className="ks-head">
        <button onClick={() => nav('/admin/kb')}>← 知识库</button>
        <span className="t">待补充知识点</span>
        <span style={{ fontSize: 12, color: '#9ca3af', marginLeft: 'auto' }}>共 {total} 条</span>
      </div>
      <div className="ks-stat">
        <span className={status === '' ? 'on' : ''} onClick={() => setStatus('')}>全部</span>
        {FILTERS.map((f) => (
          <span key={f.key} className={status === f.key ? 'on' : ''} onClick={() => setStatus(f.key)}>
            {f.label} {summary[f.key] ?? 0}
          </span>
        ))}
      </div>
      {loading && <div className="ks-empty">加载中…</div>}
      {!loading && !items.length && (
        <div className="ks-empty">{status === 'open' ? '没有待补充的知识点 🎉' : '这个状态下没有内容'}</div>
      )}
      {items.map((it) => (
        <div key={it.id} className="ks-card" onClick={() => nav(`/admin/kb/supplement/${it.id}`)}>
          <div className="ttl">{it.title}</div>
          {it.description && <div className="desc">{it.description}</div>}
          <div className="meta">
            <span className="ks-chip">{it.qtype}</span>
            <span className="ks-chip">出现 {it.hit_count} 次</span>
            <span className="ks-chip st" style={{ background: SUPPLEMENT_STATUS_TONE[it.status] }}>
              {SUPPLEMENT_STATUS_LABEL[it.status]}
            </span>
            {!!it.draft_count && <span className="ks-chip">{it.draft_count} 份草稿</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
