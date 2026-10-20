import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Navbar, Toast } from 'tdesign-mobile-react';
import { fetchKnowledgeReview, fetchReviewSimilar, submitKnowledgeReview } from '@/api/knowledge';
import { KnowledgeEmpty, KnowledgeState, useKnowledgeResource } from './knowledgeShared';
import { KnowledgeMarkdown } from './KnowledgeQuestionDetail';

function SimilarReports({ taskId }: { taskId: number }) {
  const resource = useKnowledgeResource(`similar:${taskId}`, () => fetchReviewSimilar(taskId));
  return <section className="mac-card mac-card--pad"><h3>相似工单参考</h3><p className="knowledge-muted">仅用于人工判断重复；检索分数不是相似百分比。</p>
    <KnowledgeState error={resource.error || (resource.data?.retrieval_status === 'unavailable' ? '相似工单检索暂不可用，不能据此判断没有重复问题' : undefined)} loading={resource.loading && !resource.data} retry={resource.reload} />
    {resource.data?.retrieval_status !== 'unavailable' && resource.data?.similar.length === 0 && <KnowledgeEmpty>暂无达到阈值的相似工单。</KnowledgeEmpty>}
    {resource.data?.similar.map((item) => <details className="knowledge-similar" key={item.task_id}><summary>{item.title}</summary>{item.report ? <KnowledgeMarkdown text={item.report.content} /> : <p className="knowledge-muted">暂无已通过归档报告</p>}</details>)}
  </section>;
}

export default function KnowledgeReviewPage() {
  const { reportId = '' } = useParams();
  const navigate = useNavigate();
  const resource = useKnowledgeResource(`review:${reportId}`, () => fetchKnowledgeReview(reportId));
  const report = resource.data;
  const [comment, setComment] = useState('');
  const [valuePoint, setValuePoint] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (outcome: 'approve' | 'reject') => {
    if (!report || busy) return;
    if (!comment.trim() || (outcome === 'approve' && !valuePoint.trim())) {
      Toast({ message: outcome === 'approve' ? '请填写审核意见和价值点' : '请填写驳回意见及修改建议', theme: 'warning' }); return;
    }
    setBusy(true);
    try {
      await submitKnowledgeReview(report, outcome, comment.trim(), valuePoint.trim());
      Toast({ message: outcome === 'approve' ? '内容审核通过，已提交财务审核' : '已驳回，意见仅当事人和你本人可见', theme: 'success' });
      navigate('/admin/knowledge?view=review', { replace: true });
    } catch (error) {
      Toast({ message: error instanceof Error ? error.message : '审核提交失败', theme: 'error' });
      resource.reload();
    } finally { setBusy(false); }
  };
  return <div className="dashboard-page knowledge-page"><Navbar title="匿名归档审核" leftArrow onLeftClick={() => navigate('/admin/knowledge?view=review')} fixed />
    <div className="knowledge-content"><KnowledgeState loading={resource.loading && !report} error={resource.error} retry={resource.reload} />
      {report && <>
        <div className="knowledge-note">双盲审核 · 报告第{report.version}版。提单人、接单人及身份相关附件链接已隐藏。</div>
        <section className="mac-card mac-card--pad"><h3>{report.title}</h3><KnowledgeMarkdown text={report.problem} /></section>
        <section className="mac-card mac-card--pad"><h3>接单人归档报告</h3><KnowledgeMarkdown text={report.content} /></section>
        {report.submit_status === 'submitted' ? <>
          <SimilarReports taskId={report.task_id} />
          <section className="mac-card mac-card--pad"><h3>审核结论</h3><label className="knowledge-field" htmlFor="knowledge-review-comment">审核意见（通过 / 驳回均必填）</label><textarea id="knowledge-review-comment" className="knowledge-textarea" maxLength={5000} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="说明完整性、真实性、可复用性，或驳回原因及修改建议" /><label className="knowledge-field" htmlFor="knowledge-review-value">价值点（通过时必填）</label><textarea id="knowledge-review-value" className="knowledge-textarea" maxLength={5000} value={valuePoint} onChange={(event) => setValuePoint(event.target.value)} placeholder="说明对故障排查、项目实施或行业复用的具体价值" /><div className="knowledge-actions"><Button theme="default" disabled={busy || !comment.trim()} onClick={() => void submit('reject')}>驳回补充</Button><Button theme="primary" disabled={busy || !comment.trim() || !valuePoint.trim()} loading={busy} onClick={() => void submit('approve')}>通过并提交财务</Button></div></section>
        </> : <section className="mac-card mac-card--pad"><h3>本人审核意见</h3><KnowledgeMarkdown text={report.review_comment} /><KnowledgeMarkdown text={report.value_point} /><p className="knowledge-muted">当前报告已不在待审状态，不能重复提交结论。</p></section>}
      </>}
    </div>
  </div>;
}
