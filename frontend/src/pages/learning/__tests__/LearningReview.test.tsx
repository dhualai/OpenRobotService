import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/learningReviews';
import LearningReviewDetail from '../LearningReviewDetail';
import LearningReviewInbox from '../LearningReviewInbox';
import { TaskCountsContext } from '@/shared/components/TaskCountsContext';

vi.mock('@/api/learningReviews', () => ({ listLearningReviews: vi.fn(), getLearningReview: vi.fn(), decideLearningReview: vi.fn(), editLearningReview: vi.fn() }));
vi.mock('@/shared/components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <p>{content}</p> }));

const detail: api.LearningReviewDetail = {
  id: '123', title: '题目审核：首先检查什么？', status: 'in_progress', assigned_to: 'alice', created_at: '2026-11-27T01:00:00',
  outcome: null, reason: '', can_review: true, stale: false, revision: 2, article_revision: 3,
  article: { title: '充电检查规范', category: '维护', tags: [], summary: '先检查供电。', applicability: '断电后操作。',
    key_points: [{ id: 'KP1', text: '先供电后触点。' }], method: '逐项检查。', cautions: '不得带电操作。', example: '' },
  question: { question_type: 'single_choice', stem: '首先检查什么？', options: [{ key: 'A', text: '供电' }, { key: 'B', text: '触点' }],
    correct_answer: ['A'], explanation: '文章要求先检查供电。', source_excerpt: '先检查供电。', point_ids: ['KP1'], difficulty: 'medium' },
};

const refreshCounts = vi.fn();
const getComputedStyle = window.getComputedStyle;
beforeAll(() => { vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element)); });
afterAll(() => { vi.mocked(window.getComputedStyle).mockRestore(); });
function renderDetail() {
  render(<TaskCountsContext.Provider value={{ ticketCount: 1, learningCount: 0, refresh: refreshCounts }}>
    <MemoryRouter initialEntries={['/tasks/reviews/123']}><Routes>
      <Route path="/tasks/reviews/:taskId" element={<LearningReviewDetail />} />
      <Route path="/tasks" element={<p>已返回工单任务</p>} />
    </Routes></MemoryRouter>
  </TaskCountsContext.Provider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getLearningReview).mockResolvedValue(structuredClone(detail));
  vi.mocked(api.listLearningReviews).mockResolvedValue({ items: [detail], total: 1 });
});

describe('工单任务中的题目审核工单', () => {
  it('审核入口进入工单详情而不是学习任务', async () => {
    vi.mocked(api.listLearningReviews).mockResolvedValue({ items: [detail, { ...detail, id: '124', assigned_to: null, can_review: false }], total: 2 });
    render(<MemoryRouter><Routes><Route path="/" element={<LearningReviewInbox />} />
      <Route path="/tasks/reviews/123" element={<p>已进入审核详情</p>} /></Routes></MemoryRouter>);
    expect(await screen.findByText('待自动派单')).toBeInTheDocument();
    expect(screen.getByText('待审核')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去审核' }));
    expect(screen.getByText('已进入审核详情')).toBeInTheDocument();
  });

  it('先显示知识点再显示完整题目，可直接通过且不直接启用题目', async () => {
    vi.mocked(api.decideLearningReview).mockResolvedValue({ ...detail, status: 'closed', outcome: 'approved', can_review: false });
    renderDetail();
    const article = await screen.findByRole('heading', { name: '充电检查规范' });
    const question = screen.getByRole('heading', { name: '待审核题目' });
    expect(article.compareDocumentPosition(question) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('正确答案：A')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '审核通过' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    await waitFor(() => expect(api.decideLearningReview).toHaveBeenCalledWith('123', { revision: 2, action: 'approve', verified: true, reason: '' }));
    expect(await screen.findByText('已通过')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '审核通过' })).not.toBeInTheDocument();
    expect(refreshCounts).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: '返回工单任务' }));
    expect(screen.getByText('已返回工单任务')).toBeInTheDocument();
  });

  it('驳回必须填写意见，失败保留意见并支持重试', async () => {
    vi.mocked(api.decideLearningReview).mockRejectedValueOnce(new Error('审核暂不可用')).mockResolvedValue({
      ...detail, status: 'closed', outcome: 'rejected', can_review: false, reason: '请补充选项说明',
    });
    renderDetail();
    const reject = await screen.findByRole('button', { name: '驳回题目' });
    expect(reject).toBeDisabled();
    fireEvent.change(screen.getByRole('textbox', { name: '审核意见' }), { target: { value: '请补充选项说明' } });
    fireEvent.click(reject);
    expect(await screen.findByText('审核暂不可用')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '审核意见' })).toHaveValue('请补充选项说明');
    fireEvent.click(reject);
    expect(await screen.findByText('已驳回')).toBeInTheDocument();
    expect(api.decideLearningReview).toHaveBeenLastCalledWith('123', { revision: 2, action: 'reject', verified: false, reason: '请补充选项说明' });
  });

  it('非审核人和失效工单不显示审核操作，接口拒绝访问时显示错误', async () => {
    vi.mocked(api.getLearningReview).mockResolvedValue({ ...detail, can_review: false, stale: true });
    renderDetail();
    expect(await screen.findByText(/文章已停用或题目版本已变更/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑题目' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '编辑文章' })).not.toBeInTheDocument();
    vi.mocked(api.getLearningReview).mockRejectedValue(new Error('未找到与你相关的题目审核工单'));
    fireEvent.click(screen.getByRole('button', { name: '刷新工单' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('未找到与你相关的题目审核工单');
    expect(screen.queryByRole('heading', { name: '待审核题目' })).not.toBeInTheDocument();
  });

  it('列表加载失败显示错误且可以刷新重试', async () => {
    vi.mocked(api.listLearningReviews).mockRejectedValueOnce(new Error('工单列表暂不可用')).mockResolvedValue({ items: [], total: 0 });
    render(<MemoryRouter><LearningReviewInbox /></MemoryRouter>);
    expect(await screen.findByText('工单列表暂不可用')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新审核工单' }));
    expect(await screen.findByText('暂无相关题目审核工单')).toBeInTheDocument();
  });

  it('可同时编辑题目和文章，保存失败保留输入，重试后按最新修订号审核', async () => {
    const updated = { ...detail, revision: 3, article_revision: 4,
      article: { ...detail.article!, title: '修订后的充电规范' },
      question: { ...detail.question!, stem: '修订后的检查问题' } };
    vi.mocked(api.editLearningReview).mockRejectedValueOnce(new Error('保存暂不可用')).mockResolvedValue(updated);
    vi.mocked(api.decideLearningReview).mockResolvedValue({ ...updated, status: 'closed', outcome: 'approved', can_review: false });
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: '编辑题目' }));
    const dialog = within(screen.getByRole('dialog', { name: '编辑审核内容' }));
    fireEvent.change(dialog.getByRole('textbox', { name: '题干' }), { target: { value: '修订后的检查问题' } });
    fireEvent.click(dialog.getByRole('tab', { name: '知识点文章' }));
    fireEvent.change(dialog.getByRole('textbox', { name: '文章标题' }), { target: { value: '修订后的充电规范' } });
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }));
    expect(await dialog.findByText('保存暂不可用')).toBeInTheDocument();
    expect(dialog.getByRole('textbox', { name: '文章标题' })).toHaveValue('修订后的充电规范');
    fireEvent.click(dialog.getByRole('button', { name: '保存修改' }));
    await waitFor(() => expect(api.editLearningReview).toHaveBeenCalledTimes(2));
    expect(api.editLearningReview).toHaveBeenLastCalledWith('123', {
      revision: 2, article_revision: 3, article: updated.article, question: updated.question,
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(screen.getByRole('heading', { name: '修订后的充电规范' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    await waitFor(() => expect(api.decideLearningReview).toHaveBeenCalledWith('123', { revision: 3, action: 'approve', verified: true, reason: '' }));
    expect(refreshCounts).toHaveBeenCalledTimes(2);
  }, 10000);

  it('编辑文章入口直接打开文章页签，未保存修改需要确认才能放弃', async () => {
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: '编辑文章' }));
    const dialog = within(screen.getByRole('dialog', { name: '编辑审核内容' }));
    expect(dialog.getByRole('tab', { name: '知识点文章' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.change(dialog.getByRole('textbox', { name: '知识结论' }), { target: { value: '尚未保存的文章内容' } });
    fireEvent.click(dialog.getByRole('button', { name: /取\s*消/ }));
    expect(await screen.findAllByText('放弃未保存的审核修改？')).not.toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '继续编辑' }));
    expect(dialog.getByRole('textbox', { name: '知识结论' })).toHaveValue('尚未保存的文章内容');
    expect(api.editLearningReview).not.toHaveBeenCalled();
  });
});
