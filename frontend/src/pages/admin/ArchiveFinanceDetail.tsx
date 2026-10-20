import { useEffect, useState } from 'react';
import { Button, Loading } from 'tdesign-mobile-react';
import { fetchArchiveFinanceDetail, fenToYuan, type ArchiveFinanceDetail as FinanceDetail } from '@/api/wxpay';
import { KnowledgeMarkdown } from './KnowledgeQuestionDetail';

export default function ArchiveFinanceDetail({ taskId, onClose }: { taskId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<FinanceDetail | null>(null);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError('');
    fetchArchiveFinanceDetail(taskId).then((value) => {
      if (!cancelled) setDetail(value);
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : '财务审核详情加载失败');
    });
    return () => { cancelled = true; };
  }, [taskId, attempt]);
  return <div>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <h3>工单 {taskId} · 财务审核资料</h3>
      <Button size="small" variant="outline" onClick={onClose}>关闭详情</Button>
    </div>
    {!detail && !error && <Loading text="加载中..." />}
    {error && <div role="alert">{error}<Button size="small" onClick={() => setAttempt((value) => value + 1)}>重新加载</Button></div>}
    {detail && <>
      <h4>{detail.title}</h4><KnowledgeMarkdown text={detail.problem} />
      <h4>归档报告</h4><KnowledgeMarkdown text={detail.content} />
      <h4>内容审核意见</h4><KnowledgeMarkdown text={detail.review_comment} />
      <h4>价值点</h4><KnowledgeMarkdown text={detail.value_point} />
      <p>三方奖励合计：¥{fenToYuan(detail.total_amount)}</p>
      <p style={{ fontSize: 12, color: '#888' }}>通过或驳回将同时处理该工单的三方奖励。通过后完成归档并按闭环月份入榜，实际微信到账以转账结果为准。</p>
    </>}
  </div>;
}
