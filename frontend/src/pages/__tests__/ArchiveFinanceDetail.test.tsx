import type { ReactNode } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchArchiveFinanceDetail } from '@/api/wxpay';
import ArchiveFinanceDetail from '../admin/ArchiveFinanceDetail';

vi.mock('@/api/wxpay', () => ({
  fetchArchiveFinanceDetail: vi.fn(),
  fenToYuan: (amount: number) => (amount / 100).toFixed(2),
}));
vi.mock('tdesign-mobile-react', () => ({
  Button: ({ children, onClick }: { children: ReactNode; onClick?: () => void }) => <button onClick={onClick}>{children}</button>,
  Loading: ({ text }: { text: string }) => <p>{text}</p>,
}));
vi.mock('../admin/KnowledgeQuestionDetail', () => ({
  KnowledgeMarkdown: ({ text }: { text: string }) => <div>{text}</div>,
}));

const detail = { task_id: 1, report_id: 2, title: '充电工单', problem: '充电中断',
  content: '检查电源后恢复', review_comment: '报告完整', value_point: '可复用排查方法', total_amount: 600 };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchArchiveFinanceDetail).mockResolvedValue(detail);
});

describe('现有奖励管理的工单财务审核资料', () => {
  it('展示工单、报告、审核意见、价值点及三方奖励总额，可关闭详情', async () => {
    const onClose = vi.fn();
    render(<ArchiveFinanceDetail taskId={1} onClose={onClose} />);
    expect(await screen.findByText('充电工单')).toBeInTheDocument();
    for (const text of ['充电中断', '检查电源后恢复', '报告完整', '可复用排查方法', '三方奖励合计：¥6.00']) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(screen.getByText(/通过或驳回将同时处理该工单的三方奖励/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '关闭详情' }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(fetchArchiveFinanceDetail).toHaveBeenCalledWith(1);
  });

  it('失败时显示真实错误并支持重试，不冒充空报告', async () => {
    vi.mocked(fetchArchiveFinanceDetail).mockRejectedValueOnce(new Error('权限不足'));
    render(<ArchiveFinanceDetail taskId={1} onClose={() => {}} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('权限不足');
    expect(screen.queryByText('归档报告')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
    expect(await screen.findByText('检查电源后恢复')).toBeInTheDocument();
    expect(fetchArchiveFinanceDetail).toHaveBeenCalledTimes(2);
  });

  it('工单切换后忽略旧请求返回，避免展示上一单的财务资料', async () => {
    let resolveOld: (value: typeof detail) => void = () => {};
    vi.mocked(fetchArchiveFinanceDetail).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; }));
    const view = render(<ArchiveFinanceDetail taskId={1} onClose={() => {}} />);
    vi.mocked(fetchArchiveFinanceDetail).mockResolvedValueOnce({ ...detail, task_id: 3, title: '新工单' });
    view.rerender(<ArchiveFinanceDetail taskId={3} onClose={() => {}} />);
    expect(await screen.findByText('新工单')).toBeInTheDocument();
    resolveOld(detail);
    await waitFor(() => expect(screen.queryByText('充电工单')).not.toBeInTheDocument());
    expect(screen.getByText('新工单')).toBeInTheDocument();
  });
});
