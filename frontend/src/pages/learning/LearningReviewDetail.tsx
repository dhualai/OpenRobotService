import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Checkbox, Input, Space, Spin, Tag } from 'antd';
import { decideLearningReview, getLearningReview, type LearningReviewDetail as ReviewDetail } from '@/api/learningReviews';
import LearningTheme from '@/pages/admin/learning/LearningTheme';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import { reviewStatus } from './LearningReviewInbox';
import './learner.css';

export default function LearningReviewDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [verified, setVerified] = useState(false);
  const [reason, setReason] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setDetail(null); setVerified(false); setReason('');
    if (!taskId) { setError('缺少审核工单号'); setLoading(false); return; }
    getLearningReview(taskId).then((result) => { if (active) setDetail(result); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '审核工单加载失败'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [taskId, refreshKey]);
  const decide = async (action: 'approve' | 'reject') => {
    if (!taskId || !detail || saving) return;
    setSaving(true); setError('');
    try {
      setDetail(await decideLearningReview(taskId, { revision: detail.revision, action, verified, reason }));
      setVerified(false); setReason('');
    } catch (cause) { setError(cause instanceof Error ? cause.message : '审核失败，请重试'); }
    finally { setSaving(false); }
  };
  return <LearningTheme><main className="learner-review-detail">
    <Space><Button onClick={() => navigate('/tasks/learning')}>返回学习任务</Button><Button disabled={saving} onClick={() => setRefreshKey((current) => current + 1)}>刷新工单</Button></Space>
    <h1>题目审核工单</h1>
    {error && <Alert type="error" role="alert" showIcon message={error} />}
    {loading ? <Spin aria-label="正在加载审核工单" /> : detail && <>
      <Tag>{reviewStatus(detail)}</Tag><h2>{detail.title}</h2>
      {detail.stale && <Alert type="warning" message="文章已停用或题目版本已变更，本次内容不能审核，请联系提交人重新提交。" />}
      {detail.reason && <Alert type="info" message={`审核意见：${detail.reason}`} />}
      {detail.article && <article className="learner-review-card"><h2>{detail.article.title}</h2><p>{detail.article.summary}</p>
        <p>{detail.article.applicability}</p><ul>{detail.article.key_points.map((point, index) => <li key={index}>{point.text}</li>)}</ul>
        <MarkdownRenderer content={detail.article.method} /><p>{detail.article.cautions}</p><p>{detail.article.example}</p>
      </article>}
      {detail.question && <article className="learner-review-card"><h2>待审核题目</h2><h3>{detail.question.stem}</h3>
        {detail.question.options.map((option) => <p key={option.key}>{option.key}：{option.text}</p>)}
        <p>正确答案：{detail.question.correct_answer.join('、')}</p><p>解析：{detail.question.explanation}</p>
        <blockquote>文章依据：{detail.question.source_excerpt}</blockquote>
      </article>}
      {detail.can_review && <section className="learner-review-card" aria-label="审核操作">
        <Checkbox checked={verified} disabled={saving} onChange={(event) => setVerified(event.target.checked)}>已核对题干、选项、答案、解析、文章依据和脱敏结果</Checkbox>
        <Input.TextArea aria-label="审核意见" placeholder="驳回时必填，请说明需要修改的问题" maxLength={2000} rows={3} value={reason} disabled={saving} onChange={(event) => setReason(event.target.value)} />
        <Space><Button type="primary" disabled={!verified || saving} loading={saving} onClick={() => void decide('approve')}>审核通过</Button>
          <Button danger disabled={!reason.trim() || saving} onClick={() => void decide('reject')}>驳回题目</Button></Space>
      </section>}
    </>}
  </main></LearningTheme>;
}
