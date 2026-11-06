import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import {
  changeChallengeState, fetchChallenge, fetchMyChallengeAnswer, likeChallenge, likeChallengeAnswer,
  overrideChallengeResult, publishChallenge, saveChallengeAnswer, uploadChallengeAnswerImage, type ChallengeDetail, type MyChallengeAnswer,
} from '@/api/knowledgeChallenges';
import { challengeStateLabels, useChallengePolling } from './HighValueQuestionCard';
import { localDateInput } from './KnowledgeChallenges';
import { KnowledgeEmpty, KnowledgeState, knowledgeMoney, knowledgeTime, useKnowledgeResource } from './knowledgeShared';
import ChallengeAnswerImages from './ChallengeAnswerImages';
import { setShareMeta, shareImageUrl } from '@/shared/utils/shareMeta';
import { setupWechatShare } from '@/shared/utils/wechatJsSdk';
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
  const [images, setImages] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [answerState, setAnswerState] = useState<MyChallengeAnswer | null>(null);
  const version = useRef(0);
  const saved = useRef('');
  const savedImages = useRef('[]');
  const textarea = useRef<HTMLTextAreaElement>(null);
  const uploadInProgress = useRef(false);
  const insertionEnd = useRef<number | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (uploading || insertionEnd.current === null || !textarea.current) return;
    textarea.current.focus();
    textarea.current.setSelectionRange(insertionEnd.current, insertionEnd.current);
    insertionEnd.current = null;
  }, [uploading, content]);
  useEffect(() => {
    if (resource.data && !ready) {
      setContent(resource.data.content); saved.current = resource.data.content;
      setImages(resource.data.images || []); savedImages.current = JSON.stringify(resource.data.images || []);
      version.current = resource.data.version; setReady(true);
      setAnswerState(resource.data);
      setStatus(resource.data.submitted_at ? '已有已发布答案，修改后需再次发布' : '草稿仅自己可见');
    }
  }, [resource.data, ready]);
  const persist = useCallback((value: string, submit: boolean, imageList: string[]) => {
    const operation = queue.current.then(async () => {
      if (!submit && value === saved.current && JSON.stringify(imageList) === savedImages.current) return;
      const answer = await saveChallengeAnswer(id, value, version.current, submit, imageList);
      version.current = answer.version; saved.current = value;
      savedImages.current = JSON.stringify(imageList);
      if (mounted.current) { setAnswerState(answer); setStatus(submit ? '答案已发布' : '草稿已保存，仅自己可见'); setError(''); }
    });
    queue.current = operation.catch(() => undefined);
    return operation;
  }, [id]);
  const canSubmit = canAnswer && answerState?.can_submit === true;
  const isEditing = !!answerState?.submitted_at;
  const editExhausted = isEditing && answerState.remaining_edits === 0;
  useEffect(() => {
    if (!ready || !canSubmit || busy || uploading || (content === saved.current && JSON.stringify(images) === savedImages.current)) return;
    const timer = window.setTimeout(() => {
      void persist(content, false, images).catch((failure: unknown) => {
        if (mounted.current) setError(failure instanceof Error ? failure.message : '自动保存失败，请重试');
      });
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [content, images, ready, canSubmit, busy, uploading, persist]);
  async function uploadImages(files: File[]) {
    if (!canSubmit || busy || uploadInProgress.current || !files.length) return;
    if (files.some((file) => !/\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name))) { setError('仅支持 PNG、JPG、GIF、WEBP、BMP 图片'); return; }
    if (files.some((file) => file.size > 10 * 1024 * 1024)) { setError('单张图片不能超过10MB'); return; }
    let start = textarea.current?.selectionStart ?? content.length;
    let end = textarea.current?.selectionEnd ?? start;
    let nextContent = content;
    uploadInProgress.current = true;
    setUploading(true); setError('');
    try {
      for (const file of files) {
        const url = await uploadChallengeAnswerImage(file);
        if (!mounted.current) return;
        const alt = file.name.replace(/[\\[\]\r\n]/g, '_');
        const markdown = `\n\n![${alt}](${url})\n\n`;
        const updated = nextContent.slice(0, start) + markdown + nextContent.slice(end);
        if (updated.length > 20000) throw new Error('插入图片后答案内容不能超过20000字，请缩短内容后重试');
        nextContent = updated;
        start += markdown.length;
        end = start;
        insertionEnd.current = start;
        setContent(nextContent);
      }
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : '图片上传失败，请重试'); }
    finally { uploadInProgress.current = false; if (mounted.current) setUploading(false); }
  }
  async function publish(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit || busy || uploading) return;
    setBusy(true); setError('');
    try { await persist(content, true, images); onSubmitted(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : '发布失败'); }
    finally { if (mounted.current) setBusy(false); }
  }
  return <section className={embedded ? 'challenge-answer-editor' : 'mac-card mac-card--pad'}><h3>{isEditing ? '编辑我的解答' : '我的解答'}</h3><KnowledgeState loading={resource.loading && !ready} error={resource.error} retry={resource.reload} />
    {ready && <form className="challenge-form" onSubmit={publish}><p className="challenge-notice">回答将展示你的用户名，请勿填写电话、客户信息或原工单内部链接。每人每天最多点赞5条不同答案，不能给自己的答案点赞。</p>
      <p role="note" className="knowledge-note">{editExhausted ? '本题解答已修改过一次，不能再次编辑。' : isEditing ? '每个问题的已发布解答仅可修改一次。提交修改后不能再次编辑；保存草稿或取消编辑不消耗修改次数。' : '首次发布后，每个问题的解答仅可再修改一次，请确认内容后发布。'}</p>
      <p className="challenge-notice">支持 Markdown。上传或粘贴图片后将插入光标位置，预览中可查看图文排版。单张图片不超过10MB，请勿上传客户隐私、账号口令等敏感信息。</p>
      <label>上传解答图片<input type="file" accept=".png,.jpg,.jpeg,.gif,.webp,.bmp" multiple disabled={busy || uploading || !canSubmit} onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ''; void uploadImages(files); }} /></label>
      {uploading && <p className="knowledge-muted" role="status">图片上传中…</p>}
      <div className="challenge-answer-composer">
        <label>答案内容<textarea ref={textarea} className="knowledge-textarea" value={content} onChange={(event) => setContent(event.target.value)} onPaste={(event) => {
          const files = Array.from(event.clipboardData.files).filter((file) => file.type.startsWith('image/'));
          if (!files.length) return;
          event.preventDefault();
          void uploadImages(files);
        }} required maxLength={20000} disabled={busy || uploading || !canSubmit} /></label>
        <section className="challenge-answer-preview" aria-label="答案预览"><strong>答案预览</strong><div className="challenge-answer-body"><MarkdownRenderer content={content} /></div></section>
      </div>
      <ChallengeAnswerImages images={images} disabled={busy || uploading || !canSubmit} onRemove={(index) => setImages((previous) => previous.filter((_, position) => position !== index))} />
      <span className="knowledge-muted" role="status">{editExhausted ? '本题修改机会已用完' : canAnswer ? status : '问题已截止或暂停，不能继续提交'}</span>
      {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
      <div className="challenge-actions"><button type="submit" className="knowledge-button" disabled={!canSubmit || busy || uploading || !content.trim()}>{busy ? '发布中…' : isEditing ? '提交修改' : '发布答案'}</button><button type="button" className="knowledge-link" disabled={!canSubmit || busy || uploading} onClick={() => void persist(content, false, images).catch((failure: unknown) => setError(failure instanceof Error ? failure.message : '保存失败'))}>保存草稿</button>{onCancel && <button type="button" className="knowledge-link" disabled={busy || uploading} onClick={onCancel}>取消编辑</button>}</div>
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

  // 转发卡片（ORS-1086）：按当前问题生成卡片——标题=问题标题，描述里带上悬赏奖金与截止时间。
  // 企微无 JS-SDK，只能读 head（setShareMeta 写 og 标签）；微信内再由 JS-SDK 精确指定字段。
  useEffect(() => {
    if (!challenge) return;
    const title = challenge.title || '高价值问题';
    const desc = `悬赏 ${knowledgeMoney(challenge.bounty_fen)} · ${challenge.like_count} 人也遇到 · ${knowledgeTime(challenge.ends_at)}截止 · 点击查看并解答`;
    setShareMeta({ title, description: desc });
    void setupWechatShare({ title, desc, link: window.location.href, imgUrl: shareImageUrl() });
  }, [challenge?.id, challenge?.title, challenge?.bounty_fen, challenge?.like_count, challenge?.ends_at]);
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
          <div className="challenge-actions"><button type="button" className="knowledge-like challenge-question-action" aria-pressed={challenge.liked_by_me} disabled={busy || !challenge.can_like || !canAnswer} onClick={() => void vote(() => likeChallenge(challenge))}>我也遇到 · {challenge.like_count}</button>{!myAnswer && <button type="button" className="knowledge-button challenge-question-action" disabled={!canAnswer} onClick={() => setEditorOpen(!editorOpen)}>{editorOpen ? '收起我的解答' : '我来解答'}</button>}</div>
        </section>
        {error && <p role="alert" className="knowledge-discussion-error">{error}</p>}
        {editorOpen && !myAnswer && <div ref={editorContainer}><ChallengeAnswerEditor key={challengeId} id={challengeId} canAnswer={canAnswer} onSubmitted={submitted} onCancel={() => setEditorOpen(false)} /></div>}
        <section className="mac-card mac-card--pad"><h3>{challenge.best_answer ? '社区优选解答' : '解答'} · {challenge.answers.length}</h3><p className="challenge-notice">社区解答，未经专业验证。问题赞表达共性需求，答案赞决定默认获奖者。</p>
          {challenge.my_reward_status && <div className="knowledge-note">你的奖励已接入原奖励管理，可在<Link className="knowledge-link" to="/rewards">我的奖励</Link>查看实际处理状态。</div>}
          {!challenge.answers.length && <KnowledgeEmpty>暂无解答，期待你的经验与思路</KnowledgeEmpty>}
          {[...challenge.answers].sort((first, second) => Number(second.is_mine) - Number(first.is_mine) || Number(second.is_best) - Number(first.is_best)).map((answer) => <article key={answer.id} className={`challenge-answer${answer.is_best ? ' challenge-answer--best' : ''}`}><div className="knowledge-toolbar"><strong>{answer.author}{answer.is_best ? ' · 最佳解答' : ''}{answer.is_mine ? ' · 我的答案' : ''}</strong><div className="challenge-actions">{answer.is_mine && <button type="button" className="knowledge-link" disabled={!canAnswer || !answer.can_edit} title={answer.remaining_edits === 0 ? '本题修改机会已用完' : '每个问题的已发布解答仅可修改一次'} onClick={() => setEditorOpen(!editorOpen)}>{editorOpen ? '收起编辑' : '编辑'}</button>}<button type="button" className="knowledge-like" aria-pressed={answer.liked_by_me} disabled={busy || !answer.can_like || !canAnswer} onClick={() => void vote(() => likeChallengeAnswer(challenge.id, answer))}>有帮助 · {answer.like_count}</button></div></div><div className="challenge-answer-body"><MarkdownRenderer content={answer.content} /></div><ChallengeAnswerImages images={answer.images} /><time className="knowledge-muted">{knowledgeTime(answer.submitted_at)}</time>{answer.is_mine && answer.remaining_edits === 0 && <p className="challenge-notice">本题修改机会已用完</p>}{answer.is_mine && editorOpen && <div ref={editorContainer}><ChallengeAnswerEditor key={challengeId} id={challengeId} canAnswer={canAnswer} onSubmitted={submitted} onCancel={() => setEditorOpen(false)} embedded /></div>}</article>)}
        </section>
      </>}
    </div></div>;
}
