import { beforeEach, describe, expect, it, vi } from 'vitest';
import API_CONFIG from '@/config/api';
import * as learning from '../learning';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/api/client', () => ({ createRequest: vi.fn(() => mocks.request) }));

beforeEach(() => { mocks.request.mockReset(); mocks.request.mockResolvedValue({}); });

describe('知识点与题库API协议', () => {
  it('复用后台基址，搜索编码且绕过缓存', async () => {
    const { createRequest } = await import('@/api/client');
    await learning.listLearningArticles('充电 & 排查', 'draft', 2);
    expect(createRequest).toHaveBeenCalledWith(API_CONFIG.ADMIN.BASE_URL, '知识点与题库');
    const [path, options] = mocks.request.mock.calls[0];
    const params = new URLSearchParams(path.split('?')[1]);
    expect(params.get('keyword')).toBe('充电 & 排查');
    expect(params.get('page')).toBe('2');
    expect(options.skipCache).toBe(true);
  });

  it('AI请求携带幂等标识、修订号和延长超时', async () => {
    const body = { request_id: 'request-123', revision: 3, kind: 'questions' as const, count: 5, question_types: ['single_choice' as const] };
    await learning.generateLearning('article-1', body);
    expect(mocks.request).toHaveBeenCalledWith('/learning/articles/article-1/generate', expect.objectContaining({ method: 'POST', timeout: 180000, body: JSON.stringify(body), skipCache: true }));
  });

  it('审核与启用是不同操作，只有审核传入核验确认', async () => {
    await learning.actLearningQuestion('question-1', 2, 'approve');
    await learning.actLearningQuestion('question-1', 3, 'enable');
    expect(JSON.parse(mocks.request.mock.calls[0][1].body)).toEqual({ revision: 2, action: 'approve', reason: '', verified: true });
    expect(JSON.parse(mocks.request.mock.calls[1][1].body)).toEqual({ revision: 3, action: 'enable', reason: '', verified: false });
  });

  it('预览不提交真实学习记录，仅调用后台模拟接口', async () => {
    await learning.getLearningPreview('article-1', true);
    await learning.answerLearningPreview('article-1', 'question-1', ['A']);
    expect(mocks.request.mock.calls[0][0]).toBe('/learning/articles/article-1/preview?available_only=true');
    expect(mocks.request.mock.calls[1][0]).toBe('/learning/articles/article-1/preview/answer');
    expect(JSON.parse(mocks.request.mock.calls[1][1].body)).toEqual({ question_id: 'question-1', answers: ['A'] });
  });
});
