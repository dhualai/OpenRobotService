import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Empty, Pagination, Space, Spin, Tag } from 'antd';
import { listLearningReviews, type LearningReviewTask } from '@/api/learningReviews';
import LearningTheme from '@/pages/admin/learning/LearningTheme';

export function reviewStatus(task: LearningReviewTask) {
  if (task.outcome === 'approved') return '已通过';
  if (task.outcome === 'rejected') return '已驳回';
  if (task.outcome === 'canceled' || task.status === 'canceled') return '已失效';
  if (task.status === 'closed') return '已结束';
  return task.assigned_to ? '待审核' : '待自动派单';
}

export default function LearningReviewInbox() {
  const navigate = useNavigate();
  const [tasks, setTasks] = useState<LearningReviewTask[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    listLearningReviews(page).then((result) => {
      if (active) { setTasks(result.items); setTotal(result.total); }
    }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '审核工单加载失败'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [page, refreshKey]);
  return <LearningTheme><section className="learner-review-inbox" aria-label="题目审核工单">
    <div className="learner-section-header"><h2>题目审核工单</h2><Button onClick={() => setRefreshKey((current) => current + 1)}>刷新审核工单</Button></div>
    <p>当前指派给我的题目审核工单。</p>
    {error ? <Alert type="error" showIcon message={error} /> : loading ? <Spin aria-label="加载审核工单" /> : <>
      {!tasks.length && <Empty description="暂无相关题目审核工单" />}
      <Space direction="vertical" style={{ width: '100%' }}>{tasks.map((task) => <article key={task.id} className="learner-review-card">
        <Space wrap><Tag>{reviewStatus(task)}</Tag><span>#{task.id}</span></Space><h3>{task.title}</h3>
        <Button type="primary" onClick={() => navigate(`/tasks/reviews/${task.id}`)}>{task.can_review ? '去审核' : '查看审核工单'}</Button>
      </article>)}</Space>
      {total > 20 && <Pagination current={page} total={total} pageSize={20} showSizeChanger={false} onChange={setPage} />}
    </>}
  </section></LearningTheme>;
}
