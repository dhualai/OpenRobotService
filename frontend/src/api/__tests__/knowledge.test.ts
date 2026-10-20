import { beforeEach, describe, expect, it, vi } from 'vitest';

const mockRequest = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => mockRequest }));

import { createKnowledgeDiscussion, fetchContributorQuestions, fetchKnowledgeDiscussions, fetchKnowledgeQuestions } from '../knowledge';

describe('fetchKnowledgeQuestions', () => {
  beforeEach(() => {
    mockRequest.mockReset();
    mockRequest.mockResolvedValue({ items: [], total: 0 });
  });

  it('完整列表仍默认每页20条', async () => {
    await fetchKnowledgeQuestions('2026-11', 'created_at', 2);
    expect(mockRequest).toHaveBeenCalledWith('/questions?sort=created_at&page=2&size=20&month=2026-11', { skipCache: true });
  });

  it('前三预览只请求3条，并保留点赞排序和月份', async () => {
    await fetchKnowledgeQuestions('2026-12', 'likes', 1, 3);
    expect(mockRequest).toHaveBeenCalledWith('/questions?sort=likes&page=1&size=3&month=2026-12', { skipCache: true });
  });

  it('本期全部问题不添加月份过滤', async () => {
    await fetchKnowledgeQuestions('', 'likes', 1);
    expect(mockRequest).toHaveBeenCalledWith('/questions?sort=likes&page=1&size=20', { skipCache: true });
  });

  it('人员工单请求保留月份、角色和分页，编码人员ID', async () => {
    await fetchContributorQuestions('user/name', '2026-12', 'answerer', 2);
    expect(mockRequest).toHaveBeenCalledWith('/contributors/user%2Fname/questions?role=answerer&page=2&size=20&month=2026-12', { skipCache: true });
  });

  it('知识讨论使用独立接口，读取跳过缓存', async () => {
    await fetchKnowledgeDiscussions('7', 2);
    expect(mockRequest).toHaveBeenCalledWith('/questions/7/discussions?page=2&size=20', { skipCache: true });
    await createKnowledgeDiscussion('7', '补充经验');
    expect(mockRequest).toHaveBeenLastCalledWith('/questions/7/discussions', { method: 'POST', body: JSON.stringify({ content: '补充经验' }) });
  });
});
