import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import { createChallenge, fetchChallenges, fetchChallengeSource, publishChallenge } from '@/api/knowledgeChallenges';
import { ChallengeSummary, challengeStateLabels, useChallengePolling } from './HighValueQuestionCard';
import { KnowledgeEmpty, KnowledgeState, useKnowledgeResource } from './knowledgeShared';
import '@/shared/styles/knowledgeChallenges.css';

export function localDateInput(date: Date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function parseBountyFen(value: string) {
  if (!/^\d+(\.\d{1,2})?$/.test(value.trim())) throw new Error('请输入有效奖金，最多两位小数');
  const [whole, decimal = ''] = value.trim().split('.');
  const amount = Number(whole) * 100 + Number(decimal.padEnd(2, '0'));
  if (!Number.isSafeInteger(amount) || amount < 1 || amount > 2147483647) throw new Error('请填写有效的悬赏奖金');
  return amount;
}

export function ChallengePublishForm({ onDone, onCancel }: { onDone: (id: number) => void; onCancel: () => void }) {
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [amount, setAmount] = useState('');
  const [endsAt, setEndsAt] = useState(localDateInput(new Date(Date.now() + 30 * 86400000)));
  const [taskId, setTaskId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestKey = useRef(typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID() : `challenge_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`);
  const [linkedTaskId, setLinkedTaskId] = useState<number | null>(null);
  const [loadingSource, setLoadingSource] = useState(false);
  async function loadSource() {
    setError(''); setLoadingSource(true);
    try {
      if (!/^\d+$/.test(taskId) || Number(taskId) < 1) throw new Error('请输入有效工单编号');
      const source = await fetchChallengeSource(taskId);
      setTitle(source.title); setContent(source.content); setLinkedTaskId(Number(taskId));
    } catch (failure) { setError(failure instanceof Error ? failure.message : '带入工单失败'); }
    finally { setLoadingSource(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      const draft = await createChallenge({ request_key: requestKey.current, task_id: linkedTaskId,
        title: title.trim(), content: content.trim(), bounty_fen: parseBountyFen(amount), ends_at: new Date(endsAt).toISOString() });
      const published = await publishChallenge(draft.id, draft.version);
      onDone(published.id);
    } catch (failure) { setError(failure instanceof Error ? failure.message : '发布失败'); }
    finally { setBusy(false); }
  }
  return <form className="mac-card mac-card--pad challenge-form" onSubmit={submit}>
    <h3>发布高价值问题</h3><p className="knowledge-muted">可直接发布，无需关联工单。请勿填写姓名、联系方式或客户敏感信息。</p>
    <label>问题标题<input className="knowledge-select" value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={255} disabled={busy} /></label>
    <label>问题内容<textarea className="knowledge-textarea" value={content} onChange={(event) => setContent(event.target.value)} required maxLength={20000} disabled={busy} /></label>
    <div className="challenge-form__grid"><label>悬赏奖金（元）<input className="knowledge-select" inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="由最佳解答者获得" required disabled={busy} /></label>
      <label>截止时间<input className="knowledge-select" type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} required disabled={busy} /></label></div>
    <details><summary>关联已有工单（可选）{linkedTaskId ? ` · 已关联 #${linkedTaskId}` : ''}</summary><div className="challenge-actions"><input className="knowledge-select" aria-label="来源工单编号" inputMode="numeric" value={taskId} onChange={(event) => { setTaskId(event.target.value); setLinkedTaskId(null); }} disabled={busy || loadingSource} /><button type="button" className="knowledge-link" disabled={busy || loadingSource} onClick={loadSource}>{loadingSource ? '带入中…' : '带入工单'}</button>{linkedTaskId && <button type="button" className="knowledge-link" onClick={() => { setLinkedTaskId(null); setTaskId(''); }}>取消关联</button>}</div></details>
    {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
    <div className="challenge-actions"><button className="knowledge-button" type="submit" disabled={busy || loadingSource}>{busy ? '发布中…' : '确认发布'}</button><button type="button" className="knowledge-link" onClick={onCancel} disabled={busy}>取消</button></div>
  </form>;
}

export default function KnowledgeChallenges() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view = ['active', 'mine', 'ended', 'manage'].includes(params.get('view') || '') ? params.get('view')! : 'active';
  const page = Math.max(1, Number(params.get('page')) || 1);
  const [publishing, setPublishing] = useState(false);
  const resource = useKnowledgeResource(`challenges:${view}:${page}`, () => fetchChallenges(view, page));
  useChallengePolling(resource.reload);
  const tabs = [['active', '进行中'], ['mine', '我的参与'], ['ended', '已结束'], ...(resource.data?.can_manage ? [['manage', '管理问题']] : [])];
  return <div className="dashboard-page knowledge-page"><Navbar title="高价值问题榜单" leftArrow onLeftClick={() => navigate('/admin')} right={<UserAvatarMenu />} fixed />
    <div className="knowledge-content"><div className="knowledge-toolbar"><h2>共同解答复杂问题</h2>{resource.data?.can_manage && <button className="knowledge-link" type="button" onClick={() => setPublishing(true)}>发布问题</button>}</div>
      <div className="knowledge-note">问题赞表示共性需求；答案赞决定到期最佳解答。每天最多点赞5个问题，答案另有每天5条额度。<br />到期自动选奖，管理员可处理异常结果；奖金按平台既有流程发放。</div>
      {publishing && resource.data?.can_manage && <ChallengePublishForm onDone={(id) => navigate(`/admin/knowledge/challenges/${id}`)} onCancel={() => setPublishing(false)} />}
      <div className="knowledge-tabs">{tabs.map(([value, label]) => <button type="button" key={value} aria-pressed={view === value} onClick={() => setParams({ view: value })}>{label}</button>)}</div>
      <KnowledgeState loading={resource.loading && !resource.data} error={resource.error} retry={resource.reload} />
      {resource.data?.items.map((challenge) => <section className="mac-card mac-card--pad challenge-list-card" key={challenge.id}><ChallengeSummary challenge={challenge} /><div className="knowledge-question-footer"><span>{challengeStateLabels[challenge.state]}{challenge.hidden ? ' · 已隐藏' : ''}</span><Link className="knowledge-link" to={`/admin/knowledge/challenges/${challenge.id}`}>查看问题与解答 →</Link></div></section>)}
      {resource.data && !resource.data.items.length && <KnowledgeEmpty>暂无符合条件的高价值问题</KnowledgeEmpty>}
      {resource.data && resource.data.total > 20 && <div className="knowledge-pagination"><button type="button" disabled={page <= 1} onClick={() => setParams({ view, page: String(page - 1) })}>上一页</button><span>第{page}页 · 共{resource.data.total}题</span><button type="button" disabled={page * 20 >= resource.data.total} onClick={() => setParams({ view, page: String(page + 1) })}>下一页</button></div>}
    </div></div>;
}
