import { describe, it, expect, beforeEach, vi } from 'vitest';
import { clearToken, clearCache, setToken } from '../client';
import { resetProjectInfoTreeApi, fetchLedgerSyncPreviewApi } from '../infoNodes';

/** 假响应：只要 ok/status/json 三件套（与 client.test.ts 同一套写法） */
const res = (status: number, body: unknown): Response => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
} as Response);

const PREVIEW = {
  project_id: 'P1',
  project_name: '中力越南项目',
  project_code: 'P1',
  ledger_updated_at: '2026-09-19 11:20',
  field_count: 3,
  mirror_field_total: 5,
  fill: [], overwrite: [], unmatched: [],
};

describe('fetchLedgerSyncPreviewApi（台账同步预览）', () => {
  beforeEach(() => {
    clearToken();
    clearCache();
    vi.restoreAllMocks();
    setToken('test-token');
  });

  it('正常返回时把三组预览与元信息归一化', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(res(200, PREVIEW));
    const data = await fetchLedgerSyncPreviewApi('P1');
    expect(data.field_count).toBe(3);
    expect(data.ledger_updated_at).toBe('2026-09-19 11:20');
    expect(data.fill).toEqual([]);
  });

  it('后端没有这条路由（FastAPI 默认 404）时换成看得懂的提示', async () => {
    // 改了后端路由但没重启后端时就是这个响应：{"detail":"Not Found"}
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(res(404, { detail: 'Not Found' }));
    await expect(fetchLedgerSyncPreviewApi('P1')).rejects.toThrow(/后端没有「台账同步」接口/);
  });

  it('后端自己的 404（项目不存在）原话透传，不换成上面那句', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(res(404, { detail: '项目不存在' }));
    await expect(fetchLedgerSyncPreviewApi('P1')).rejects.toThrow('项目不存在');
  });

  it('400（项目还没有信息节点）原话透传', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      res(400, { detail: '该项目还没有信息节点，请先在编辑页新建节点后再同步' }),
    );
    await expect(fetchLedgerSyncPreviewApi('P1')).rejects.toThrow('还没有信息节点');
  });
});

describe('resetProjectInfoTreeApi（一键清空：恢复为模板结构）', () => {
  beforeEach(() => {
    clearToken();
    clearCache();
    vi.restoreAllMocks();
    setToken('test-token');
  });

  it('POST 到 reset-to-template，返回清掉的内容数与删掉的节点数', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      res(200, { cleared: 132, nodes_removed: 4 }),
    );
    expect(await resetProjectInfoTreeApi('P1')).toEqual({ cleared: 132, nodesRemoved: 4 });
    expect(String(fetchMock.mock.calls[0][0])).toContain('/info-nodes/projects/P1/reset-to-template');
    expect(fetchMock.mock.calls[0][1]?.method).toBe('POST');
  });

  it('后端没回计数时按 0 处理（提示「没有可清的内容」而不是崩）', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(res(200, {}));
    expect(await resetProjectInfoTreeApi('P1')).toEqual({ cleared: 0, nodesRemoved: 0 });
  });

  it('403（不是这个项目的人）原话透传', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      res(403, { detail: '只有该项目下的人员可以编辑项目信息树' }),
    );
    await expect(resetProjectInfoTreeApi('P1')).rejects.toThrow('只有该项目下的人员');
  });
});
