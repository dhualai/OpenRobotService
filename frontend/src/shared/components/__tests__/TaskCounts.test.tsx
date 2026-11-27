import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MainLayout from '../MainLayout';
import TaskCategoryTabs from '../TaskCategoryTabs';
import { useTaskCounts } from '../TaskCountsContext';
import { buildRelevanceFilters } from '@/shared/utils/ticketFilters';

const mocks = vi.hoisted(() => ({ request: vi.fn(), questionCount: vi.fn(), followupCount: vi.fn(),
  auth: { username: 'alice-account', userId: 'alice', projectIds: ['P1'] } }));
vi.mock('@/stores/auth', () => ({ useAuthStore: () => mocks.auth }));
vi.mock('@/api/client', () => ({ createRequest: () => mocks.request }));
vi.mock('@/api/learner', () => ({ getLearningQuestionCount: () => mocks.questionCount() }));
vi.mock('@/api/ticket', () => ({ getMyFollowupsCount: () => mocks.followupCount() }));
vi.mock('../AdminDataAssistant', () => ({ default: () => null }));
vi.mock('tdesign-mobile-react', () => ({ Loading: () => <p>加载中</p> }));

function CountsContent() {
  const { refresh } = useTaskCounts();
  return <><TaskCategoryTabs /><button onClick={refresh}>刷新数量</button></>;
}
function renderCounts() {
  return render(<MemoryRouter initialEntries={['/tasks']}><Routes><Route element={<MainLayout />}>
    <Route path="*" element={<CountsContent />} />
  </Route></Routes></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.request.mockResolvedValue({ total: 5 });
  mocks.questionCount.mockResolvedValue({ count: 4 });
  mocks.followupCount.mockResolvedValue({ count: 0 });
});

describe('系统任务及两类任务角标', () => {
  it('工单角标沿用含审核单的待我处理数，学习角标仅计题目，总角标相加且不重复计审核单', async () => {
    renderCounts();
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('9'));
    expect(screen.getByTestId('task-category-count-tickets')).toHaveTextContent('5');
    expect(screen.getByTestId('task-category-count-learning')).toHaveTextContent('4');
    const body = JSON.parse(mocks.request.mock.calls[0][1].body);
    expect(body.filters).toEqual(buildRelevanceFilters('mine', 'alice', ['P1']));
    expect(screen.getByRole('tab', { name: /工单任务/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: /学习任务/ }));
    expect(screen.getByRole('tab', { name: /学习任务/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('处理工单或变更题目后刷新总角标和分类角标', async () => {
    renderCounts();
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('9'));
    mocks.request.mockResolvedValue({ total: 4 });
    mocks.questionCount.mockResolvedValue({ count: 3 });
    fireEvent.click(screen.getByRole('button', { name: '刷新数量' }));
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('7'));
    expect(screen.getByTestId('task-category-count-tickets')).toHaveTextContent('4');
    expect(screen.getByTestId('task-category-count-learning')).toHaveTextContent('3');
  });

  it('一个计数接口失败不影响另一个，刷新失败保留上次成功数', async () => {
    mocks.questionCount.mockRejectedValueOnce(new Error('暂不可用'));
    renderCounts();
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('5'));
    expect(screen.queryByTestId('task-category-count-learning')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新数量' }));
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('9'));
    mocks.request.mockRejectedValue(new Error('暂不可用'));
    mocks.questionCount.mockResolvedValue({ count: 2 });
    fireEvent.click(screen.getByRole('button', { name: '刷新数量' }));
    await waitFor(() => expect(screen.getByTestId('nav-badge-tasks')).toHaveTextContent('7'));
    expect(screen.getByTestId('task-category-count-tickets')).toHaveTextContent('5');
  });

  it('零数量不展示角标，大数量显示999+', async () => {
    mocks.request.mockResolvedValue({ total: 0 });
    mocks.questionCount.mockResolvedValue({ count: 0 });
    renderCounts();
    await waitFor(() => expect(mocks.questionCount).toHaveBeenCalled());
    expect(screen.queryByTestId('nav-badge-tasks')).not.toBeInTheDocument();
    expect(screen.queryByTestId('task-category-count-tickets')).not.toBeInTheDocument();
    expect(screen.queryByTestId('task-category-count-learning')).not.toBeInTheDocument();
    mocks.request.mockResolvedValue({ total: 1000 });
    fireEvent.click(screen.getByRole('button', { name: '刷新数量' }));
    expect(await screen.findByTestId('nav-badge-tasks')).toHaveTextContent('999+');
    expect(screen.getByTestId('task-category-count-tickets')).toHaveTextContent('999+');
  });
});
