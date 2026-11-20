import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Toast } from 'tdesign-mobile-react';
import * as api from '@/api/knowledge';
import { fetchRewardStats } from '@/api/wxpay';
import { POLL_INTERVAL_MS } from '@/config/poll';
import { ApiError } from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import KnowledgeContributionCard from '../admin/KnowledgeContributionCard';
import KnowledgeCenter from '../admin/KnowledgeCenter';
import KnowledgeReviewPage from '../admin/KnowledgeReviewPage';
import KnowledgeQuestionDetail from '../admin/KnowledgeQuestionDetail';
import KnowledgeContributorPage from '../admin/KnowledgeContributorPage';
import Dashboard from '../admin/Dashboard';
import { fetchChallengePreview } from '@/api/knowledgeChallenges';

vi.mock('@/api/knowledgeChallenges', () => ({ fetchChallengePreview: vi.fn() }));

vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/SubscriptionReminder', () => ({ default: () => null }));
vi.mock('@/api/dashboard', () => ({
  fetchDashboardSummaryAll: vi.fn().mockRejectedValue(new Error('看板暂不可用')),
  syncWecomProjects: vi.fn(),
}));
vi.mock('@/api/wxpay', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/wxpay')>(),
  fetchRewardStats: vi.fn(),
}));
vi.mock('tdesign-mobile-react', () => ({
  Navbar: ({ title, leftArrow, onLeftClick }: { title: string; leftArrow?: boolean; onLeftClick?: () => void }) => <header>{leftArrow && <button type="button" aria-label="返回" onClick={onLeftClick}>返回</button>}{title}</header>,
  Button: ({ children, onClick, disabled }: { children: React.ReactNode; onClick?: () => void; disabled?: boolean }) => <button onClick={onClick} disabled={disabled}>{children}</button>,
  Toast: vi.fn(), Dialog: () => null, Popup: () => null,
}));
vi.mock('@/api/knowledge', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/knowledge')>(),
  fetchKnowledgeOverview: vi.fn(), fetchContributors: vi.fn(), fetchKnowledgeQuestions: vi.fn(), fetchKnowledgeQuestion: vi.fn(),
  fetchPendingReviews: vi.fn(), fetchMyReviewHistory: vi.fn(), fetchKnowledgeReview: vi.fn(),
  fetchReviewSimilar: vi.fn(), submitKnowledgeReview: vi.fn(), toggleKnowledgeLike: vi.fn(),
  fetchKnowledgeDiscussions: vi.fn(), createKnowledgeDiscussion: vi.fn(), fetchContributorQuestions: vi.fn(),
}));

const rewardStatsDefaults = { pending_amount: 9900, pending_count: 9, withdrawing_amount: 0, withdrawing_count: 0, withdrawn_amount: 0 };
const person = { user_id: 'alice', name: '贡献甲', avatar_resource_id: null, rank: 1,
  question_count: 4, answer_count: 6, contribution_count: 10, valid_like_count: 8,
  estimated_reward_fen: 10000, earned_reward_fen: 1250, tied: false };
const overview: api.KnowledgeOverview = { month: '2026-11', months: ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12'],
  starts_at: '2026-07-31T16:00:00Z', ends_at: '2026-12-31T16:00:00Z', campaign_status: 'active',
  settlement_status: 'live', top_three: [person], top_questioners: [person], top_answerers: [person], can_review: false, rule_notice: '综合贡献统一计奖' };
const question: api.KnowledgeQuestion = { task_id: 7, title: '充电桩故障处理', problem: '充电无法启动',
  content: '检查供电后恢复', review_comment: '验证完整', value_point: '可复用排查方法',
  created_at: '2026-11-01T01:00:00Z', archived_at: '2026-11-12T01:00:00Z', valid_like_count: 2, liked_by_me: false, can_like: true,
  questioner: person, answerer: { user_id: 'bob', name: '接单乙', avatar_resource_id: null } };
const report: api.KnowledgeReview = { report_id: 9, task_id: 7, version: 2, revision: 3,
  submit_status: 'submitted', title: '匿名充电问题', problem: '无法充电', content: '## 解决方案\n检查供电', review_comment: '', value_point: '' };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchChallengePreview).mockResolvedValue({ items: [], total: 0, can_manage: false });
  vi.mocked(fetchRewardStats).mockResolvedValue(rewardStatsDefaults);
  useAuthStore.setState({ username: 'knowledge-test', name: '当前用户', projectIds: [], permissions: [], isAdmin: false, roles: null, canViewKnowledgeContribution: true });
  vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue(overview);
  vi.mocked(api.fetchContributors).mockResolvedValue({ items: [person] });
  vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValue({ items: [question], total: 1 });
  vi.mocked(api.fetchKnowledgeQuestion).mockResolvedValue(question);
  vi.mocked(api.fetchKnowledgeDiscussions).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.createKnowledgeDiscussion).mockResolvedValue({ id: 1, task_id: 7, author: person, content: '经验补充', created_at: '2026-11-13T01:00:00Z' });
  vi.mocked(api.fetchContributorQuestions).mockResolvedValue({ user: person, month: '2026-11', months: overview.months, question_count: 4, answer_count: 6, earned_reward_fen: 1250, items: [question], total: 1 });
  vi.mocked(api.fetchPendingReviews).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.fetchMyReviewHistory).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.fetchKnowledgeReview).mockResolvedValue(report);
  vi.mocked(api.fetchReviewSimilar).mockResolvedValue({ similar: [], retrieval_status: 'ok' });
  vi.mocked(api.submitKnowledgeReview).mockResolvedValue({});
  vi.mocked(api.toggleKnowledgeLike).mockResolvedValue({ ...question, liked_by_me: true });
});

describe('知识贡献榜UI', () => {
  it('首页同时保留知识贡献榜和我的奖励入口，并显示待提现金额', async () => {
    vi.mocked(fetchRewardStats).mockResolvedValue({ ...rewardStatsDefaults, withdrawing_amount: 1250, withdrawing_count: 2, withdrawn_amount: 3456 });
    render(<MemoryRouter><Routes><Route path="/" element={<Dashboard />} /><Route path="/rewards" element={<p>奖励详情入口</p>} /></Routes></MemoryRouter>);
    expect(await screen.findByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    const rewardsEntry = await screen.findByRole('button', { name: /待提现（元）.*2 笔可提.*¥12\.50/ });
    expect(rewardsEntry.tagName).toBe('SECTION');
    expect(screen.getAllByRole('button', { name: /待提现（元）/ })).toHaveLength(1);
    expect(rewardsEntry).toHaveClass('mac-card', 'mac-card--pad');
    expect(within(rewardsEntry).getByText('¥12.50')).toHaveStyle({ fontSize: '28px', fontWeight: '700', color: '#227197' });
    expect(within(rewardsEntry).queryByText('¥99.00')).not.toBeInTheDocument();
    expect(within(rewardsEntry).getByText('累计已到账')).toBeInTheDocument();
    expect(within(rewardsEntry).getByText('¥34.56')).toHaveStyle({ color: '#2ba471' });
    fireEvent.click(rewardsEntry);
    expect(await screen.findByText('奖励详情入口')).toBeInTheDocument();
  });

  it('奖励发生变化后定时更新全量待提现合计，卸载后停止刷新', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchRewardStats)
        .mockResolvedValueOnce({ ...rewardStatsDefaults, withdrawing_amount: 1250, withdrawing_count: 1 })
        .mockResolvedValue({ ...rewardStatsDefaults, withdrawing_amount: 1250 + 2350 + 99, withdrawing_count: 3, withdrawn_amount: 7000 });
      const view = render(<MemoryRouter><Dashboard /></MemoryRouter>);
      await act(async () => {});
      const card = screen.getByRole('button', { name: /待提现（元）/ });
      expect(within(card).getByText('¥12.50')).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); });
      expect(within(card).getByText('¥36.99')).toBeInTheDocument();
      expect(within(card).getByText('3 笔可提')).toBeInTheDocument();
      expect(within(card).getByText('¥70.00')).toBeInTheDocument();
      view.unmount();
      const calls = vi.mocked(fetchRewardStats).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); });
      fireEvent.focus(window);
      fireEvent(document, new Event('visibilitychange'));
      expect(fetchRewardStats).toHaveBeenCalledTimes(calls);
    } finally {
      vi.useRealTimers();
    }
  });

  it('回到前台时重新汇总，临时失败保留金额并可再次刷新', async () => {
    vi.mocked(fetchRewardStats).mockResolvedValueOnce({ ...rewardStatsDefaults, withdrawing_amount: 3699, withdrawing_count: 3 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    const card = await screen.findByRole('button', { name: /3 笔可提.*¥36\.99/ });
    vi.mocked(fetchRewardStats).mockRejectedValueOnce(new Error('temporary failure'));
    await act(async () => { fireEvent.focus(window); });
    expect(fetchRewardStats).toHaveBeenCalledTimes(2);
    expect(within(card).getByText('¥36.99')).toBeInTheDocument();
    vi.mocked(fetchRewardStats).mockResolvedValue({ ...rewardStatsDefaults, withdrawing_amount: 2449, withdrawing_count: 2, withdrawn_amount: 1250 });
    await act(async () => { fireEvent(document, new Event('visibilitychange')); });
    expect(within(card).getByText('¥24.49')).toBeInTheDocument();
    expect(within(card).getByText('2 笔可提')).toBeInTheDocument();
    expect(within(card).getByText('¥12.50')).toBeInTheDocument();
  });

  it('首次统计失败后自动重试，不一直将待提现合计显示为零', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(fetchRewardStats)
        .mockRejectedValueOnce(new Error('temporary failure'))
        .mockResolvedValue({ ...rewardStatsDefaults, withdrawing_amount: 3699, withdrawing_count: 3 });
      const view = render(<MemoryRouter><Dashboard /></MemoryRouter>);
      await act(async () => {});
      await act(async () => { await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS); });
      expect(screen.getByRole('button', { name: /3 笔可提.*¥36\.99/ })).toBeInTheDocument();
      view.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('账号变化后刷新本人奖励，不被上一账号的迟到响应覆盖', async () => {
    let finishPrevious!: (stats: Awaited<ReturnType<typeof fetchRewardStats>>) => void;
    vi.mocked(fetchRewardStats)
      .mockReturnValueOnce(new Promise((resolve) => { finishPrevious = resolve; }))
      .mockResolvedValue({ ...rewardStatsDefaults, withdrawing_amount: 2350, withdrawing_count: 1 });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await act(async () => { useAuthStore.setState({ username: 'another-user' }); });
    const card = screen.getByRole('button', { name: /待提现（元）/ });
    expect(within(card).getByText('¥23.50')).toBeInTheDocument();
    await act(async () => { finishPrevious({ ...rewardStatsDefaults, withdrawing_amount: 9900, withdrawing_count: 9 }); });
    expect(within(card).getByText('¥23.50')).toBeInTheDocument();
    expect(within(card).queryByText('¥99.00')).not.toBeInTheDocument();
  });

  it('普通用户奖励统计失败时仍可查看榜单并进入奖励入口', async () => {
    useAuthStore.setState({ canViewKnowledgeContribution: false });
    vi.mocked(fetchRewardStats).mockRejectedValue(new Error('奖励暂不可用'));
    render(<MemoryRouter><Routes><Route path="/" element={<Dashboard />} /><Route path="/rewards" element={<p>奖励详情入口</p>} /></Routes></MemoryRouter>);
    await waitFor(() => expect(fetchRewardStats).toHaveBeenCalledOnce());
    expect(screen.getByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '高价值问题榜单' })).toBeInTheDocument();
    expect(api.fetchKnowledgeOverview).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: /待提现（元）/ }));
    expect(await screen.findByText('奖励详情入口')).toBeInTheDocument();
  });

  it('保持奖励卡片UI不变，仅将奖励标题和卡片放在知识榜上方', async () => {
    const rendered = render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText('贡献甲');
    const welcome = rendered.container.querySelector('.admin-welcome');
    const rewardsHeading = screen.getByRole('heading', { name: '我的奖励' });
    const rewardsCard = screen.getByRole('button', { name: /待提现（元）/ });
    const knowledgeHeading = screen.getByRole('heading', { name: '知识贡献榜' });
    const knowledgeCard = screen.getByRole('region', { name: '知识贡献榜' });
    const ticketHeading = screen.getByRole('heading', { name: '工单数据看板' });
    expect(welcome).not.toBeNull();
    expect(welcome!.nextElementSibling).toBe(rewardsHeading.parentElement);
    expect(rewardsHeading.parentElement!.nextElementSibling).toBe(rewardsCard);
    expect(rewardsCard.nextElementSibling).toBe(knowledgeHeading.parentElement);
    expect(knowledgeHeading.compareDocumentPosition(knowledgeCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(knowledgeCard.compareDocumentPosition(ticketHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(knowledgeHeading).toHaveClass('mac-section-title__text');
    expect(knowledgeHeading.parentElement).toHaveClass('mac-section-title');
    expect(knowledgeHeading.className).toBe(ticketHeading.className);
    expect(screen.getAllByText('知识贡献榜')).toHaveLength(1);
  });

  it.each([
    ['知识贡献榜', '详细榜单', '/admin/knowledge', '点击'],
    ['知识贡献榜', '详细榜单', '/admin/knowledge', '回车'],
    ['高价值问题榜单', '所有问题', '/admin/knowledge/challenges', '点击'],
    ['高价值问题榜单', '所有问题', '/admin/knowledge/challenges', '回车'],
  ])('%s标题右侧的%s进入%s，支持%s操作', async (title, label, path, interaction) => {
    const user = userEvent.setup();
    render(<MemoryRouter><Routes><Route path="/" element={<Dashboard />} /><Route path={path} element={<p>二级页面内容</p>} /></Routes></MemoryRouter>);
    await screen.findByText('贡献甲');
    const heading = screen.getByRole('heading', { name: title });
    const entry = within(heading.parentElement!).getByRole('link', { name: label });
    expect(entry).toHaveClass('mac-section-title__more');
    expect(entry.parentElement).toHaveClass('mac-section-title__actions');
    expect(entry).toHaveAttribute('href', path);
    if (interaction === '回车') {
      entry.focus();
      await user.keyboard('{Enter}');
    } else {
      await user.click(entry);
    }
    expect(await screen.findByText('二级页面内容')).toBeInTheDocument();
  });

  it.each(['{Enter}', ' '])('原奖励汇总卡片支持通过 %s 键进入奖励详情', async (key) => {
    const user = userEvent.setup();
    render(<MemoryRouter><Routes><Route path="/" element={<Dashboard />} /><Route path="/rewards" element={<p>奖励详情入口</p>} /></Routes></MemoryRouter>);
    const rewardsEntry = screen.getByRole('button', { name: /待提现（元）/ });
    rewardsEntry.focus();
    await user.keyboard(key);
    expect(await screen.findByText('奖励详情入口')).toBeInTheDocument();
  });

  it.each(['贡献甲', '按月统计 · 以有效知识沉淀贡献排名'])('贡献卡片移除查看全部，点击%s进入知识中心', async (content) => {
    render(<MemoryRouter><Routes><Route path="/" element={<KnowledgeContributionCard />} /><Route path="/admin/knowledge" element={<p>知识中心入口</p>} /></Routes></MemoryRouter>);
    await screen.findByText('贡献甲');
    expect(screen.queryByRole('heading', { name: '知识贡献榜' })).not.toBeInTheDocument();
    expect(screen.queryByText(/查看全部/)).not.toBeInTheDocument();
    const card = screen.getByRole('region', { name: '知识贡献榜' });
    expect(card).toHaveClass('knowledge-clickable-card');
    expect(within(card).getAllByRole('link')).toHaveLength(1);
    expect(within(card).getByRole('link', { name: '查看知识贡献榜' })).toHaveAttribute('href', '/admin/knowledge');
    fireEvent.click(screen.getByText(content));
    expect(await screen.findByText('知识中心入口')).toBeInTheDocument();
  });

  it('贡献卡片保留键盘聚焦和回车导航', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><Routes><Route path="/" element={<KnowledgeContributionCard />} /><Route path="/admin/knowledge" element={<p>知识中心入口</p>} /></Routes></MemoryRouter>);
    await screen.findByText('贡献甲');
    screen.getByRole('link', { name: '查看知识贡献榜' }).focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByText('知识中心入口')).toBeInTheDocument();
  });

  it.each(['首页卡片', '榜单中心'])('%s的路由404提示后端部署问题，并支持重试', async (page) => {
    vi.mocked(api.fetchKnowledgeOverview).mockRejectedValueOnce(new ApiError('Not Found', 404, undefined, { detail: 'Not Found' }));
    render(<MemoryRouter><Routes>
      <Route path="/" element={page === '首页卡片' ? <KnowledgeContributionCard /> : <KnowledgeCenter />} />
      <Route path="/admin/knowledge" element={<p>知识中心入口</p>} />
    </Routes></MemoryRouter>);
    expect(await screen.findByRole('alert')).toHaveTextContent('知识贡献接口尚未启用，请更新并重启业务后端');
    const retry = screen.getByRole('button', { name: '重新加载' });
    expect(retry.closest('a')).toBeNull();
    fireEvent.click(retry);
    await waitFor(() => expect(api.fetchKnowledgeOverview).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect((await screen.findAllByText('贡献甲')).length).toBeGreaterThan(0);
    expect(screen.queryByText('知识中心入口')).not.toBeInTheDocument();
  });

  it('问题资源404保留业务原因，不误报接口未启用', async () => {
    const detail = '问题尚未公开或不属于本期活动';
    vi.mocked(api.fetchKnowledgeQuestion).mockRejectedValueOnce(new ApiError(detail, 404, undefined, { detail }));
    render(<MemoryRouter initialEntries={['/questions/7']}><Routes><Route path="/questions/:taskId" element={<KnowledgeQuestionDetail />} /></Routes></MemoryRouter>);
    expect(await screen.findByRole('alert')).toHaveTextContent(detail);
    expect(screen.queryByText(/知识贡献接口尚未启用/)).not.toBeInTheDocument();
  });

  it('使用现有卡片样式，综合前三展示提问解答数量而非奖金，空缺不伪造用户', async () => {
    const rendered = render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    expect(await screen.findByText('贡献甲')).toBeInTheDocument();
    expect(screen.getByText('提问4 · 解答6')).toBeInTheDocument();
    expect(screen.getByText('10次贡献')).toBeInTheDocument();
    expect(screen.queryByText(/¥/)).not.toBeInTheDocument();
    expect(screen.getAllByText('虚位以待')).toHaveLength(2);
    expect(screen.queryByText('预计月度奖励，核验后发放')).not.toBeInTheDocument();
    expect(rendered.container.querySelector('.mac-card--pad')).not.toBeNull();
  });

  it('加载失败显示重试，不当作零数据', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockRejectedValueOnce(new Error('接口暂不可用'));
    render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    expect(await screen.findByRole('alert')).toHaveTextContent('接口暂不可用');
    fireEvent.click(screen.getByText('重新加载'));
    expect(await screen.findByText('贡献甲')).toBeInTheDocument();
  });

  it('切换账号后不闪现或回填上一账号数据', async () => {
    render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    await screen.findByText('贡献甲');
    let resolveNext: (value: api.KnowledgeOverview) => void = () => {};
    vi.mocked(api.fetchKnowledgeOverview).mockReturnValueOnce(new Promise((resolve) => { resolveNext = resolve; }));
    await act(async () => { useAuthStore.setState({ username: 'other-account' }); });
    expect(screen.queryByText('贡献甲')).not.toBeInTheDocument();
    await act(async () => resolveNext({ ...overview, top_three: [] }));
    expect(screen.getAllByText('虚位以待')).toHaveLength(3);
  });

  it.each([
    '/admin/knowledge',
    '/admin/knowledge?tab=questions',
    '/admin/knowledge?tab=questioners',
    '/admin/knowledge?tab=answerers',
  ])('管理员访问%s也不显示异常观察和月度结算', async (entry) => {
    useAuthStore.setState({ permissions: ['admin', 'backend:knowledge_incentive:manage'] });
    const administratorOverview = { ...overview, can_manage: true };
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue(administratorOverview);
    render(<MemoryRouter initialEntries={[entry]}><KnowledgeCenter /></MemoryRouter>);
    if (entry.includes('tab=')) await screen.findAllByRole('tab');
    else await screen.findByRole('link', { name: '查看知识榜单' });
    expect(screen.queryByRole('button', { name: /异常观察|月度结算|冻结月度快照|发放月度奖金/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '异常观察与月度结算' })).not.toBeInTheDocument();
    if (entry.includes('tab=')) {
      expect(screen.getAllByRole('tab')).toHaveLength(3);
    } else {
      expect(screen.getByRole('link', { name: '查看知识榜单' })).toBeInTheDocument();
    }
  });

  it('解答榜请求独立解答排名，不另造接单奖金', async () => {
    render(<MemoryRouter initialEntries={['/admin/knowledge']}><Routes><Route path="/admin/knowledge" element={<KnowledgeCenter />} /></Routes></MemoryRouter>);
    fireEvent.click(await screen.findByRole('link', { name: '查看最佳解答人榜单' }));
    expect(await screen.findAllByRole('tab')).toHaveLength(3);
    fireEvent.click(screen.getByRole('tab', { name: '最佳解答人榜单' }));
    expect(await screen.findByText('解答6')).toBeInTheDocument();
    expect(screen.getByText('已获 ¥12.5')).toBeInTheDocument();
    expect(screen.queryByText('¥100')).not.toBeInTheDocument();
    expect(api.fetchContributors).toHaveBeenCalledWith('2026-11', 'answerer');
    expect(screen.queryByText(/综合|合并排名/)).not.toBeInTheDocument();
    expect(screen.queryByText('展开异常观察与月度结算')).not.toBeInTheDocument();
  });

  it('问题支持时间/获赞排序及点赞', async () => {
    render(<MemoryRouter initialEntries={['/?tab=questions']}><KnowledgeCenter /></MemoryRouter>);
    await screen.findByText('充电桩故障处理');
    expect(api.fetchKnowledgeQuestions).toHaveBeenLastCalledWith('2026-11', 'created_at', 1);
    expect(screen.getByLabelText('问题排序')).toHaveValue('created_at');
    fireEvent.change(screen.getByLabelText('问题排序'), { target: { value: 'likes' } });
    await waitFor(() => expect(api.fetchKnowledgeQuestions).toHaveBeenLastCalledWith('2026-11', 'likes', 1));
    await screen.findByText('充电桩故障处理');
    fireEvent.click(screen.getByRole('button', { name: '点赞：充电桩故障处理' }));
    await waitFor(() => expect(api.toggleKnowledgeLike).toHaveBeenCalledWith(question));
  });

  it('人员榜仅展示前十，不把第十一名混入已结算奖金', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, settlement_status: 'granted' });
    vi.mocked(api.fetchContributors).mockResolvedValue({ items: [person,
      { ...person, user_id: 'outside', name: '未入奖用户', rank: 11, estimated_reward_fen: 0 }] });
    render(<MemoryRouter initialEntries={['/?tab=answerers']}><KnowledgeCenter /></MemoryRouter>);
    await screen.findByText('贡献甲');
    expect(screen.queryByText('未入奖用户')).not.toBeInTheDocument();
    expect(screen.getByText('已获 ¥12.5')).toBeInTheDocument();
    expect(screen.queryByText('已入奖励账户')).not.toBeInTheDocument();
  });

  it('暂无贡献时不宣传具体开始日期，不伪造榜单数据', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, campaign_status: 'upcoming', top_three: [] });
    render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    expect(await screen.findByText('按月统计 · 以有效知识沉淀贡献排名')).toBeInTheDocument();
    expect(screen.queryByText(/11月1日|活动未开始|活动将于/)).not.toBeInTheDocument();
    expect(screen.queryByText('¥100')).not.toBeInTheDocument();
  });
});

describe('知识贡献榜首页可见性', () => {
  it.each(['超级管理员', '现场工程师', '项目经理'])('%s获得已启用卡片权限后保留完整首页卡片', async () => {
    useAuthStore.setState({ canViewKnowledgeContribution: true, permissions: [], isAdmin: false });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(await screen.findByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看知识贡献榜' })).toHaveAttribute('href', '/admin/knowledge');
    expect(screen.getByRole('link', { name: '查看高价值问题榜单' })).toHaveAttribute('href', '/admin/knowledge/challenges');
    expect(screen.getByRole('link', { name: '详细榜单' })).toHaveAttribute('href', '/admin/knowledge');
    expect(screen.getByRole('link', { name: '所有问题' })).toHaveAttribute('href', '/admin/knowledge/challenges');
    expect(screen.queryByText('查看榜单')).not.toBeInTheDocument();
    expect(screen.queryByText(/查看全部/)).not.toBeInTheDocument();
    const contributionHeading = screen.getByRole('heading', { name: '知识贡献榜' });
    const challengeHeading = screen.getByRole('heading', { name: '高价值问题榜单' });
    expect(contributionHeading.compareDocumentPosition(challengeHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(await screen.findByText('贡献甲')).toBeInTheDocument();
  });

  it.each([{ permissions: [] }, { permissions: ['admin'] }, { permissions: ['*'] }, { permissions: ['frontend:admin:*:show'] }])('无需单独授权，权限$permissions的用户也可查看两个榜单', async ({ permissions }) => {
    useAuthStore.setState({ canViewKnowledgeContribution: false, permissions, isAdmin: false, canManageKnowledgeChallenges: false });
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    expect(screen.getByRole('heading', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '高价值问题榜单' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '高价值问题榜单' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '详细榜单' })).toHaveAttribute('href', '/admin/knowledge');
    expect(screen.getByRole('link', { name: '所有问题' })).toHaveAttribute('href', '/admin/knowledge/challenges');
    expect(screen.queryByText('查看榜单')).not.toBeInTheDocument();
    expect(screen.queryByText(/每人每天最多点赞5个不同问题/)).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '工单数据看板' })).toBeInTheDocument();
    await screen.findByText('贡献甲');
    expect(api.fetchKnowledgeOverview).toHaveBeenCalledOnce();
    expect(fetchChallengePreview).toHaveBeenCalledOnce();
    expect(useAuthStore.getState().canManageKnowledgeChallenges).toBe(false);
  });

  it('单独挂载卡片时普通用户同样可查看榜单', async () => {
    useAuthStore.setState({ canViewKnowledgeContribution: false });
    render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    expect(await screen.findByText('贡献甲')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(api.fetchKnowledgeOverview).toHaveBeenCalledOnce();
  });

  it('角色刷新或历史可见标记变化不再隐藏公开榜单', async () => {
    render(<MemoryRouter><Dashboard /></MemoryRouter>);
    await screen.findByText('贡献甲');
    act(() => useAuthStore.setState({ canViewKnowledgeContribution: false }));
    expect(screen.getByRole('region', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '知识贡献榜' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '高价值问题榜单' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '详细榜单' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '所有问题' })).toBeInTheDocument();
    expect(screen.queryByText('查看榜单')).not.toBeInTheDocument();
  });
});

describe('每日点赞限制提示', () => {
  it('首页卡片保留前三名列表但不常驻展示点赞限制', async () => {
    render(<MemoryRouter><KnowledgeContributionCard /></MemoryRouter>);
    expect(await screen.findByRole('list', { name: '知识贡献榜前三名' })).toBeInTheDocument();
    expect(screen.queryByText(/每人每天最多点赞5个不同问题/)).not.toBeInTheDocument();
    expect(screen.queryByText(/北京时间每日0点重置|取消点赞不返还当日名额/)).not.toBeInTheDocument();
    expect(Toast).not.toHaveBeenCalled();
  });

  it.each(['active', 'upcoming', 'ended'] as const)('%s状态的榜单不常驻展示点赞规则', async (status) => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, campaign_status: status });
    const rendered = render(<MemoryRouter><KnowledgeCenter /></MemoryRouter>);
    expect(await screen.findByRole('link', { name: '查看知识榜单' })).toBeInTheDocument();
    expect(rendered.container).not.toHaveTextContent(/每人每天最多点赞5个不同问题|北京时间每日0点重置|取消点赞不返还当日名额/);
    expect(Toast).not.toHaveBeenCalled();
  });

  it.each(['/admin/knowledge?tab=questions', '/admin/knowledge/questions/7'])('%s额度内正常点赞不弹出限制提示', async (path) => {
    render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/admin/knowledge" element={<KnowledgeCenter />} /><Route path="/admin/knowledge/questions/:taskId" element={<KnowledgeQuestionDetail />} /></Routes></MemoryRouter>);
    const like = await screen.findByRole('button', { name: `点赞：${question.title}` });
    expect(Toast).not.toHaveBeenCalled();
    fireEvent.click(like);
    await waitFor(() => expect(api.toggleKnowledgeLike).toHaveBeenCalledWith(question));
    await waitFor(() => expect(like).not.toBeDisabled());
    expect(Toast).not.toHaveBeenCalled();
  });

  it.each(['/admin/knowledge?tab=questions', '/admin/knowledge/questions/7'])('%s超额点赞展示后端提示且不改变点赞数', async (path) => {
    const message = '今日已点赞5个不同问题，不能再点赞新问题（北京时间每日0点重置）';
    vi.mocked(api.toggleKnowledgeLike).mockRejectedValueOnce(new ApiError(message, 429));
    render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/admin/knowledge" element={<KnowledgeCenter />} /><Route path="/admin/knowledge/questions/:taskId" element={<KnowledgeQuestionDetail />} /></Routes></MemoryRouter>);
    const like = await screen.findByRole('button', { name: `点赞：${question.title}` });
    expect(Toast).not.toHaveBeenCalled();
    fireEvent.click(like);
    await waitFor(() => expect(Toast).toHaveBeenCalledWith({ message, theme: 'error' }));
    expect(Toast).toHaveBeenCalledTimes(1);
    expect(like).toHaveAttribute('aria-pressed', 'false');
    expect(like).toHaveTextContent('2');
    expect(api.fetchKnowledgeQuestion).toHaveBeenCalledTimes(path.includes('/questions/') ? 1 : 0);
    expect(api.fetchKnowledgeQuestions).toHaveBeenCalledTimes(path.includes('/questions/') ? 0 : 1);
  });
});

describe('奖金激励说明由专门入口承载', () => {
  it.each([
    '/admin/knowledge',
    '/admin/knowledge?tab=questions',
    '/admin/knowledge?tab=questioners',
    '/admin/knowledge?tab=answerers',
  ])('%s不展示奖励规则或叠加领奖说明', async (entry) => {
    const internalNotice = '内部发奖说明：前三各100元，第4—10名各50元；核验后发放，可叠加领奖。';
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, rule_notice: internalNotice });
    const rendered = render(<MemoryRouter initialEntries={[entry]}><KnowledgeCenter /></MemoryRouter>);
    if (entry.includes('tab=questions')) await screen.findByText(question.title);
    else if (entry.includes('tab=')) await screen.findByRole('button', { name: '查看贡献甲的工单' });
    else await screen.findByRole('link', { name: '查看知识榜单' });
    expect(screen.queryByText(internalNotice)).not.toBeInTheDocument();
    expect(rendered.container).not.toHaveTextContent(/前三各100元|第4—10名各50元|奖励金额待公布|奖励待公布|奖金叠加|奖励叠加|叠加领奖|不另设.*奖金|预计月度奖励|核验后发放|已入奖励账户/);
    expect(screen.queryByText('¥100')).not.toBeInTheDocument();
    if (entry.includes('tab=questioners') || entry.includes('tab=answerers')) {
      expect(screen.getByText('已获 ¥12.5')).toBeInTheDocument();
    } else expect(screen.queryByText(/¥/)).not.toBeInTheDocument();
  });

  it.each(['live', 'reviewing', 'held', 'approved', 'granting', 'granted', 'stale'])('状态为%s时首页及总览不显示发奖/结算提示', async (status) => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, settlement_status: status });
    const rendered = render(<MemoryRouter><KnowledgeContributionCard /><KnowledgeCenter /></MemoryRouter>);
    await screen.findByRole('link', { name: '查看最佳提问人榜单' });
    expect(rendered.container).not.toHaveTextContent(/预计月度奖励|月度奖励已发放|已入奖励账户|核验|发放|结算|计奖|规则待定|奖励待公布/);
    expect(screen.queryByText(/¥/)).not.toBeInTheDocument();
  });
});

describe('横向榜单预览', () => {
  const contributors = [person,
    { ...person, user_id: 'bob', name: '贡献乙', rank: 2, question_count: 9, answer_count: 0, contribution_count: 9 },
    { ...person, user_id: 'carol', name: '贡献丙', rank: 3, question_count: 0, answer_count: 8, contribution_count: 8 },
    { ...person, user_id: 'david', name: '完整榜第四名', rank: 4, estimated_reward_fen: 5000 }];
  const questioners = [{ ...contributors[1], rank: 1 }, { ...person, rank: 2 },
    { ...contributors[3], name: '提问榜第三名', rank: 3 },
    { ...contributors[3], user_id: 'questioner-fourth', rank: 4 }];
  const answerers = [{ ...contributors[2], rank: 1 }, { ...person, rank: 2 },
    { ...contributors[3], name: '解答榜第三名', rank: 3 },
    { ...contributors[3], user_id: 'answerer-fourth', rank: 4 }];
  const questions = [question,
    { ...question, task_id: 8, title: '定位漂移排查', valid_like_count: 1 },
    { ...question, task_id: 9, title: '导航绕行处理', valid_like_count: 1 },
    { ...question, task_id: 10, title: '完整列表第四题', valid_like_count: 0 }];

  function openBoards(month = '2026-11') {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, top_three: contributors.slice(0, 3), top_questioners: questioners.slice(0, 3), top_answerers: answerers.slice(0, 3) });
    vi.mocked(api.fetchContributors).mockImplementation(async (_month, role) => ({ items: role === 'questioner' ? questioners : role === 'answerer' ? answerers : contributors }));
    vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValue({ items: questions, total: 4 });
    return render(<MemoryRouter initialEntries={[`/admin/knowledge?month=${month}`]}><Routes><Route path="/admin/knowledge" element={<KnowledgeCenter />} /><Route path="/admin/knowledge/questions/:taskId" element={<KnowledgeQuestionDetail />} /></Routes></MemoryRouter>);
  }

  it('三个横向模块各展示对应前三，提问解答榜使用独立名次且不显示综合', async () => {
    const rendered = openBoards();
    await screen.findByText('充电桩故障处理');
    expect(screen.getByRole('heading', { name: '让每一次问题的解决，都沉淀为知识。' })).toBeInTheDocument();
    expect(screen.queryByText('问题榜单')).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    expect(rendered.container.querySelectorAll('.knowledge-board-card__body')).toHaveLength(3);
    for (const title of ['知识榜单', '最佳提问人榜单', '最佳解答人榜单']) {
      const board = screen.getByRole('region', { name: title });
      expect(within(board).getAllByRole('listitem')).toHaveLength(3);
      expect(within(board).getByRole('link', { name: `查看${title}` })).toHaveAttribute('href', expect.stringContaining('month=2026-11'));
    }
    for (const title of ['最佳提问人榜单', '最佳解答人榜单']) {
      const board = screen.getByRole('region', { name: title });
      const rows = within(board).getAllByRole('listitem');
      expect(rows[0]).toHaveTextContent(title === '最佳提问人榜单' ? '贡献乙' : '贡献丙');
      expect(rows[1]).toHaveTextContent('贡献甲');
      expect(rows[2]).toHaveTextContent(title === '最佳提问人榜单' ? '提问榜第三名' : '解答榜第三名');
      expect(within(board).queryByText(/¥/)).not.toBeInTheDocument();
      expect(rows[0]).toHaveTextContent(title === '最佳提问人榜单' ? '9次提问' : '8次解答');
      expect(rows[1]).toHaveTextContent(title === '最佳提问人榜单' ? '4次提问' : '6次解答');
      expect(rows[2]).toHaveTextContent(title === '最佳提问人榜单' ? '4次提问' : '6次解答');
      expect(board).not.toHaveTextContent(/综合|合并排名/);
      expect(board).not.toHaveTextContent(title === '最佳提问人榜单' ? /次解答/ : /次提问/);
      expect(within(board).queryByText('预计月度奖励，核验后发放')).not.toBeInTheDocument();
    }
    expect(screen.queryByText('完整榜第四名')).not.toBeInTheDocument();
    expect(screen.queryByText('完整列表第四题')).not.toBeInTheDocument();
    expect(api.fetchKnowledgeQuestions).toHaveBeenCalledWith('2026-11', 'likes', 1, 3);
  });

  it.each(['知识榜单', '最佳提问人榜单', '最佳解答人榜单'])('%s可进入完整列表并返回概览，月份保持不变', async (title) => {
    openBoards('2026-12');
    fireEvent.click(await screen.findByRole('link', { name: `查看${title}` }));
    expect(await screen.findByText(title === '知识榜单' ? '完整列表第四题' : '完整榜第四名')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: title })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('榜单月份')).toHaveValue('2026-12');
    if (title === '知识榜单') {
      expect(screen.getByLabelText('问题排序')).toHaveValue('likes');
      expect(api.fetchKnowledgeQuestions).toHaveBeenLastCalledWith('2026-12', 'likes', 1);
    } else {
      expect(api.fetchContributors).toHaveBeenCalledWith('2026-12', title === '最佳提问人榜单' ? 'questioner' : 'answerer');
    }
    fireEvent.click(screen.getByRole('button', { name: '← 返回榜单概览' }));
    expect(await screen.findByRole('link', { name: '查看知识榜单' })).toBeInTheDocument();
    expect(screen.getByLabelText('榜单月份')).toHaveValue('2026-12');
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('同月切换提问和解答榜重新请求，不复用另一角色的名单及名次', async () => {
    openBoards();
    fireEvent.click(await screen.findByRole('link', { name: '查看最佳提问人榜单' }));
    const questionerGrid = await screen.findByRole('list', { name: '最佳提问人前十名' });
    expect(await within(questionerGrid).findByRole('button', { name: '查看贡献乙的工单' })).toHaveTextContent('提问9');
    expect(questionerGrid).not.toHaveTextContent(/综合|解答/);
    fireEvent.click(screen.getByRole('tab', { name: '最佳解答人榜单' }));
    const answererGrid = await screen.findByRole('list', { name: '最佳解答人前十名' });
    expect(await within(answererGrid).findByRole('button', { name: '查看贡献丙的工单' })).toHaveTextContent('解答8');
    expect(within(answererGrid).queryByRole('button', { name: '查看贡献乙的工单' })).not.toBeInTheDocument();
    expect(answererGrid).not.toHaveTextContent(/综合|提问/);
    expect(api.fetchContributors).toHaveBeenCalledWith('2026-11', 'questioner');
    expect(api.fetchContributors).toHaveBeenLastCalledWith('2026-11', 'answerer');
  });

  it('问题预览失败不影响人员榜，显示真实错误并可单独重试', async () => {
    openBoards();
    vi.mocked(api.fetchKnowledgeQuestions).mockRejectedValueOnce(new Error('问题预览暂不可用'));
    expect(await screen.findByRole('alert')).toHaveTextContent('问题预览暂不可用');
    expect(within(screen.getByRole('region', { name: '最佳提问人榜单' })).getByText('贡献甲')).toBeInTheDocument();
    expect(screen.queryByText('暂无上榜问题')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('充电桩故障处理')).toBeInTheDocument();
  });

  it('暂无活动数据时仍保留三个模块及空缺提示，不伪造前三和奖金', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, campaign_status: 'upcoming', top_three: [], top_questioners: [], top_answerers: [] });
    vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValue({ items: [], total: 0 });
    render(<MemoryRouter><KnowledgeCenter /></MemoryRouter>);
    expect(await screen.findAllByText('暂无上榜问题')).toHaveLength(3);
    expect(screen.getAllByText('虚位以待')).toHaveLength(6);
    expect(screen.queryByText('¥100')).not.toBeInTheDocument();
    expect(screen.getByText(/按月统计 · 以有效知识沉淀贡献排名/)).toBeInTheDocument();
    expect(screen.queryByText(/11月1日|活动未开始|活动将于/)).not.toBeInTheDocument();
  });

  it.each(['2026-08', '2026-09', '2026-10', '2026-12'])('切换到%s刷新三个预览，不残留上一月的用户和问题', async (month) => {
    openBoards();
    await screen.findByText('充电桩故障处理');
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValueOnce({ ...overview, month, top_three: [{ ...person, name: '当月贡献者' }], top_questioners: [{ ...person, name: '当月贡献者' }], top_answerers: [{ ...person, name: '当月贡献者' }] });
    vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValueOnce({ items: [{ ...question, title: '当月归档问题' }], total: 1 });
    fireEvent.change(screen.getByLabelText('榜单月份'), { target: { value: month } });
    expect(await screen.findByText('当月归档问题')).toBeInTheDocument();
    expect(screen.getAllByText('当月贡献者')).toHaveLength(2);
    expect(screen.queryByText('贡献甲')).not.toBeInTheDocument();
    expect(screen.queryByText('充电桩故障处理')).not.toBeInTheDocument();
    expect(api.fetchKnowledgeOverview).toHaveBeenLastCalledWith(month);
    expect(api.fetchKnowledgeQuestions).toHaveBeenLastCalledWith(month, 'likes', 1, 3);
  });

  it('从问题卡进入完整列表后，仍可查看解答报告与匿名审核意见', async () => {
    openBoards('2026-12');
    vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValue({ items: [question], total: 1 });
    vi.mocked(api.fetchKnowledgeQuestion).mockResolvedValueOnce(question);
    fireEvent.click(await screen.findByRole('link', { name: '查看知识榜单' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看问题、解答与审核意见 →' }));
    expect(await screen.findByText('检查供电后恢复')).toBeInTheDocument();
    expect(screen.getByText('验证完整')).toBeInTheDocument();
    expect(screen.getByText('审核人身份不公开')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(await screen.findByLabelText('问题排序')).toHaveValue('likes');
    expect(screen.getByLabelText('榜单月份')).toHaveValue('2026-12');
  });

  it('完整问题榜保留待我审核入口，兼容审核完成后的原有返回链接', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, can_review: true });
    vi.mocked(api.fetchPendingReviews).mockResolvedValueOnce({ items: [report], total: 1 });
    render(<MemoryRouter initialEntries={['/?view=review']}><KnowledgeCenter /></MemoryRouter>);
    expect(await screen.findByText('匿名充电问题')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '待我审核' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '查看报告与相似工单 →' })).toBeInTheDocument();
  });
});

describe('知识列表、人员工单和讨论区', () => {
  function openKnowledge(path: string) {
    return render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/admin/knowledge" element={<KnowledgeCenter />} /><Route path="/admin/knowledge/contributors/:userId" element={<KnowledgeContributorPage />} /><Route path="/admin/knowledge/questions/:taskId" element={<KnowledgeQuestionDetail />} /></Routes></MemoryRouter>);
  }

  it.each(['questioners', 'answerers'])('%s完整榜仅展示本榜数量与实际已获金额，不展示综合或另一角色数量', async (focus) => {
    openKnowledge(`/admin/knowledge?tab=${focus}&month=2026-11`);
    const tile = await screen.findByRole('button', { name: '查看贡献甲的工单' });
    expect(within(tile).getByLabelText(person.name)).toHaveClass('knowledge-avatar');
    expect(tile).toHaveTextContent(focus === 'questioners' ? '提问4' : '解答6');
    expect(tile).not.toHaveTextContent(focus === 'questioners' ? /解答|综合/ : /提问|综合/);
    expect(screen.queryByText(/综合|合并排名/)).not.toBeInTheDocument();
    expect(tile).toHaveTextContent('已获 ¥12.5');
    expect(tile).not.toHaveTextContent('¥100');
    fireEvent.click(tile);
    expect(await screen.findByText('已获 ¥12.5')).toBeInTheDocument();
    expect(screen.getByText('提问4 · 解答6')).toBeInTheDocument();
  });

  it('尚未入账时显示已获零元，不用预计奖金回填', async () => {
    vi.mocked(api.fetchContributors).mockResolvedValue({ items: [{ ...person, earned_reward_fen: 0 }] });
    openKnowledge('/admin/knowledge?tab=questioners&month=2026-11');
    expect(await screen.findByText('已获 ¥0')).toBeInTheDocument();
    expect(screen.queryByText('¥100')).not.toBeInTheDocument();
  });

  it.each(['卡片空白', '问题摘要', '价值点', '人员信息', '问题标题', '查看详情按钮'])('点击优质问题%s直接进入归档详情，返回保留月份和排序', async (area) => {
    openKnowledge('/admin/knowledge?tab=questions&month=2026-11&sort=likes');
    const card = await screen.findByRole('region', { name: question.title });
    const targets = {
      卡片空白: card,
      问题摘要: within(card).getByText(question.problem),
      价值点: within(card).getByText(`价值点：${question.value_point}`),
      人员信息: within(card).getByText(/提单：贡献甲/),
      问题标题: within(card).getByRole('button', { name: question.title }),
      查看详情按钮: within(card).getByRole('button', { name: '查看问题、解答与审核意见 →' }),
    };
    fireEvent.click(targets[area as keyof typeof targets]);
    expect(await screen.findByText(question.content)).toBeInTheDocument();
    expect(api.fetchKnowledgeQuestion).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(await screen.findByLabelText('榜单月份')).toHaveValue('2026-11');
    expect(screen.getByLabelText('问题排序')).toHaveValue('likes');
  });

  it('卡片点赞不进入详情，键盘仍可打开归档详情', async () => {
    const user = userEvent.setup();
    openKnowledge('/admin/knowledge?tab=questions&month=2026-11');
    const card = await screen.findByRole('region', { name: question.title });
    await user.click(within(card).getByRole('button', { name: `点赞：${question.title}` }));
    expect(api.toggleKnowledgeLike).toHaveBeenCalledWith(question);
    expect(api.fetchKnowledgeQuestion).not.toHaveBeenCalled();
    const heading = await screen.findByRole('button', { name: question.title });
    heading.focus();
    await user.keyboard('{Enter}');
    expect(await screen.findByText(question.content)).toBeInTheDocument();
  });

  it('问题榜的二级页面为知识列表，点赞按钮位于问题卡片右侧标题栏', async () => {
    openKnowledge('/admin/knowledge?tab=questions&month=2026-11');
    expect(await screen.findByRole('heading', { name: '知识列表' })).toBeInTheDocument();
    const card = await screen.findByRole('region', { name: question.title });
    const like = within(card).getByRole('button', { name: `点赞：${question.title}` });
    expect(like).toHaveTextContent(`有帮助 · ${question.valid_like_count}`);
    expect(like.closest('.knowledge-question-head')).not.toBeNull();
    fireEvent.click(like);
    await waitFor(() => expect(api.toggleKnowledgeLike).toHaveBeenCalledWith(question));
  });

  it('前十按三列头像网格展示，点击人员可查看并切换提单接单列表', async () => {
    const people = Array.from({ length: 11 }, (_, position) => ({ ...person, user_id: `person-${position + 1}`, name: `人员${position + 1}`, rank: position + 1 }));
    vi.mocked(api.fetchContributors).mockResolvedValue({ items: people });
    vi.mocked(api.fetchContributorQuestions).mockResolvedValue({ user: people[0], month: '2026-11', months: overview.months, question_count: 4, answer_count: 6, earned_reward_fen: 1250, items: [question], total: 1 });
    openKnowledge('/admin/knowledge?tab=questioners&month=2026-11');
    const grid = await screen.findByRole('list', { name: '最佳提问人前十名' });
    await waitFor(() => expect(within(grid).getAllByRole('button')).toHaveLength(10));
    expect(grid).toHaveClass('knowledge-people-grid');
    expect(screen.queryByText('人员11')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看人员1的工单' }));
    expect(await screen.findByText(question.title)).toBeInTheDocument();
    expect(api.fetchContributorQuestions).toHaveBeenLastCalledWith('person-1', '2026-11', 'questioner', 1);
    expect(screen.getByRole('button', { name: 'TA的提单' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'TA的接单' }));
    await waitFor(() => expect(api.fetchContributorQuestions).toHaveBeenLastCalledWith('person-1', '2026-11', 'answerer', 1));
    fireEvent.click(await screen.findByRole('button', { name: '查看问题、解答与审核意见 →' }));
    expect(await screen.findByText('检查供电后恢复')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(await screen.findByRole('button', { name: 'TA的接单' })).toHaveAttribute('aria-pressed', 'true');
  });

  it.each(['2026-08', '2026-09'])('人员贡献可切换到%s，保留角色及返回月份', async (month) => {
    openKnowledge('/admin/knowledge/contributors/alice?month=2026-11&role=questioner');
    await screen.findByText(question.title);
    vi.mocked(api.fetchContributorQuestions).mockResolvedValueOnce({ user: person, month, months: overview.months, question_count: 1, answer_count: 0, earned_reward_fen: 0, items: [question], total: 1 });
    fireEvent.change(screen.getByLabelText('贡献月份'), { target: { value: month } });
    await waitFor(() => expect(api.fetchContributorQuestions).toHaveBeenLastCalledWith('alice', month, 'questioner', 1));
    expect(await screen.findByText(question.title)).toBeInTheDocument();
    expect(screen.getByLabelText('贡献月份')).toHaveValue(month);
    fireEvent.click(screen.getByRole('button', { name: '返回' }));
    expect(await screen.findByLabelText('榜单月份')).toHaveValue(month);
    await waitFor(() => expect(api.fetchContributors).toHaveBeenLastCalledWith(month, 'questioner'));
  });

  it('跨月归档的问题列表及详情显示提问时间，统计口径有明确提示', async () => {
    const historicalQuestion = { ...question, created_at: '2026-08-31T16:00:00Z', archived_at: '2026-10-19T01:00:00Z' };
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, month: '2026-09' });
    vi.mocked(api.fetchKnowledgeQuestions).mockResolvedValue({ items: [historicalQuestion], total: 1 });
    vi.mocked(api.fetchKnowledgeQuestion).mockResolvedValue(historicalQuestion);
    openKnowledge('/admin/knowledge?tab=questions&month=2026-09');
    const card = await screen.findByRole('region', { name: question.title });
    expect(within(card).getByText(/提问于 2026年9月1日/)).toBeInTheDocument();
    expect(screen.getByText(/按提问时间归属月份/)).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '最新提问' })).toHaveValue('created_at');
    fireEvent.click(within(card).getByRole('button', { name: '查看问题、解答与审核意见 →' }));
    expect(await screen.findByText(/提问于 2026年9月1日/)).toBeInTheDocument();
    expect(screen.queryByText(/归档于/)).not.toBeInTheDocument();
  });

  it('详情展示提单接单人、归档报告和匿名意见，并支持点赞', async () => {
    openKnowledge('/admin/knowledge/questions/7');
    await screen.findByText('检查供电后恢复');
    expect(screen.getByText('沉淀知识')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '问题解答 · 已通过知识沉淀报告' })).toBeInTheDocument();
    expect(screen.getByText('提单人')).toBeInTheDocument();
    expect(screen.getByText('接单人')).toBeInTheDocument();
    expect(screen.getByText('贡献甲')).toBeInTheDocument();
    expect(screen.getByText('接单乙')).toBeInTheDocument();
    expect(screen.getByText('审核人身份不公开')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: `点赞：${question.title}` })).toHaveTextContent(`有帮助 · ${question.valid_like_count}`);
    vi.mocked(api.fetchKnowledgeQuestion).mockResolvedValueOnce({ ...question, liked_by_me: true, valid_like_count: 3 });
    fireEvent.click(screen.getByRole('button', { name: `点赞：${question.title}` }));
    expect(await screen.findByRole('button', { name: `取消点赞：${question.title}` })).toHaveTextContent('有帮助 · 3');
  });

  it('讨论区校验空白，发布成功刷新真实讨论且匿名作者不泄露身份', async () => {
    openKnowledge('/admin/knowledge/questions/7');
    await screen.findByRole('region', { name: '讨论区' });
    expect(screen.getByRole('button', { name: '发布讨论' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('参与讨论'), { target: { value: '  经验补充  ' } });
    vi.mocked(api.fetchKnowledgeDiscussions).mockResolvedValueOnce({ items: [{ id: 1, task_id: 7, author: null, content: '经验补充', created_at: '2026-11-13T01:00:00Z' }], total: 1 });
    fireEvent.click(screen.getByRole('button', { name: '发布讨论' }));
    await waitFor(() => expect(api.createKnowledgeDiscussion).toHaveBeenCalledWith('7', '经验补充'));
    expect(await screen.findByText('经验补充')).toBeInTheDocument();
    expect(screen.getByText('匿名用户')).toBeInTheDocument();
    expect(screen.getByLabelText('参与讨论')).toHaveValue('');
  });

  it('讨论发布失败保留输入，列表加载失败不假装没有讨论', async () => {
    vi.mocked(api.fetchKnowledgeDiscussions).mockRejectedValueOnce(new Error('讨论加载失败'));
    vi.mocked(api.createKnowledgeDiscussion).mockRejectedValueOnce(new Error('讨论发布失败'));
    openKnowledge('/admin/knowledge/questions/7');
    expect(await screen.findByRole('alert')).toHaveTextContent('讨论加载失败');
    expect(screen.queryByText('还没有讨论，欢迎分享你的经验。')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    await screen.findByText('还没有讨论，欢迎分享你的经验。');
    fireEvent.change(screen.getByLabelText('参与讨论'), { target: { value: '暂存经验' } });
    fireEvent.click(screen.getByRole('button', { name: '发布讨论' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('讨论发布失败');
    expect(screen.getByLabelText('参与讨论')).toHaveValue('暂存经验');
  });

  it('指定审核人没有待审报告时也能从总览进入评审工作台', async () => {
    vi.mocked(api.fetchKnowledgeOverview).mockResolvedValue({ ...overview, can_review: true, reviewer_configured: true, pending_review_count: 0 });
    openKnowledge('/admin/knowledge');
    fireEvent.click(await screen.findByRole('button', { name: '进入评审工作台' }));
    expect(await screen.findByText(/暂无分配给你的待审报告/)).toBeInTheDocument();
    expect(api.fetchPendingReviews).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '我的审核意见' }));
    expect(await screen.findByText('暂无本人审核意见。')).toBeInTheDocument();
  });

  it('普通用户的评审旧链接显示权限原因，不获取他人待审报告', async () => {
    openKnowledge('/admin/knowledge?view=review');
    expect(await screen.findByText(/你不在指定审核名单中/)).toBeInTheDocument();
    expect(api.fetchPendingReviews).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: '待我审核' })).not.toBeInTheDocument();
  });
});

describe('匿名审核UI', () => {
  function openReview() {
    render(<MemoryRouter initialEntries={['/review/9']}><Routes><Route path="/review/:reportId" element={<KnowledgeReviewPage />} /><Route path="/admin/knowledge" element={<p>返回待审列表</p>} /></Routes></MemoryRouter>);
  }

  it('通过和驳回都要求意见，通过另要求价值点并提交版本', async () => {
    openReview();
    await screen.findByText('匿名充电问题');
    expect(screen.getByRole('button', { name: '驳回补充' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '通过并提交财务' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('审核意见（通过 / 驳回均必填）'), { target: { value: '报告完整' } });
    expect(screen.getByRole('button', { name: '驳回补充' })).toBeEnabled();
    expect(screen.getByRole('button', { name: '通过并提交财务' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('价值点（通过时必填）'), { target: { value: '可复用' } });
    fireEvent.click(screen.getByRole('button', { name: '通过并提交财务' }));
    await waitFor(() => expect(api.submitKnowledgeReview).toHaveBeenCalledWith(report, 'approve', '报告完整', '可复用'));
    expect(Toast).toHaveBeenCalledWith(expect.objectContaining({ message: '内容审核通过，已提交财务审核' }));
    expect(await screen.findByText('返回待审列表')).toBeInTheDocument();
  });

  it('检索异常不能展示成没有相似问题', async () => {
    vi.mocked(api.fetchReviewSimilar).mockResolvedValue({ similar: [], retrieval_status: 'unavailable' });
    openReview();
    expect(await screen.findByRole('alert')).toHaveTextContent('检索暂不可用');
    expect(screen.queryByText('暂无达到阈值的相似工单。')).not.toBeInTheDocument();
    expect(screen.queryByText('提单甲')).not.toBeInTheDocument();
    expect(api.fetchReviewSimilar).toHaveBeenCalledWith(7);
  });
});
