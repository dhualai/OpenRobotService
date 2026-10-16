import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Navbar } from 'tdesign-mobile-react';
import UserAvatarMenu from '@/shared/components/UserAvatarMenu';
import {
  fetchContributors, fetchKnowledgeOverview, fetchKnowledgeQuestions, fetchMyReviewHistory,
  fetchPendingReviews, type KnowledgeOverview,
} from '@/api/knowledge';
import {
  KnowledgeAvatar, KnowledgeEmpty, KnowledgeState, knowledgeLikeRule, knowledgeMoney, knowledgeTime,
  useKnowledgeResource,
} from './knowledgeShared';
import KnowledgeRankCard, { contributorPreviewEntries } from './KnowledgeRankCard';
import KnowledgeQuestionCard from './KnowledgeQuestionCard';

const tabs = [['questions', '问题榜单'], ['questioners', '最佳提问人榜单'], ['answerers', '最佳解答人榜单']] as const;

function Questions({ month }: { month: string }) {
  const [params, setParams] = useSearchParams();
  const sort = params.get('sort') === 'likes' ? 'likes' : 'archived_at';
  const [page, setPage] = useState(1);
  const [allMonths, setAllMonths] = useState(false);
  const resource = useKnowledgeResource(`questions:${allMonths ? 'all' : month}:${sort}:${page}`, () => fetchKnowledgeQuestions(allMonths ? '' : month, sort, page));
  const data = resource.data;
  return <>
    <div className="knowledge-toolbar"><h3>知识列表</h3><span className="knowledge-muted">{data ? `共${data.total}条知识` : '公开归档问题'}</span></div>
    <div className="knowledge-toolbar">
      <select className="knowledge-select" aria-label="问题范围" value={allMonths ? 'all' : 'month'} onChange={(event) => { setAllMonths(event.target.value === 'all'); setPage(1); }}><option value="month">当月归档问题</option><option value="all">本期全部问题</option></select>
      <select className="knowledge-select" aria-label="问题排序" value={sort} onChange={(event) => { setParams((previous) => { const next = new URLSearchParams(previous); next.set('sort', event.target.value); return next; }); setPage(1); }}><option value="archived_at">最新归档</option><option value="likes">最多有效点赞</option></select>
    </div>
    <KnowledgeState error={resource.error} loading={resource.loading && !data} retry={resource.reload} />
    {data?.items.length === 0 && <KnowledgeEmpty>暂无有效归档问题，历史工单不计入本期活动。</KnowledgeEmpty>}
    {data?.items.map((question) => <KnowledgeQuestionCard key={question.task_id} question={question} onChange={resource.reload} />)}
    {data && data.total > 20 && <div className="knowledge-pagination"><button className="knowledge-link" disabled={page === 1 || resource.loading} onClick={() => setPage(page - 1)}>上一页</button><span className="knowledge-muted">第{page}页 · 共{data.total}题</span><button className="knowledge-link" disabled={page * 20 >= data.total || resource.loading} onClick={() => setPage(page + 1)}>下一页</button></div>}
    <p className="knowledge-muted">优质问题按有效点赞数排序，可查看完整问题与归档报告。</p>
  </>;
}

function BoardPreviews({ overview, month }: { overview: KnowledgeOverview; month: string }) {
  const resource = useKnowledgeResource(`question-preview:${month}`, () => fetchKnowledgeQuestions(month, 'likes', 1, 3));
  const period = `${month.replace('-', '年')}月`;
  return <div className="knowledge-board-stack">
    <KnowledgeRankCard title="问题榜单" kind="questions" subtitle={`${period} · 按有效点赞排序`}
      to={`/admin/knowledge?tab=questions&month=${month}&sort=likes`}
      entries={(resource.data?.items || []).slice(0, 3).map((question, position) => ({ id: question.task_id, rank: position + 1, label: question.title, description: `提单：${question.questioner.name} · 接单：${question.answerer.name}`, metric: `${question.valid_like_count}赞` }))}
      loading={resource.loading && !resource.data} error={resource.error} retry={resource.reload}
      footer={<span>按有效点赞排序，点击查看完整问题与解答</span>} />
    <KnowledgeRankCard title="最佳提问人榜单" kind="questioners" subtitle={`${period} · 综合月榜前三`}
      to={`/admin/knowledge?tab=questioners&month=${month}`} entries={contributorPreviewEntries(overview.top_three, 'questioners')}
      footer={<span>提单、接单合并排名 · 当前展示提单贡献</span>} />
    <KnowledgeRankCard title="最佳解答人榜单" kind="answerers" subtitle={`${period} · 综合月榜前三`}
      to={`/admin/knowledge?tab=answerers&month=${month}`} entries={contributorPreviewEntries(overview.top_three, 'answerers')}
      footer={<span>提单、接单合并排名 · 当前展示接单贡献</span>} />
  </div>;
}

function Contributors({ month, focus }: { month: string; focus: string }) {
  const navigate = useNavigate();
  const resource = useKnowledgeResource(`contributors:${month}`, () => fetchContributors(month));
  return <section className="mac-card mac-card--pad">
    <h3>{month} 综合贡献月榜 · 前十名</h3>
    <div className="knowledge-note">提单、接单合并排名，同数量按有效获赞排序。当前突出显示{focus === 'questioners' ? '提单' : '接单'}贡献。</div>
    <KnowledgeState error={resource.error} loading={resource.loading && !resource.data} retry={resource.reload} />
    {resource.data?.items.length === 0 && <KnowledgeEmpty>本月暂无有效贡献，完成归档审核后即可参与。</KnowledgeEmpty>}
    <ol className="knowledge-people-grid" aria-label="综合月榜前十名">{resource.data?.items.filter((person) => person.rank <= 10).slice(0, 10).map((person) => <li key={person.user_id}><button type="button" className="knowledge-person-tile" aria-label={`查看${person.name}的工单`} onClick={() => navigate(`/admin/knowledge/contributors/${encodeURIComponent(person.user_id)}?month=${month}&role=${focus === 'questioners' ? 'questioner' : 'answerer'}&board=${focus}`)}>
      <span className="knowledge-person-tile__portrait"><KnowledgeAvatar user={person} /><span>{person.rank}</span></span><strong title={person.name}>{person.name}</strong>
      <span className="knowledge-muted"><b>{focus === 'questioners' ? `提单${person.question_count}` : `接单${person.answer_count}`}</b><br />{focus === 'questioners' ? `接单${person.answer_count}` : `提单${person.question_count}`} · 综合{person.contribution_count}</span>
      <span className="knowledge-muted">获赞{person.valid_like_count}{person.tied ? ' · 同分' : ''}</span>
      <span className="knowledge-ranking-prize">{person.estimated_reward_fen ? knowledgeMoney(person.estimated_reward_fen) : '—'}</span>
    </button></li>)}</ol>
  </section>;
}

function PendingReviews() {
  const navigate = useNavigate();
  const resource = useKnowledgeResource('pending-reviews', fetchPendingReviews);
  return <><KnowledgeState error={resource.error} loading={resource.loading && !resource.data} retry={resource.reload} />
    {resource.data?.items.length === 0 && <KnowledgeEmpty>暂无分配给你的待审报告。接单人提交归档报告并派给你后，会出现在这里；待审队列不受活动月份限制。</KnowledgeEmpty>}
    {resource.data?.items.map((report) => <section key={report.report_id} className="mac-card mac-card--pad"><h3>{report.title}</h3><p className="knowledge-excerpt">{report.problem}</p><div className="knowledge-toolbar"><span className="knowledge-muted">匿名审核 · 报告第{report.version}版</span><button type="button" className="knowledge-link" onClick={() => navigate(`/admin/knowledge/reviews/${report.report_id}`)}>查看报告与相似工单 →</button></div></section>)}
  </>;
}

function MyReviewHistory() {
  const resource = useKnowledgeResource('my-review-history', fetchMyReviewHistory);
  return <><KnowledgeState error={resource.error} loading={resource.loading && !resource.data} retry={resource.reload} />
    {resource.data?.items.length === 0 && <KnowledgeEmpty>暂无本人审核意见。</KnowledgeEmpty>}
    {resource.data?.items.map((entry) => <section className="mac-card mac-card--pad" key={entry.id}><h3>{entry.outcome === 'approved' ? '已通过' : '已驳回'} · 报告第{entry.version}版</h3><p className="knowledge-excerpt">{entry.review_comment}</p>{entry.value_point && <div className="knowledge-note">价值点：{entry.value_point}</div>}<span className="knowledge-muted">{knowledgeTime(entry.created_at)} · 仅本人意见记录</span></section>)}
  </>;
}

export default function KnowledgeCenter() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = tabs.some(([value]) => value === params.get('tab')) ? params.get('tab')!
    : ['review', 'history'].includes(params.get('view') || '') ? 'questions' : null;
  const view = params.get('view') || 'public';
  const month = params.get('month') || '';
  const resource = useKnowledgeResource(`overview:${month}`, () => fetchKnowledgeOverview(month || undefined));
  const overview = resource.data;
  const selectedMonth = month || overview?.month || '2026-11';
  const pageTitle = tab === 'questions' ? view === 'review' ? '评审工作台' : view === 'history' ? '我的审核意见' : '知识列表' : tab ? tabs.find(([value]) => value === tab)![1] : '知识贡献榜';
  const showBoards = () => setParams((previous) => { const next = new URLSearchParams(previous); next.delete('tab'); next.delete('view'); next.delete('sort'); return next; });
  const showTab = (nextTab: string, nextView = 'public') => setParams((previous) => { const next = new URLSearchParams(previous); next.set('tab', nextTab); next.set('view', nextView); return next; });
  return <div className="dashboard-page knowledge-page">
    <Navbar title={pageTitle} leftArrow onLeftClick={() => tab ? showBoards() : navigate('/admin')} right={<UserAvatarMenu />} fixed />
    <div className="knowledge-content">
      <div className="knowledge-toolbar"><h2>让每一次解决，都成为知识</h2><select className="knowledge-select" aria-label="榜单月份" value={selectedMonth} onChange={(event) => setParams((previous) => { const next = new URLSearchParams(previous); next.set('month', event.target.value); return next; })}>{(overview?.months || ['2026-11', '2026-12']).map((value) => <option key={value} value={value}>{value.replace('-', '年')}月</option>)}</select></div>
      <KnowledgeState error={resource.error} loading={resource.loading && !overview} retry={resource.reload} />
      {overview && <>
        <div className="knowledge-note">{overview.campaign_status === 'ended' ? '本期统计已结束，可查看归档知识与贡献榜单。' : '按月统计 · 以有效归档贡献排名'}<br />提单、接单合并排名，贡献数相同按有效获赞数排序。<br />{knowledgeLikeRule}</div>
        {!tab && <div className="knowledge-review-entry">{overview.can_review ? <><button type="button" className="knowledge-link" aria-label="进入评审工作台" onClick={() => showTab('questions', 'review')}>评审工作台 · 待审{overview.pending_review_count || 0} →</button><span className="knowledge-muted">查看分配给你的匿名报告及本人审核意见</span></> : <span className="knowledge-muted">评审视角仅向平台指定名单及已有审核任务的人员开放。</span>}</div>}
        {!tab ? <BoardPreviews overview={overview} month={selectedMonth} /> : <>
          <button type="button" className="knowledge-link" onClick={showBoards}>← 返回榜单概览</button>
          <div className="knowledge-tabs" role="tablist" aria-label="知识榜单">{tabs.map(([value, label]) => <button type="button" role="tab" key={value} aria-selected={tab === value} onClick={() => showTab(value)}>{label}</button>)}</div>
          {tab === 'questions' && overview.can_review && <div className="knowledge-subtabs">{[['public', '知识列表'], ['review', '待我审核'], ['history', '我的审核意见']].map(([value, label]) => <button type="button" key={value} aria-pressed={view === value} onClick={() => showTab(tab, value)}>{label}</button>)}</div>}
          {tab === 'questions' ? view === 'review' || view === 'history' ? !overview.can_review ? <KnowledgeEmpty>你不在指定审核名单中，且暂无分配给你的审核任务。如需参与审核，请联系平台管理员。</KnowledgeEmpty> : view === 'review' ? <PendingReviews /> : <MyReviewHistory /> : <Questions key={selectedMonth} month={selectedMonth} /> : <Contributors month={selectedMonth} focus={tab} />}
        </>}
      </>}
    </div>
  </div>;
}
