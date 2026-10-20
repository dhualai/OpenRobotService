import { useState, type FormEvent } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { createKnowledgeDiscussion, fetchKnowledgeDiscussions } from '@/api/knowledge';
import { KnowledgeAvatar, KnowledgeEmpty, KnowledgeState, knowledgeTime, useKnowledgeResource } from './knowledgeShared';

export default function KnowledgeDiscussion({ taskId }: { taskId: string }) {
  const [page, setPage] = useState(1);
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const resource = useKnowledgeResource(`discussions:${taskId}:${page}`, () => fetchKnowledgeDiscussions(taskId, page));
  const data = resource.data;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !content.trim()) return;
    setBusy(true);
    setSubmitError('');
    try {
      await createKnowledgeDiscussion(taskId, content.trim());
      setContent('');
      if (page === 1) resource.reload(); else setPage(1);
      Toast({ message: '讨论已发布', theme: 'success' });
    } catch (error) { setSubmitError(error instanceof Error ? error.message : '发布失败，请重试'); }
    finally { setBusy(false); }
  };
  return <section className="mac-card mac-card--pad" aria-label="讨论区">
    <div className="knowledge-toolbar"><h3>讨论区</h3><span className="knowledge-muted">{data ? `${data.total}条讨论` : '问题交流'}</span></div>
    <p className="knowledge-muted">围绕问题和解决方案交流，请勿发布客户隐私、账号口令或审核人身份信息。讨论不计入贡献排名。</p>
    <form onSubmit={submit} className="knowledge-discussion-form"><label className="knowledge-field" htmlFor="knowledge-discussion-content">参与讨论</label><textarea id="knowledge-discussion-content" className="knowledge-textarea" maxLength={2000} value={content} onChange={(event) => setContent(event.target.value)} placeholder="分享你的经验，或补充需要讨论的问题…" disabled={busy} />
      <div className="knowledge-toolbar"><span className="knowledge-muted">{content.length}/2000</span><button type="submit" className="knowledge-like" disabled={busy || !content.trim()}>{busy ? '正在发布…' : '发布讨论'}</button></div>
      {submitError && <p role="alert" className="knowledge-discussion-error">{submitError}</p>}
    </form>
    <KnowledgeState loading={resource.loading && !data} error={resource.error} retry={resource.reload} />
    {data?.items.length === 0 && <KnowledgeEmpty>还没有讨论，欢迎分享你的经验。</KnowledgeEmpty>}
    {data?.items.map((entry) => <article key={entry.id} className="knowledge-discussion-entry">{entry.author ? <KnowledgeAvatar user={entry.author} /> : <span className="knowledge-avatar" aria-label="匿名用户">匿</span>}<div><strong>{entry.author?.name || '匿名用户'}</strong><time className="knowledge-muted" dateTime={entry.created_at}>{knowledgeTime(entry.created_at)}</time><p>{entry.content}</p></div></article>)}
    {data && data.total > 20 && <div className="knowledge-pagination"><button className="knowledge-link" disabled={page === 1 || resource.loading} onClick={() => setPage(page - 1)}>上一页讨论</button><span className="knowledge-muted">第{page}页 · 共{data.total}条</span><button className="knowledge-link" disabled={page * 20 >= data.total || resource.loading} onClick={() => setPage(page + 1)}>下一页讨论</button></div>}
  </section>;
}
