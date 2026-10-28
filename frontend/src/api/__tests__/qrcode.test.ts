import { beforeEach, describe, expect, it, vi } from 'vitest';

const request = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => request }));

import { downloadQrcodes } from '../qrcode';

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
