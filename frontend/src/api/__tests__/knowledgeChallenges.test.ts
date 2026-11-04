import { beforeEach, describe, expect, it, vi } from 'vitest';

const request = vi.hoisted(() => vi.fn());
vi.mock('@/api/client', () => ({ createRequest: () => request }));
import {
  changeChallengeState, createChallenge, fetchChallengePreview, fetchChallenges,
  likeChallengeAnswer, overrideChallengeResult, saveChallengeAnswer,
  uploadChallengeAnswerAttachment, downloadChallengeAnswerAttachment,
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
  it('图片列表随草稿与发布提交，空列表可以删除已有图片', async () => {
    const images = ['/api/tasks/files/helpdesk-comment/spec-doc/images/' + 'a'.repeat(32) + '.png'];
    await saveChallengeAnswer('7', '附图排查', 2, false, images);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer', { method: 'PUT', body: JSON.stringify({ content: '附图排查', version: 2, images }) });
    await saveChallengeAnswer('7', '删除图片', 3, true, []);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer/submit', { method: 'POST', body: JSON.stringify({ content: '删除图片', version: 3, images: [] }) });
  });
  it('管理状态与结果纠正独立于财务接口', async () => {
    const state = { action: 'pause', reason: '补充材料', version: 2 };
    await changeChallengeState(3, state);
    expect(request).toHaveBeenLastCalledWith('/3/state', { method: 'PATCH', body: JSON.stringify(state) });
    const result = { action: 'replace', reason: '更换结果', answer_id: 8, version: 3 };
    await overrideChallengeResult(3, result);
    expect(request).toHaveBeenLastCalledWith('/3/result/override', { method: 'POST', body: JSON.stringify(result) });
  });
  it('文档附件随草稿与发布提交，空列表清除附件', async () => {
    const attachments = ['a'.repeat(32)];
    await saveChallengeAnswer('7', '附文档', 2, false, [], attachments);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer', { method: 'PUT', body: JSON.stringify({ content: '附文档', version: 2, images: [], attachments }) });
    await saveChallengeAnswer('7', '删除文档', 3, true, [], []);
    expect(request).toHaveBeenLastCalledWith('/7/my-answer/submit', { method: 'POST', body: JSON.stringify({ content: '删除文档', version: 3, images: [], attachments: [] }) });
  });
  it('文档上传使用带问题标识的FormData接口', async () => {
    const file = new File(['# guide'], '说明.md', { type: 'text/markdown' });
    await uploadChallengeAnswerAttachment('7', file);
    expect(request).toHaveBeenCalledWith('/7/answer-attachments', { method: 'POST', body: expect.any(FormData), timeout: 120000 });
    expect((request.mock.calls[0][1].body as FormData).get('file')).toBe(file);
  });
  it('文档下载经过认证请求且按原始文件名保存', async () => {
    const attachment = { id: 'a'.repeat(32), name: '说明.pdf', size: 8, content_type: 'application/pdf' };
    request.mockResolvedValue(new ArrayBuffer(8));
    const createObjectURL = vi.fn(() => 'blob:attachment');
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.useFakeTimers();
    try {
      await downloadChallengeAnswerAttachment('7', attachment);
      expect(request).toHaveBeenCalledWith(`/7/answer-attachments/${attachment.id}`, { skipCache: true, responseType: 'arrayBuffer' });
      expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
      expect(click.mock.instances[0]).toHaveAttribute('download', attachment.name);
      expect(click.mock.instances[0]).toHaveAttribute('href', 'blob:attachment');
      vi.runAllTimers();
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:attachment');
    } finally { vi.useRealTimers(); vi.unstubAllGlobals(); click.mockRestore(); }
  });
});
