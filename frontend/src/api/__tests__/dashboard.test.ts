import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockRequest = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => mockRequest }));

import { fetchDashboardQuestions, fetchTicketsByStatus } from '../dashboard';

describe('fetchTicketsByStatus', () => {
  beforeEach(() => {
    mockRequest.mockReset();
    mockRequest.mockResolvedValue({ code: 0, data: { items: [], total: 25 } });
  });

  it('默认请求第一页并保留真实总数', async () => {
    expect(await fetchTicketsByStatus('all')).toEqual({ items: [], total: 25 });
    expect(mockRequest).toHaveBeenCalledWith('/dashboard/tickets?status=all&skip=0&limit=20', { skipCache: true });
  });

  it('分页与项目筛选参数同时传入', async () => {
    await fetchTicketsByStatus('pending', ['P1', 'P2'], { skip: 20, limit: 20 });
    expect(mockRequest).toHaveBeenCalledWith(
      '/dashboard/tickets?status=pending&skip=20&limit=20&project_ids=P1%2CP2',
      { skipCache: true },
    );
  });

  it('无关联项目时明确传空范围，不回退全量', async () => {
    await fetchTicketsByStatus('overdue', []);
    expect(mockRequest).toHaveBeenCalledWith(
      '/dashboard/tickets?status=overdue&skip=0&limit=20&project_ids=', { skipCache: true },
    );
  });

  it('请求失败不伪装成零条工单', async () => {
    mockRequest.mockRejectedValueOnce(new Error('network error'));
    await expect(fetchTicketsByStatus('all')).rejects.toThrow('network error');
    mockRequest.mockResolvedValueOnce({ code: 1 });
    await expect(fetchTicketsByStatus('all')).rejects.toThrow('工单列表加载失败');
  });
});

describe('fetchDashboardQuestions', () => {
  beforeEach(() => mockRequest.mockReset());

  it('按日查询，分页请求跳过通用缓存', async () => {
    const data = { report_date: '2026-09-28', items: [], total: 25, page: 2, page_size: 20, last_sync_at: null };
    mockRequest.mockResolvedValue({ code: 0, data });
    expect(await fetchDashboardQuestions('2026-09-28', 2)).toEqual(data);
    expect(mockRequest).toHaveBeenCalledWith(
      '/dashboard/questions?report_date=2026-09-28&page=2&page_size=20', { skipCache: true },
    );
  });

  it('默认第一页，并允许指定每页条数', async () => {
    mockRequest.mockResolvedValue({ code: 0, data: { items: [] } });
    await fetchDashboardQuestions('2026-10-13');
    expect(mockRequest).toHaveBeenLastCalledWith(
      '/dashboard/questions?report_date=2026-10-13&page=1&page_size=20', { skipCache: true },
    );
    await fetchDashboardQuestions('2026-10-13', 3, 10);
    expect(mockRequest).toHaveBeenLastCalledWith(
      '/dashboard/questions?report_date=2026-10-13&page=3&page_size=10', { skipCache: true },
    );
  });

  it('网络和业务错误不伪装成无数据', async () => {
    mockRequest.mockRejectedValueOnce(new Error('权限不足'));
    await expect(fetchDashboardQuestions('2026-10-13')).rejects.toThrow('权限不足');
    mockRequest.mockResolvedValueOnce({ code: 1 });
    await expect(fetchDashboardQuestions('2026-10-13')).rejects.toThrow('问题列表加载失败');
    mockRequest.mockResolvedValueOnce({ code: 0 });
    await expect(fetchDashboardQuestions('2026-10-13')).rejects.toThrow('问题列表加载失败');
  });
});
