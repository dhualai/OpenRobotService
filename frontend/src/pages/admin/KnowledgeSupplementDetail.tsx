// 知识补充 · 知识点详情（ORS-1159）—— 多人草稿 + AI 汇总 + 审核。
// 审核通过后后端写 kb/team/chat_qa/{标题}.md，下次周入库生效。

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  approveSupplement,
  fetchSupplementDetail,
  mergeSupplementDrafts,
  rejectSupplement,
  saveSupplementDraft,
  submitSupplementReview,
  SUPPLEMENT_STATUS_LABEL,
  SUPPLEMENT_STATUS_TONE,
  type SupplementDraft,
  type SupplementMe,
  type SupplementQuestion,
} from '@/api/kbSupplement';

const CSS = `
.ksd-page { padding: 16px 14px 60px; }
.ksd-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.ksd-head button { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px 12px; font-size: 14px; color: #2563eb; cursor: pointer; }
.ksd-head .st { margin-left: auto; font-size: 12px; color: #fff; border-radius: 6px; padding: 3px 10px; }
.ksd-title { font-size: 18px; font-weight: 700; color: #111827; line-height: 1.5; margin-bottom: 6px; }
.ksd-desc { font-size: 13.5px; color: #6b7280; line-height: 1.7; margin-bottom: 14px; white-space: pre-wrap; }
.ksd-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.ksd-chip { font-size: 11.5px; border-radius: 6px; padding: 2px 8px; color: #475569; background: #f1f5f9; }
.ksd-sec { background: #fff; border-radius: 12px; padding: 14px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.ksd-sec h4 { margin: 0 0 10px; font-size: 14px; color: #111827; display: flex; align-items: center; gap: 8px; }
.ksd-sec h4 .who { font-size: 12px; color: #9ca3af; font-weight: 400; }
.ksd-md { font-size: 14px; line-height: 1.75; color: #1f2937; overflow-wrap: break-word; }
.ksd-md h1 { font-size: 17px; } .ksd-md h2 { font-size: 15.5px; margin: 14px 0 8px; }
.ksd-md ul, .ksd-md ol { padding-left: 22px; }
.ksd-md pre { background: #0f172a; color: #e2e8f0; padding: 10px; border-radius: 8px; overflow-x: auto; font-size: 12.5px; }
.ksd-md code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 12.5px; }
.ksd-md table { display: block; overflow-x: auto; border-collapse: collapse; font-size: 13px; }
.ksd-md th, .ksd-md td { border: 1px solid #e5e7eb; padding: 5px 8px; }
.ksd-ta { width: 100%; min-height: 180px; border: 1px solid #e5e7eb; border-radius: 10px; padding: 10px 12px; font-size: 14px; line-height: 1.7; resize: vertical; }
.ksd-bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
.ksd-btn { border: 1px solid #e5e7eb; background: #fff; border-radius: 8px; padding: 8px 14px; font-size: 14px; color: #374151; cursor: pointer; }
.ksd-btn.pri { background: #2563eb; border-color: #2563eb; color: #fff; }
.ksd-btn.warn { background: #dc2626; border-color: #dc2626; color: #fff; }
.ksd-btn:disabled { opacity: .5; cursor: not-allowed; }
.ksd-note { font-size: 12px; color: #94a3b8; }
.ksd-merged { border: 1px solid #ddd6fe; background: #faf5ff; }
.ksd-empty { text-align: center; color: #9ca3af; font-size: 14px; padding: 40px 0; }
.ksd-msg { position: sticky; bottom: 12px; background: #111827; color: #fff; border-radius: 10px; padding: 10px 14px; font-size: 13px; margin-top: 12px; }
`;

export default function KnowledgeSupplementDetail() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState<SupplementQuestion | null>(null);
  const [drafts, setDrafts] = useState<SupplementDraft[]>([]);
  const [me, setMe] = useState<SupplementMe | null>(null);
  const [myDraft, setMyDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [rejectReason, setRejectReason] = useState('');
  const [showReject, setShowReject] = useState(false);
  const [loadErr, setLoadErr] = useState('');

  const load = useCallback(async () => {
    const r = await fetchSupplementDetail(id);
    if (!r) {
      setLoadErr('知识点不存在，或加载失败（可能已被清理）');
      return;
    }
    setLoadErr('');
    setQ(r.data);
    setDrafts(r.drafts);
    setMe(r.me);
    const mine = r.drafts.find((d) => !d.is_merged && d.author_id === r.me.user_id);
    if (mine) setMyDraft(mine.content_md);
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const merged = useMemo(() => drafts.find((d) => d.is_merged), [drafts]);
  const others = useMemo(() => drafts.filter((d) => !d.is_merged), [drafts]);
  const canWrite = !!me?.can_write;
  const canReview = !!me?.can_review;
  const locked = q?.status === 'approved';

  const act = async (fn: () => Promise<void>, okMsg: string) => {
    if (busy) return;
    setBusy(true);
    setMsg('');
    try {
      await fn();
      setMsg(okMsg);
      await load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!q) {
    return (
      <div className="ksd-page">
        <style>{CSS}</style>
        {loadErr ? (
          <>
            <div className="ksd-empty">{loadErr}</div>
            <div style={{ textAlign: 'center' }}>
              <button className="ksd-btn" onClick={() => nav('/admin/kb/supplement')}>← 返回待补充列表</button>
            </div>
          </>
        ) : (
          <div className="ksd-empty">加载中…</div>
        )}
      </div>
    );
  }

  return (
    <div className="ksd-page">
      <style>{CSS}</style>
      <div className="ksd-head">
        <button onClick={() => nav('/admin/kb/supplement')}>← 待补充</button>
        <span className="st" style={{ background: SUPPLEMENT_STATUS_TONE[q.status] }}>
          {SUPPLEMENT_STATUS_LABEL[q.status]}
        </span>
      </div>

      <div className="ksd-title">{q.title}</div>
      {q.description && <div className="ksd-desc">{q.description}</div>}
      <div className="ksd-meta">
        <span className="ksd-chip">{q.qtype}</span>
        <span className="ksd-chip">出现 {q.hit_count} 次</span>
        <span className="ksd-chip">{others.length} 份草稿</span>
      </div>

      {/* AI 汇总稿 */}
      {merged && (
        <div className="ksd-sec ksd-merged">
          <h4>✨ AI 汇总稿 <span className="who">（可继续人工补充，审核通过以汇总稿为准）</span></h4>
          <div className="ksd-md">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{merged.content_md}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* 其他人草稿（互相可见） */}
      {others.length > 0 && (
        <div className="ksd-sec">
          <h4>成员草稿 <span className="who">{others.length} 份 · 互相可见</span></h4>
          {others.map((d) => (
            <div key={d.id} style={{ borderTop: '1px dashed #e5e7eb', paddingTop: 10, marginTop: 10 }}>
              <div style={{ fontSize: 12.5, color: '#64748b', marginBottom: 6 }}>
                {d.author_name || d.author_id}
                {d.author_id === me?.user_id ? '（我）' : ''}
                {d.updated_at ? ` · ${d.updated_at.slice(0, 16).replace('T', ' ')}` : ''}
              </div>
              <div className="ksd-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{d.content_md}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 我的草稿 */}
      {canWrite && !locked && (
        <div className="ksd-sec">
          <h4>我的答案草稿 <span className="who">markdown，可写排查步骤/参数/注意事项</span></h4>
          <textarea
            className="ksd-ta"
            value={myDraft}
            placeholder="写下你的答案（保存后对其他成员可见）…"
            onChange={(e) => setMyDraft(e.target.value)}
          />
          <div className="ksd-bar">
            <button className="ksd-btn" disabled={busy || !myDraft.trim()}
              onClick={() => act(async () => { await saveSupplementDraft(id, myDraft); }, '草稿已保存')}>
              保存草稿
            </button>
            <button className="ksd-btn" disabled={busy}
              onClick={() => act(async () => {
                setMsg('AI 汇总中…');
                const md = await mergeSupplementDrafts(id);
                setMsg(`AI 汇总完成（${md.length} 字）`);
              }, 'AI 汇总完成')}>
              ✨ AI 汇总所有草稿
            </button>
            <button className="ksd-btn pri" disabled={busy || !drafts.length}
              onClick={() => act(async () => { await submitSupplementReview(id); }, '已提交审核')}>
              提交审核
            </button>
          </div>
          {q.status === 'rejected' && (
            <div className="ksd-note" style={{ marginTop: 8, color: '#dc2626' }}>
              上次审核被驳回：修改后重新提交审核。
            </div>
          )}
        </div>
      )}

      {/* 审核区 */}
      {canReview && !locked && (
        <div className="ksd-sec">
          <h4>审核 <span className="who">通过后写入知识库 team/chat_qa，下次入库生效</span></h4>
          <div className="ksd-bar">
            <button className="ksd-btn pri" disabled={busy || !drafts.length}
              onClick={() => act(async () => { const r = await approveSupplement(id); setMsg(`已入库：${r?.file || ''}`); }, '审核通过，已写入知识库')}>
              通过入库
            </button>
            <button className="ksd-btn warn" disabled={busy}
              onClick={() => setShowReject((v) => !v)}>
              驳回
            </button>
          </div>
          {showReject && (
            <div style={{ marginTop: 10 }}>
              <textarea className="ksd-ta" style={{ minHeight: 80 }} value={rejectReason}
                placeholder="驳回理由（必填）" onChange={(e) => setRejectReason(e.target.value)} />
              <div className="ksd-bar">
                <button className="ksd-btn warn" disabled={busy || !rejectReason.trim()}
                  onClick={() => act(async () => {
                    await rejectSupplement(id, rejectReason);
                    setShowReject(false);
                    setRejectReason('');
                  }, '已驳回')}>
                  确认驳回
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {!canWrite && !canReview && (
        <div className="ksd-note">当前账号没有补充或审核权限（仅智能规划研究院成员可补充，审核人名单见后台配置）。</div>
      )}

      {msg && <div className="ksd-msg">{msg}</div>}
    </div>
  );
}
