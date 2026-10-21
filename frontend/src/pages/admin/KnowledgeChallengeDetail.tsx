import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import {
  changeChallengeState, fetchChallenge, fetchMyChallengeAnswer, likeChallenge, likeChallengeAnswer,
  overrideChallengeResult, publishChallenge, saveChallengeAnswer, type ChallengeDetail, type MyChallengeAnswer,
} from '@/api/knowledgeChallenges';
import { challengeStateLabels, useChallengePolling } from './HighValueQuestionCard';
import { localDateInput } from './KnowledgeChallenges';
import { KnowledgeEmpty, KnowledgeState, knowledgeMoney, knowledgeTime, useKnowledgeResource } from './knowledgeShared';
import '@/shared/styles/knowledgeChallenges.css';

const actionLabels: Record<string, string> = {
  pause: '暂停征集', resume: '恢复征集', extend: '延长截止时间', cancel: '取消问题',
  hide: '隐藏问题', unhide: '取消隐藏', reject: '不予通过', replace: '更换最佳回答', restore: '恢复获奖结果',
};

export function ChallengeManagement({ challenge, onChanged }: { challenge: ChallengeDetail; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [action, setAction] = useState('');
  const [reason, setReason] = useState('');
  const [answerId, setAnswerId] = useState('');
  const [endsAt, setEndsAt] = useState(localDateInput(new Date(new Date(challenge.ends_at).getTime() + 7 * 86400000)));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!challenge.can_manage_state) return null;
  const ongoing = ['active', 'paused'].includes(challenge.state) && new Date(challenge.ends_at).getTime() > Date.now();
  const settled = ['completed', 'rejected'].includes(challenge.state);
  const actions = [...(ongoing ? [challenge.state === 'paused' ? 'resume' : 'pause', 'extend', 'cancel'] : []),
    ...(settled ? [challenge.state === 'rejected' ? 'restore' : 'reject', 'replace'] : []), challenge.hidden ? 'unhide' : 'hide'];
  const selected = actions.includes(action) ? action : actions[0];
  async function publish() {
    setBusy(true); setError('');
    try { await publishChallenge(challenge.id, challenge.version); onChanged(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '发布失败'); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError('');
    try {
      if (['reject', 'replace', 'restore'].includes(selected)) {
        await overrideChallengeResult(challenge.id, { action: selected, reason, version: challenge.version,
          ...(answerId ? { answer_id: Number(answerId) } : {}) });
      } else {
        await changeChallengeState(challenge.id, { action: selected, reason, version: challenge.version,
          ...(selected === 'extend' ? { ends_at: new Date(endsAt).toISOString() } : {}) });
      }
      setExpanded(false); setReason(''); onChanged();
    } catch (failure) { setError(failure instanceof Error ? failure.message : '操作失败'); }
    finally { setBusy(false); }
  }
  return <>
    <div className="challenge-actions">{challenge.state === 'draft' ? <button type="button" className="knowledge-link" disabled={busy} onClick={publish}>发布此草稿</button> : <button type="button" className="knowledge-link" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>更改状态</button>}</div>
    {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
    {expanded && <form className="challenge-form" aria-label="更改高价值问题状态" onSubmit={submit}>
      <label>操作<select className="knowledge-select" value={selected} onChange={(event) => setAction(event.target.value)} disabled={busy}>{actions.map((value) => <option value={value} key={value}>{actionLabels[value]}</option>)}</select></label>
      {selected === 'extend' && <label>新的截止时间<input className="knowledge-select" type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} required disabled={busy} /></label>}
      {['replace', 'restore'].includes(selected) && <label>选择最佳回答<select className="knowledge-select" value={answerId} onChange={(event) => setAnswerId(event.target.value)} required={selected === 'replace'} disabled={busy}><option value="">{selected === 'restore' ? '恢复自动评选人选' : '请选择'}</option>{challenge.answers.map((answer) => <option value={answer.id} key={answer.id}>{answer.author} · {answer.like_count}赞 · {answer.content.slice(0, 30)}</option>)}</select></label>}
      <label>操作原因<textarea className="knowledge-textarea" value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={2000} disabled={busy} /></label>
      <p className="challenge-notice">结果调整会留痕；已进入财务的奖金不会自动撤回、更换收款人或重复发放。</p>
      <div className="challenge-actions"><button className="knowledge-button" type="submit" disabled={busy || !reason.trim()}>{busy ? '处理中…' : '确认操作'}</button><button className="knowledge-link" type="button" onClick={() => setExpanded(false)} disabled={busy}>取消</button></div>
    </form>}
    {challenge.reward_attention && <div className="knowledge-note">业务结果与原奖金记录不一致，请按现有权限在奖励管理中核对。原收款人及资金状态未被自动修改。<Link className="knowledge-link" to="/admin/wxpay-rewards">前往奖励管理 →</Link></div>}
    {challenge.last_error && <p role="alert">{challenge.last_error}</p>}
  </>;
}

export function ChallengeAnswerEditor({ id, canAnswer, onSubmitted, onCancel, embedded = false }: { id: string; canAnswer: boolean; onSubmitted: () => void; onCancel?: () => void; embedded?: boolean }) {
  const resource = useKnowledgeResource(`my-challenge-answer:${id}`, () => fetchMyChallengeAnswer(id));
  const [content, setContent] = useState('');
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [answerState, setAnswerState] = useState<MyChallengeAnswer | null>(null);
  const version = useRef(0);
  const saved = useRef('');
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (resource.data && !ready) {
      setContent(resource.data.content); saved.current = resource.data.content;
      version.current = resource.data.version; setReady(true);
      setAnswerState(resource.data);
      setStatus(resource.data.submitted_at ? '已有已发布答案，修改后需再次发布' : '草稿仅自己可见');
    }
  }, [resource.data, ready]);
  const persist = useCallback((value: string, submit: boolean) => {
    const operation = queue.current.then(async () => {
      if (!submit && value === saved.current) return;
      const answer = await saveChallengeAnswer(id, value, version.current, submit);
      version.current = answer.version; saved.current = value;
      if (mounted.current) { setAnswerState(answer); setStatus(submit ? '答案已发布' : '草稿已保存，仅自己可见'); setError(''); }
    });
    queue.current = operation.catch(() => undefined);
    return operation;
  }, [id]);
  const canSubmit = canAnswer && answerState?.can_submit === true;
  const isEditing = !!answerState?.submitted_at;
  const editExhausted = isEditing && answerState.remaining_edits === 0;
  useEffect(() => {
    if (!ready || !canSubmit || busy || content === saved.current) return;
    const timer = window.setTimeout(() => {
      void persist(content, false).catch((failure: unknown) => {
        if (mounted.current) setError(failure instanceof Error ? failure.message : '自动保存失败，请重试');
      });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [content, ready, canSubmit, busy, persist]);
  async function publish(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setError('');
    try { await persist(content, true); onSubmitted(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '发布失败'); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <section className={embedded ? 'challenge-answer-editor' : 'mac-card mac-card--pad'}><h3>{isEditing ? '编辑我的解答' : '我的解答'}</h3><KnowledgeState loading={resource.loading && !ready} error={resource.error} retry={resource.reload} />
    {ready && <form className="challenge-form" onSubmit={publish}><p className="challenge-notice">匿名展示，请勿填写姓名、电话、客户信息或原工单内部链接。每人每天最多点赞5条不同答案，不能给自己的答案点赞。</p>
      <p role="note" className="knowledge-note">{editExhausted ? '本题解答已修改过一次，不能再次编辑。' : isEditing ? '每个问题的已发布解答仅可修改一次。提交修改后不能再次编辑；保存草稿或取消编辑不消耗修改次数。' : '首次发布后，每个问题的解答仅可再修改一次，请确认内容后发布。'}</p>
      <label>答案内容<textarea className="knowledge-textarea" value={content} onChange={(event) => setContent(event.target.value)} required maxLength={20000} disabled={busy || !canSubmit} /></label>
      <span className="knowledge-muted" role="status">{editExhausted ? '本题修改机会已用完' : canAnswer ? status : '问题已截止或暂停，不能继续提交'}</span>
      {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
      <div className="challenge-actions"><button type="submit" className="knowledge-button" disabled={!canSubmit || busy || !content.trim()}>{busy ? '发布中…' : isEditing ? '提交修改' : '发布答案'}</button><button type="button" className="knowledge-link" disabled={!canSubmit || busy} onClick={() => void persist(content, false).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : '保存失败'))}>保存草稿</button>{onCancel && <button type="button" className="knowledge-link" disabled={busy} onClick={onCancel}>取消编辑</button>}</div>
    </form>}
  </section>;
}

export default function KnowledgeChallengeDetail() {
  const { challengeId = '' } = useParams();
  const navigate = useNavigate();
  const resource = useKnowledgeResource(`challenge-detail:${challengeId}`, () => fetchChallenge(challengeId));
  useChallengePolling(resource.reload);
  const challenge = resource.data;
  const [editorOpen, setEditorOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const editorContainer = useRef<HTMLDivElement>(null);
  const [, refreshClock] = useState(0);
  useEffect(() => { const timer = window.setInterval(() => refreshClock((value) => value + 1), 15000); return () => window.clearInterval(timer); }, []);
  const canAnswer = !!challenge && challenge.can_answer && new Date(challenge.ends_at).getTime() > Date.now();
  const myAnswer = challenge?.answers.find((answer) => answer.is_mine);
  useEffect(() => { if (editorOpen) editorContainer.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }); }, [editorOpen]);
  function submitted() {
    setEditorOpen(false);
    resource.reload();
  }
  async function vote(operation: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await operation(); resource.reload(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '点赞失败'); }
    finally { setBusy(false); }
  }
  return <div className="dashboard-page knowledge-page"><Navbar title="高价值问题" leftArrow onLeftClick={() => navigate('/admin/knowledge/challenges')} right={<UserAvatarMenu />} fixed />
    <div className="knowledge-content"><KnowledgeState loading={resource.loading && !challenge} error={resource.error} retry={resource.reload} />
      {challenge && <>
        <section className="mac-card mac-card--pad"><div className="knowledge-toolbar"><h2>{challenge.title}</h2><ChallengeManagement challenge={challenge} onChanged={resource.reload} /></div>
          <p className="knowledge-muted">{challenge.questioner} · {challengeStateLabels[challenge.state]}{challenge.hidden ? ' · 已隐藏' : ''}</p>
          <p className="challenge-prize">{knowledgeMoney(challenge.bounty_fen)} <small className="knowledge-muted">最佳解答悬赏</small></p>
          <p className="knowledge-muted">{knowledgeTime(challenge.ends_at)} 截止{challenge.asked_at ? ` · 提问于 ${knowledgeTime(challenge.asked_at)}` : ''}</p>
          <p className="challenge-content">{challenge.content}</p>
          <div className="knowledge-note">到期按答案有效点赞选出最佳解答，管理员可处理异常结果；奖金按平台既有流程发放。每人每天最多点赞5个问题，答案另有每天5条额度。</div>
          <div className="challenge-actions"><button type="button" className="knowledge-like challenge-question-action" aria-pressed={challenge.liked_by_me} disabled={busy || !challenge.can_like || !canAnswer} onClick={() => void vote(() => likeChallenge(challenge))}>我也遇到 · {challenge.like_count}</button><button type="button" className="knowledge-button challenge-question-action" disabled={!canAnswer && !myAnswer} onClick={() => setEditorOpen(!editorOpen)}>{editorOpen ? '收起我的解答' : myAnswer ? '查看/编辑我的解答' : '我来解答'}</button></div>
        </section>
        {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
        {editorOpen && !myAnswer && <div ref={editorContainer}><ChallengeAnswerEditor key={challengeId} id={challengeId} canAnswer={canAnswer} onSubmitted={submitted} onCancel={() => setEditorOpen(false)} /></div>}
        <section className="mac-card mac-card--pad"><h3>{challenge.best_answer ? '社区优选解答' : '匿名解答'} · {challenge.answers.length}</h3><p className="challenge-notice">社区解答，未经专业验证。问题赞表达共性需求，答案赞决定默认获奖者。</p>
          {challenge.my_reward_status && <div className="knowledge-note">你的奖励已接入原奖励管理，可在<Link className="knowledge-link" to="/rewards">我的奖励</Link>查看实际处理状态。</div>}
          {!challenge.answers.length && <KnowledgeEmpty>暂无解答，期待你的经验与思路</KnowledgeEmpty>}
          {[...challenge.answers].sort((first, second) => Number(second.is_best) - Number(first.is_best)).map((answer) => <article key={answer.id} className={`challenge-answer${answer.is_best ? ' challenge-answer--best' : ''}`}><div className="knowledge-toolbar"><strong>{answer.author}{answer.is_best ? ' · 最佳解答' : ''}{answer.is_mine ? ' · 我的答案' : ''}</strong><div className="challenge-actions">{answer.is_mine && <button type="button" className="knowledge-link" disabled={!canAnswer || !answer.can_edit} title={answer.remaining_edits === 0 ? '本题修改机会已用完' : '每个问题的已发布解答仅可修改一次'} onClick={() => setEditorOpen(!editorOpen)}>{editorOpen ? '收起编辑' : '编辑'}</button>}<button type="button" className="knowledge-like" aria-pressed={answer.liked_by_me} disabled={busy || !answer.can_like || !canAnswer} onClick={() => void vote(() => likeChallengeAnswer(challenge.id, answer))}>有帮助 · {answer.like_count}</button></div></div><p className="challenge-content">{answer.content}</p><time className="knowledge-muted">{knowledgeTime(answer.submitted_at)}</time>{answer.is_mine && answer.remaining_edits === 0 && <p className="challenge-notice">本题修改机会已用完</p>}{answer.is_mine && editorOpen && <div ref={editorContainer}><ChallengeAnswerEditor key={challengeId} id={challengeId} canAnswer={canAnswer} onSubmitted={submitted} onCancel={() => setEditorOpen(false)} embedded /></div>}</article>)}
        </section>
      </>}
    </div></div>;
}
