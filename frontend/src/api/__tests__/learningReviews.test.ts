import { beforeEach, describe, expect, it, vi } from 'vitest';
import API_CONFIG from '@/config/api';
import { decideLearningReview, getLearningReview, listLearningReviews } from '../learningReviews';

const mocks = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/api/client', () => ({ createRequest: vi.fn(() => mocks.request) }));
beforeEach(() => { mocks.request.mockReset(); mocks.request.mockResolvedValue({}); });

describe('学习审核接口', () => {
  it('使用登录用户的任务接口，保留字符串工单号并携带修订号', async () => {
    const { createRequest } = await import('@/api/client');
    const body = { revision: 2, action: 'reject' as const, verified: false, reason: '请补充依据' };
    await listLearningReviews(2);
    await getLearningReview('9007199254740993');
    await decideLearningReview('9007199254740993', body);
    expect(createRequest).toHaveBeenCalledWith(API_CONFIG.TASKS.BASE_URL, '学习审核');
    expect(mocks.request.mock.calls[0]).toEqual(['/learning/reviews?page=2', { skipCache: true }]);
    expect(mocks.request.mock.calls[1]).toEqual(['/learning/reviews/9007199254740993', { skipCache: true }]);
    expect(mocks.request.mock.calls[2]).toEqual(['/learning/reviews/9007199254740993/decision', { skipCache: true, method: 'POST', body: JSON.stringify(body) }]);
  });
});
