import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DashboardQuestion, DashboardQuestions } from '@/api/dashboard';

const mockFetchQuestions = vi.hoisted(() => vi.fn());
vi.mock('@/api/dashboard', () => ({ fetchDashboardQuestions: mockFetchQuestions }));

import QuestionModuleCard from '../admin/QuestionModuleCard';

const question: DashboardQuestion = {
  record_id: 'record-1', reported_at: '2026-10-13T14:54:00+08:00',
  problem_description: '车辆执行任务异常', solution_description: '检查配置后恢复',
  vehicle_info: '00123 / XP3', vehicle_number: '', vehicle_model: '',
};

function response(reportDate: string, overrides: Partial<DashboardQuestions> = {}): DashboardQuestions {
  return {
    report_date: reportDate, total: 1, page: 1, page_size: 20,
    items: [question], last_sync_at: '2026-10-13T01:00:00Z', ...overrides,
  };
}

describe('问题模块卡片', () => {
  beforeEach(() => {
    mockFetchQuestions.mockReset();
    mockFetchQuestions.mockImplementation((reportDate: string) => Promise.resolve(response(reportDate)));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('默认日期为北京时间当天，而非UTC日期', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-12T18:00:00Z'));
    await act(async () => { render(<QuestionModuleCard />); });
    expect(screen.getByLabelText('上报日期')).toHaveValue('2026-10-13');
    expect(mockFetchQuestions).toHaveBeenCalledWith('2026-10-13', 1);
  });

  it('展示真实合并车辆字段及两类描述，不擅自拆分', async () => {
    render(<QuestionModuleCard />);
    expect(screen.getByRole('heading', { name: '问题模块' })).toBeInTheDocument();
    await screen.findByText('车辆执行任务异常');
    expect(screen.getByText('车辆编号和车型：00123 / XP3')).toBeInTheDocument();
    expect(screen.getByText('检查配置后恢复')).toBeInTheDocument();
    expect(screen.getByText('共 1 条问题 · 按北京时间筛选')).toBeInTheDocument();
    expect(screen.getByText(/数据最近同步时间/)).toBeInTheDocument();
  });

  it('切换日期回到第一页，今天按钮恢复当天', async () => {
    render(<QuestionModuleCard />);
    await screen.findByText('车辆执行任务异常');
    const today = (screen.getByLabelText('上报日期') as HTMLInputElement).value;
    fireEvent.change(screen.getByLabelText('上报日期'), { target: { value: '2026-09-28' } });
    await waitFor(() => expect(mockFetchQuestions).toHaveBeenLastCalledWith('2026-09-28', 1));
    await screen.findByText('车辆执行任务异常');
    fireEvent.click(screen.getByRole('button', { name: '今天' }));
    await waitFor(() => expect(mockFetchQuestions).toHaveBeenLastCalledWith(today, 1));
  });

  it('加载更多追加记录且保留真实总数', async () => {
    mockFetchQuestions.mockImplementation((reportDate: string, page: number) => Promise.resolve(response(reportDate, {
      total: 21, page,
      items: page === 1
        ? Array.from({ length: 20 }, (_, index) => ({ ...question, record_id: `record-${index}`, problem_description: `问题${index}` }))
        : [{ ...question, record_id: 'record-20', problem_description: '最后一个问题' }],
    })));
    render(<QuestionModuleCard />);
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    await screen.findByText('最后一个问题');
    expect(screen.getByText('问题0')).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(21);
    expect(screen.queryByRole('button', { name: '加载更多' })).not.toBeInTheDocument();
    expect(screen.getByText('共 21 条问题 · 按北京时间筛选')).toBeInTheDocument();
  });

  it('空字段和空日期有明确提示', async () => {
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, {
      items: [{ ...question, problem_description: '', solution_description: '', vehicle_info: '' }],
    })));
    render(<QuestionModuleCard />);
    await screen.findByText('车辆编号和车型：未填写');
    expect(screen.getByText('暂未填写')).toBeInTheDocument();
    expect(screen.getByText('未填写')).toBeInTheDocument();
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, { total: 0, items: [] })));
    fireEvent.change(screen.getByLabelText('上报日期'), { target: { value: '2026-09-28' } });
    await screen.findByText('该日期暂无上报问题');
  });

  it('接口失败显示重试，不伪装成暂无数据', async () => {
    mockFetchQuestions.mockRejectedValueOnce(new Error('权限不足'));
    render(<QuestionModuleCard />);
    expect(await screen.findByRole('alert')).toHaveTextContent('权限不足');
    expect(screen.queryByText('该日期暂无上报问题')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByText('车辆执行任务异常');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('加载更多失败保留记录，重试请求同一页', async () => {
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, { total: 21 })));
    mockFetchQuestions.mockRejectedValueOnce(new Error('网络异常'));
    render(<QuestionModuleCard />);
    fireEvent.click(await screen.findByRole('button', { name: '加载更多' }));
    await screen.findByRole('alert');
    expect(screen.getByText('车辆执行任务异常')).toBeInTheDocument();
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, {
      page: 2, total: 21, items: [{ ...question, record_id: 'second', problem_description: '第二页问题' }],
    })));
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    await screen.findByText('第二页问题');
    expect(mockFetchQuestions.mock.lastCall?.[1]).toBe(2);
    expect(screen.getByText('车辆执行任务异常')).toBeInTheDocument();
  });

  it('快速切换日期不会被旧请求覆盖', async () => {
    let resolveOld!: (data: DashboardQuestions) => void;
    mockFetchQuestions.mockImplementationOnce(() => new Promise<DashboardQuestions>((resolve) => { resolveOld = resolve; }));
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, {
      items: [{ ...question, problem_description: '新日期问题' }],
    })));
    render(<QuestionModuleCard />);
    const oldDate = (screen.getByLabelText('上报日期') as HTMLInputElement).value;
    fireEvent.change(screen.getByLabelText('上报日期'), { target: { value: '2026-09-28' } });
    await screen.findByText('新日期问题');
    await act(async () => { resolveOld(response(oldDate, { items: [{ ...question, problem_description: '旧日期问题' }] })); });
    expect(screen.queryByText('旧日期问题')).not.toBeInTheDocument();
    expect(screen.getByText('新日期问题')).toBeInTheDocument();
  });

  it('刷新重新请求第一页', async () => {
    render(<QuestionModuleCard />);
    await screen.findByText('车辆执行任务异常');
    fireEvent.click(screen.getByRole('button', { name: '刷新' }));
    await waitFor(() => expect(mockFetchQuestions).toHaveBeenCalledTimes(2));
    expect(mockFetchQuestions.mock.lastCall?.[1]).toBe(1);
  });

  it('长描述可以展开收起，源HTML按纯文本展示', async () => {
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(200);
    const text = '<script>alert(1)</script>\n长描述';
    mockFetchQuestions.mockImplementationOnce((reportDate: string) => Promise.resolve(response(reportDate, {
      items: [{ ...question, problem_description: text }],
    })));
    const { container } = render(<QuestionModuleCard />);
    const expand = await screen.findByRole('button', { name: '展开问题描述' });
    fireEvent.click(expand);
    expect(screen.getByRole('button', { name: '收起问题描述' })).toHaveAttribute('aria-expanded', 'true');
    expect(container.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '收起问题描述' }));
    expect(screen.getByRole('button', { name: '展开问题描述' })).toHaveAttribute('aria-expanded', 'false');
  });
});
