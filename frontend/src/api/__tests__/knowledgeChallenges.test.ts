import { beforeEach, describe, expect, it, vi } from 'vitest';

const request = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => request }));
import {
  changeChallengeState, createChallenge, fetchChallengePreview, fetchChallenges,
  likeChallengeAnswer, overrideChallengeResult, saveChallengeAnswer,
} from '../knowledgeChallenges';

describe('高价值问题接口', () => {
  beforeEach(() => { request.mockReset(); request.mockResolvedValue({}); });
  it('首页使用独立前三接口，完整榜单保留分页且不附带原月榜月份', async () => {
    await fetchChallengePreview();
    expect(request).toHaveBeenLastCalledWith('/overview', { skipCache: true });
    await fetchChallenges('mine', 2);
    expect(request).toHaveBeenLastCalledWith('?view=mine&page=2&size=20', { skipCache: true });
  });
  it('独立发布不需要工单字段', async () => {
    const body = { request_key: 'one', title: '独立问题', content: '内容', bounty_fen: 30000, ends_at: '2026-12-01T00:00:00Z' };
    await createChallenge(body);
    expect(request).toHaveBeenCalledWith('', { method: 'POST', body: JSON.stringify(body) });
  });
  it('草稿保存和答案发布使用不同动作并携带版本', async () => {
    await saveChallengeAnswer('7', '草稿', 2);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer', { method: 'PUT', body: JSON.stringify({ content: '草稿', version: 2 }) });
    await saveChallengeAnswer('7', '发布', 3, true);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer/submit', { method: 'POST', body: JSON.stringify({ content: '发布', version: 3 }) });
  });
  it('答案点赞与问题点赞分开，取消使用DELETE', async () => {
    await likeChallengeAnswer(3, { id: 8, liked_by_me: true } as Parameters<typeof likeChallengeAnswer>[1]);
    expect(request).toHaveBeenCalledWith('/3/answers/8/like', { method: 'DELETE' });
  });
  it('管理状态与结果纠正独立于财务接口', async () => {
    const state = { action: 'pause', reason: '补充材料', version: 2 };
    await changeChallengeState(3, state);
    expect(request).toHaveBeenLastCalledWith('/3/state', { method: 'PATCH', body: JSON.stringify(state) });
    const result = { action: 'replace', reason: '更换结果', answer_id: 8, version: 3 };
    await overrideChallengeResult(3, result);
    expect(request).toHaveBeenLastCalledWith('/3/result/override', { method: 'POST', body: JSON.stringify(result) });
  });
});
