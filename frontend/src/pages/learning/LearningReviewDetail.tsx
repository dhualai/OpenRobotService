import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Alert, Button, Input, Space, Spin, Tag } from 'antd';
import { decideLearningReview, getLearningReview, type LearningReviewDetail as ReviewDetail } from '@/api/learningReviews';
import LearningTheme from '@/pages/admin/learning/LearningTheme';
import MarkdownRenderer from '@/shared/components/MarkdownRenderer';
import TaskCategoryTabs from '@/shared/components/TaskCategoryTabs';
import { useTaskCounts } from '@/shared/components/TaskCountsContext';
import ReviewContentEditor from './ReviewContentEditor';
import { reviewStatus } from './LearningReviewInbox';
import './learner.css';

export default function LearningReviewDetail() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { refresh: refreshCounts } = useTaskCounts();
  const [detail, setDetail] = useState<ReviewDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState<'question' | 'article' | null>(null);
  const [reason, setReason] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(''); setDetail(null); setEditing(null); setReason('');
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
      setDetail(await decideLearningReview(taskId, { revision: detail.revision, action, verified: action === 'approve', reason }));
      setReason(''); refreshCounts();
    } catch (cause) { setError(cause instanceof Error ? cause.message : '审核失败，请重试'); }
    finally { setSaving(false); }
  };
  return <LearningTheme><main className="learner-review-detail">
    <TaskCategoryTabs />
    <Space><Button disabled={saving || !!editing} onClick={() => navigate('/tasks')}>返回工单任务</Button><Button disabled={saving || !!editing} onClick={() => setRefreshKey((current) => current + 1)}>刷新工单</Button></Space>
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
        <Space wrap>
          <Button type="primary" disabled={saving || !detail.question || !detail.article} onClick={() => setEditing('question')}>编辑题目</Button>
          <Button type="primary" disabled={saving || !detail.question || !detail.article} onClick={() => setEditing('article')}>编辑文章</Button>
        </Space>
        <p>可直接通过，也可先编辑并保存。点击审核通过即确认已核对题目、文章依据与脱敏结果；通过后仍需在后台启用题目。</p>
        <Input.TextArea aria-label="审核意见" placeholder="驳回时必填，请说明需要修改的问题" maxLength={2000} rows={3} value={reason} disabled={saving} onChange={(event) => setReason(event.target.value)} />
        <Space><Button type="primary" disabled={saving || !!editing} loading={saving} onClick={() => void decide('approve')}>审核通过</Button>
          <Button danger disabled={!reason.trim() || saving || !!editing} onClick={() => void decide('reject')}>驳回题目</Button></Space>
      </section>}
    </>}
    {editing && detail && <ReviewContentEditor detail={detail} initialTab={editing} onClose={() => setEditing(null)}
      onSaved={(result) => { setDetail(result); setEditing(null); setError(''); refreshCounts(); }} />}
  </main></LearningTheme>;
}
