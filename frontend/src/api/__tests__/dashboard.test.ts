import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockRequest = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => mockRequest }));

import { fetchTicketsByStatus } from '../dashboard';

describe('fetchTicketsByStatus', () => {
  beforeEach(() => {
    mockRequest.mockReset();
    mockRequest.mockResolvedValue({ code: 0, data: { items: [], total: 25 } });
  });

  it('默认请求第一页并保留真实总数', async () => {
    expect(await fetchTicketsByStatus('all')).toEqual({ items: [], total: 25 });
    expect(mockRequest).toHaveBeenCalledWith('/dashboard/tickets?status=all&skip=0&limit=20');
  });

  it('分页与项目筛选参数同时传入', async () => {
    await fetchTicketsByStatus('pending', ['P1', 'P2'], { skip: 20, limit: 20 });
    expect(mockRequest).toHaveBeenCalledWith(
      '/dashboard/tickets?status=pending&skip=20&limit=20&project_ids=P1%2CP2',
    );
  });

  it('无关联项目时明确传空范围，不回退全量', async () => {
    await fetchTicketsByStatus('overdue', []);
    expect(mockRequest).toHaveBeenCalledWith('/dashboard/tickets?status=overdue&skip=0&limit=20&project_ids=');
  });

  it('请求失败不伪装成零条工单', async () => {
    mockRequest.mockRejectedValueOnce(new Error('network error'));
    await expect(fetchTicketsByStatus('all')).rejects.toThrow('network error');
    mockRequest.mockResolvedValueOnce({ code: 1 });
    await expect(fetchTicketsByStatus('all')).rejects.toThrow('工单列表加载失败');
  });
});
