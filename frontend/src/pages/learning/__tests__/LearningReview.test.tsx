import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/learningReviews';
import LearningReviewDetail from '../LearningReviewDetail';
import LearningReviewInbox from '../LearningReviewInbox';

vi.mock('@/api/learningReviews', () => ({ listLearningReviews: vi.fn(), getLearningReview: vi.fn(), decideLearningReview: vi.fn() }));
vi.mock('@/shared/components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <p>{content}</p> }));

const detail: api.LearningReviewDetail = {
  id: '123', title: '题目审核：首先检查什么？', status: 'in_progress', assigned_to: 'alice', created_at: '2026-11-27T01:00:00',
  outcome: null, reason: '', can_review: true, stale: false, revision: 2,
  article: { title: '充电检查规范', category: '维护', tags: [], summary: '先检查供电。', applicability: '断电后操作。',
    key_points: [{ text: '先供电后触点。' }], method: '逐项检查。', cautions: '不得带电操作。', example: '' },
  question: { question_type: 'single_choice', stem: '首先检查什么？', options: [{ key: 'A', text: '供电' }, { key: 'B', text: '触点' }],
    correct_answer: ['A'], explanation: '文章要求先检查供电。', source_excerpt: '先检查供电。', point_ids: ['KP1'], difficulty: 'medium' },
};

function renderDetail() {
  render(<MemoryRouter initialEntries={['/tasks/learning/reviews/123']}><Routes>
    <Route path="/tasks/learning/reviews/:taskId" element={<LearningReviewDetail />} />
    <Route path="/tasks/learning" element={<p>已返回学习任务</p>} />
  </Routes></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getLearningReview).mockResolvedValue(structuredClone(detail));
  vi.mocked(api.listLearningReviews).mockResolvedValue({ items: [detail], total: 1 });
});

describe('学习任务题目审核工单', () => {
  it('展示我相关的审核工单并打开审核详情', async () => {
    vi.mocked(api.listLearningReviews).mockResolvedValue({ items: [detail, { ...detail, id: '124', assigned_to: null, can_review: false }], total: 2 });
    render(<MemoryRouter><Routes><Route path="/" element={<LearningReviewInbox />} />
      <Route path="/tasks/learning/reviews/123" element={<p>已进入审核详情</p>} /></Routes></MemoryRouter>);
    expect(await screen.findByText('待自动派单')).toBeInTheDocument();
    expect(screen.getByText('待审核')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '去审核' }));
    expect(screen.getByText('已进入审核详情')).toBeInTheDocument();
  });

  it('先显示知识点再显示完整审核题目，核验后才能通过且不直接启用题目', async () => {
    vi.mocked(api.decideLearningReview).mockResolvedValue({ ...detail, status: 'closed', outcome: 'approved', can_review: false });
    renderDetail();
    const article = await screen.findByRole('heading', { name: '充电检查规范' });
    const question = screen.getByRole('heading', { name: '待审核题目' });
    expect(article.compareDocumentPosition(question) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('正确答案：A')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '审核通过' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    await waitFor(() => expect(api.decideLearningReview).toHaveBeenCalledWith('123', { revision: 2, action: 'approve', verified: true, reason: '' }));
    expect(await screen.findByText('已通过')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '审核通过' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回学习任务' }));
    expect(screen.getByText('已返回学习任务')).toBeInTheDocument();
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
});
