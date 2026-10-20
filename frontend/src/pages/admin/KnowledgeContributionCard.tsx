import { fetchKnowledgeOverview } from '@/api/knowledge';
import { useAuthStore } from '@/stores/auth';
import { knowledgeLikeRule, useKnowledgeResource } from './knowledgeShared';
import KnowledgeRankCard, { contributorPreviewEntries } from './KnowledgeRankCard';

function VisibleKnowledgeContributionCard() {
  const resource = useKnowledgeResource('overview', () => fetchKnowledgeOverview());
  const overview = resource.data;
  return <KnowledgeRankCard title="知识贡献榜" hideTitle kind="combined" className="knowledge-home-card"
    notice={knowledgeLikeRule}
    subtitle={overview ? `${overview.month.replace('-', '年')}月 · 提单接单合并排名` : '提单接单合并排名'}
    entries={contributorPreviewEntries(overview?.top_three || [], 'combined')} to="/admin/knowledge"
    error={resource.error} loading={resource.loading && !overview} retry={resource.reload}
    footer={overview && <span>{overview.campaign_status === 'ended' ? '本期统计已结束 · 查看贡献记录' : '按月统计 · 以有效归档贡献排名'}</span>} />;
}

export default function KnowledgeContributionCard() {
  const canView = useAuthStore((state) => state.canViewKnowledgeContribution);
  return canView ? <VisibleKnowledgeContributionCard /> : null;
}
