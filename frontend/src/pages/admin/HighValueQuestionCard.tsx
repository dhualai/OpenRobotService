import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Trophy } from 'lucide-react';
import { fetchChallengePreview, type Challenge } from '@/api/knowledgeChallenges';
import { KnowledgeEmpty, KnowledgeState, knowledgeMoney, knowledgeTime, useKnowledgeResource } from './knowledgeShared';
import '@/shared/styles/knowledgeChallenges.css';

export const challengeStateLabels: Record<string, string> = {
  draft: '草稿', active: '进行中', paused: '已暂停', settling: '结果计算中',
  completed: '已结束 · 已选出最佳', unanswered: '已结束 · 暂无获奖解答',
  rejected: '已结束 · 不予奖励', cancelled: '已取消',
};

export function useChallengePolling(reload: () => void) {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => {
    const timer = window.setInterval(() => latest.current(), 60000);
    return () => window.clearInterval(timer);
  }, []);
}

export function ChallengeSummary({ challenge, rank }: { challenge: Challenge; rank?: number }) {
  return <Link to={`/admin/knowledge/challenges/${challenge.id}`} className="challenge-summary">
    {rank !== undefined && <span className="knowledge-board-preview__rank">{rank}</span>}
    <span className="challenge-summary__text"><strong>{challenge.title}</strong><small>{challenge.like_count}人也遇到 · {knowledgeTime(challenge.ends_at)}截止</small></span>
    <span className="knowledge-ranking-prize">{knowledgeMoney(challenge.bounty_fen)}<small>悬赏奖金</small></span>
  </Link>;
}

export default function HighValueQuestionCard() {
  const resource = useKnowledgeResource('challenge-preview', fetchChallengePreview);
  useChallengePolling(resource.reload);
  return <section className="mac-card mac-card--pad knowledge-board-card knowledge-home-card" aria-label="高价值问题榜单">
    <div className="knowledge-board-card__heading"><span className="knowledge-muted">共同解答复杂问题</span><Link className="knowledge-link" to="/admin/knowledge/challenges">查看全部 →</Link></div>
    <KnowledgeState loading={resource.loading && !resource.data} error={resource.error} retry={resource.reload} />
    {resource.data && <div className="knowledge-board-card__body">
      <div className="knowledge-board-cover" data-kind="questions" aria-hidden><span className="knowledge-board-cover__face"><Trophy size={28} /><strong>高价值问题</strong><small>TOP 3</small></span></div>
      {resource.data.items.length ? <ol className="challenge-preview" aria-label="高价值问题前三名">{resource.data.items.slice(0, 3).map((challenge, index) => <li key={challenge.id}><ChallengeSummary challenge={challenge} rank={index + 1} /></li>)}</ol> : <KnowledgeEmpty>暂无进行中的高价值问题</KnowledgeEmpty>}
    </div>}
    <div className="knowledge-card-footer">按问题点赞排序 · 到期按答案有效赞选出最佳解答</div>
  </section>;
}
