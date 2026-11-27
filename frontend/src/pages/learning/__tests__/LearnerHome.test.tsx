import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import LearnerHome from '../LearnerHome';

vi.mock('@/api/learner', () => ({
  listLearnerArticles: vi.fn().mockResolvedValue({ items: [{ id: 'article1', title: '供电检查规范', category: '维护',
    total_count: 3, answered_count: 1, correct_count: 1, completed: false }], total: 1 }),
  getLearnerProgress: vi.fn().mockResolvedValue({ total_articles: 1, completed_articles: 0,
    total_questions: 3, answered_questions: 1, correct_questions: 1 }),
}));
vi.mock('tdesign-mobile-react', () => ({ Navbar: () => <header>学习中心</header>, Loading: () => <p>加载中</p>, Toast: vi.fn() }));

describe('学习任务内容范围', () => {
  it('仅展示知识点答题内容，不再展示审核工单或我提交的审核', async () => {
    render(<MemoryRouter><LearnerHome /></MemoryRouter>);
    expect(await screen.findByText('供电检查规范')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '工单任务' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '学习任务' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '题目审核工单' })).not.toBeInTheDocument();
    expect(screen.queryByText(/我提交的审核/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '刷新审核工单' })).not.toBeInTheDocument();
  });
});
