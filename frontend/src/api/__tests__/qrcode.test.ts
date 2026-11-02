import { beforeEach, describe, expect, it, vi } from 'vitest';

const request = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => request }));

import { downloadQrcodes, fetchAllQrcodes, fetchQrcodes, type QrcodeItem } from '../qrcode';

describe('downloadQrcodes', () => {
  beforeEach(() => vi.resetAllMocks());

  it('通过统一鉴权客户端发送范围并读取 ZIP 二进制', async () => {
    request.mockResolvedValue(new Uint8Array([80, 75, 3, 4]).buffer);
    const params = { scope: 'range' as const, id_start: 10, id_end: 20 };
    const result = await downloadQrcodes(params);
    expect(request).toHaveBeenCalledWith('/qrcodes/batch-download', {
      method: 'POST', body: JSON.stringify(params), responseType: 'arrayBuffer', timeout: 120_000,
    });
    expect(result.type).toBe('application/zip');
    expect(result.size).toBe(4);
  });

  it('保留服务端的错误原因，不把错误响应当 ZIP 返回', async () => {
    const error = new Error('权限不足');
    request.mockRejectedValue(error);
    await expect(downloadQrcodes({ scope: 'all' })).rejects.toBe(error);
  });
});

describe('二维码列表加载', () => {
  beforeEach(() => vi.resetAllMocks());

  it('将日期字段和起止日期传给列表接口', async () => {
    request.mockResolvedValue({ total: 0, items: [] });
    await fetchQrcodes({ date_field: 'ticket_created_at', date_start: '2026-10-27', date_end: '2026-10-28', skip: 20, limit: 20 });
    const query = new URLSearchParams(request.mock.calls[0][0].split('?')[1]);
    expect(query.get('date_field')).toBe('ticket_created_at');
    expect(query.get('date_start')).toBe('2026-10-27');
    expect(query.get('date_end')).toBe('2026-10-28');
    expect(query.get('skip')).toBe('20');
  });

  it('超过 500 条时继续翻页，全部加载且始终保留筛选条件', async () => {
    const rows = Array.from({ length: 501 }, (_, index) => ({ id: index + 1 }) as QrcodeItem);
    request.mockResolvedValueOnce({ total: 501, items: rows.slice(0, 500) })
      .mockResolvedValueOnce({ total: 501, items: rows.slice(500) });
    const result = await fetchAllQrcodes({ status: 'published', date_start: '2026-10-27' });
    expect(result.items).toEqual(rows);
    expect(result.total).toBe(501);
    expect(request).toHaveBeenCalledTimes(2);
    const queries = request.mock.calls.map(([url]) => new URLSearchParams(url.split('?')[1]));
    expect(queries.map((query) => query.get('skip'))).toEqual(['0', '500']);
    queries.forEach((query) => {
      expect(query.get('status')).toBe('published');
      expect(query.get('date_start')).toBe('2026-10-27');
      expect(query.get('limit')).toBe('500');
    });
  });

  it('空结果只查询一次，翻页失败不会返回部分数据冒充全部', async () => {
    request.mockResolvedValueOnce({ total: 0, items: [] });
    expect(await fetchAllQrcodes({})).toEqual({ total: 0, items: [] });
    expect(request).toHaveBeenCalledOnce();
    request.mockResolvedValueOnce({ total: 2, items: [{ id: 1 }] }).mockRejectedValueOnce(new Error('加载失败'));
    await expect(fetchAllQrcodes({})).rejects.toThrow('加载失败');
  });

  it('筛选条件已改变时停止加载旧请求的后续分页', async () => {
    let current = true;
    request.mockImplementationOnce(async () => {
      current = false;
      return { total: 2, items: [{ id: 1 }] };
    });
    await fetchAllQrcodes({}, () => current);
    expect(request).toHaveBeenCalledOnce();
  });
});
