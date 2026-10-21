import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChallengeAnswer, ChallengeDetail } from '@/api/knowledgeChallenges';

const api = vi.hoisted(() => ({
  fetchChallengePreview: vi.fn(), fetchChallenges: vi.fn(), createChallenge: vi.fn(),
  fetchChallengeSource: vi.fn(), publishChallenge: vi.fn(), fetchChallenge: vi.fn(),
  fetchMyChallengeAnswer: vi.fn(), saveChallengeAnswer: vi.fn(), changeChallengeState: vi.fn(),
  overrideChallengeResult: vi.fn(), likeChallenge: vi.fn(), likeChallengeAnswer: vi.fn(),
}));
vi.mock('@/api/knowledgeChallenges', () => api);
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('tdesign-mobile-react', () => ({ Navbar: ({ title }: { title: string }) => <div>{title}</div> }));

import HighValueQuestionCard from '../HighValueQuestionCard';
import { ChallengePublishForm, parseBountyFen } from '../KnowledgeChallenges';
import KnowledgeChallengeDetail, { ChallengeAnswerEditor, ChallengeManagement } from '../KnowledgeChallengeDetail';

function challenge(id = 1): ChallengeDetail {
  return { id, title: `高价值问题${id}`, content: '请协助排查', bounty_fen: 10000,
    ends_at: new Date(Date.now() + 86400000).toISOString(), asked_at: new Date().toISOString(),
    state: 'active', hidden: false, version: 2, source: 'standalone', questioner: '平台发布',
    like_count: 2, liked_by_me: false, can_like: true, can_answer: true, can_manage_state: false,
    answers: [], best_answer: null, knowledge_source: 'community', professionally_verified: false, my_reward_status: null };
}

function wrapped(element: React.ReactNode) { return render(<MemoryRouter>{element}</MemoryRouter>); }

function publishedAnswer(overrides: Partial<ChallengeAnswer> = {}): ChallengeAnswer {
  return { id: 9, author: '匿名解答者1', content: '已发布的解答', like_count: 2,
    submitted_at: new Date().toISOString(), is_mine: true, is_best: false, liked_by_me: false,
    can_like: false, can_edit: true, remaining_edits: 1, ...overrides };
}

function detailPage() {
  return render(<MemoryRouter initialEntries={['/admin/knowledge/challenges/1']}><Routes>
    <Route path="/admin/knowledge/challenges/:challengeId" element={<KnowledgeChallengeDetail />} />
  </Routes></MemoryRouter>);
}

describe('高价值问题交互', () => {
  beforeEach(() => { vi.resetAllMocks(); });
  afterEach(cleanup);
  it('首页最多展示三题及奖金，链接没有嵌套', async () => {
    api.fetchChallengePreview.mockResolvedValue({ items: [1, 2, 3, 4].map(challenge), total: 4, can_manage: false });
    const rendered = wrapped(<HighValueQuestionCard />);
    await screen.findByText('高价值问题1');
    expect(screen.queryByText('高价值问题4')).toBeNull();
    expect(screen.getAllByText('¥100')).toHaveLength(3);
    expect(rendered.container.querySelector('a a')).toBeNull();
  });
  it('普通用户看不到状态按钮，管理员由服务端标记控制', () => {
    const rendered = wrapped(<ChallengeManagement challenge={challenge()} onChanged={vi.fn()} />);
    expect(screen.queryByText('更改状态')).toBeNull();
    rendered.rerender(<MemoryRouter><ChallengeManagement challenge={{ ...challenge(), can_manage_state: true }} onChanged={vi.fn()} /></MemoryRouter>);
    fireEvent.click(screen.getByText('更改状态'));
    expect(screen.getByLabelText('操作原因')).toBeTruthy();
    expect(screen.queryByText('选择审核人')).toBeNull();
  });
  it('无需选工单即可发布，奖金按整数分提交', async () => {
    api.createChallenge.mockResolvedValue({ ...challenge(), state: 'draft', version: 1 });
    api.publishChallenge.mockResolvedValue(challenge());
    const done = vi.fn();
    wrapped(<ChallengePublishForm onDone={done} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('问题标题'), { target: { value: '独立发布的问题' } });
    fireEvent.change(screen.getByLabelText('问题内容'), { target: { value: '没有来源工单的内容' } });
    fireEvent.change(screen.getByLabelText('悬赏奖金（元）'), { target: { value: '100.05' } });
    fireEvent.click(screen.getByText('确认发布'));
    await waitFor(() => expect(done).toHaveBeenCalledWith(1));
    expect(api.createChallenge).toHaveBeenCalledWith(expect.objectContaining({ task_id: null, bounty_fen: 10005 }));
    expect(api.publishChallenge).toHaveBeenCalledWith(1, 1);
    expect(api.fetchChallengeSource).not.toHaveBeenCalled();
  });
  it('问题点赞与解答按钮共用形状样式，展开收起及点赞不受影响', async () => {
    const question = challenge();
    api.fetchChallenge.mockResolvedValue(question);
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.likeChallenge.mockResolvedValue(question);
    render(<MemoryRouter initialEntries={['/admin/knowledge/challenges/1']}><Routes>
      <Route path="/admin/knowledge/challenges/:challengeId" element={<KnowledgeChallengeDetail />} />
    </Routes></MemoryRouter>);
    const likeButton = await screen.findByRole('button', { name: '我也遇到 · 2' });
    expect(likeButton).toHaveClass('knowledge-like', 'challenge-question-action');
    const answerButton = screen.getByRole('button', { name: '我来解答' });
    expect(answerButton).toHaveClass('knowledge-button', 'challenge-question-action');
    fireEvent.click(answerButton);
    expect(await screen.findByLabelText('答案内容')).toBeTruthy();
    expect(screen.getByRole('button', { name: '收起我的解答' })).toHaveClass('challenge-question-action');
    fireEvent.click(screen.getByRole('button', { name: '收起我的解答' }));
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    fireEvent.click(likeButton);
    await waitFor(() => expect(api.likeChallenge).toHaveBeenCalledWith(question));
    await waitFor(() => expect(likeButton).not.toBeDisabled());
  });
  it('草稿自动保存不会自动发布，手动发布沿用最新版本', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockImplementation(async (_id, content, version) => ({ content, version: version + 1, submitted_at: null, remaining_edits: 1, can_submit: true }));
    const submitted = vi.fn();
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={submitted} />);
    fireEvent.change(await screen.findByLabelText('答案内容'), { target: { value: '先检查供电' } });
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '先检查供电', 0, false), { timeout: 2500 });
    expect(submitted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('发布答案'));
    await waitFor(() => expect(submitted).toHaveBeenCalled());
    expect(api.saveChallengeAnswer).toHaveBeenLastCalledWith('1', '先检查供电', 1, true);
  });
  it('仅本人解答卡片显示编辑入口，直接带入内容并提示仅可修改一次', async () => {
    const mine = publishedAnswer();
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [mine, publishedAnswer({ id: 10, is_mine: false, can_like: true, can_edit: false, remaining_edits: 0 })] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: true });
    detailPage();
    const editButton = await screen.findByRole('button', { name: '编辑' });
    expect(screen.getAllByRole('button', { name: '编辑' })).toHaveLength(1);
    const card = editButton.closest('article');
    fireEvent.click(editButton);
    const editor = await screen.findByLabelText('答案内容');
    expect(editor).toHaveValue(mine.content);
    expect(editor.closest('article')).toBe(card);
    expect(screen.getByRole('note')).toHaveTextContent('每个问题的已发布解答仅可修改一次');
    expect(screen.getByRole('button', { name: '提交修改' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    expect(screen.getByRole('button', { name: '编辑' })).toBeEnabled();
    expect(api.saveChallengeAnswer).not.toHaveBeenCalled();
  });
  it('提交唯一一次修改后禁止继续发布或保存草稿', async () => {
    const mine = publishedAnswer();
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: '补充后的完整解答', version: 2, submitted_at: mine.submitted_at, remaining_edits: 0, can_submit: false });
    const submitted = vi.fn();
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={submitted} />);
    fireEvent.change(await screen.findByLabelText('答案内容'), { target: { value: '补充后的完整解答' } });
    fireEvent.click(screen.getByRole('button', { name: '提交修改' }));
    await waitFor(() => expect(submitted).toHaveBeenCalledOnce());
    expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '补充后的完整解答', 1, true);
    expect(screen.getByLabelText('答案内容')).toBeDisabled();
    expect(screen.getByRole('note')).toHaveTextContent('本题解答已修改过一次，不能再次编辑');
    expect(screen.getByRole('button', { name: '提交修改' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  });
  it('修改次数用完后，原解答入口也只能查看内容', async () => {
    const mine = publishedAnswer({ can_edit: false, remaining_edits: 0 });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [mine] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 2, submitted_at: mine.submitted_at, remaining_edits: 0, can_submit: false });
    detailPage();
    expect(await screen.findByRole('button', { name: '编辑' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '查看/编辑我的解答' }));
    expect(await screen.findByLabelText('答案内容')).toBeDisabled();
    expect(screen.getByRole('button', { name: '提交修改' })).toBeDisabled();
    expect(api.saveChallengeAnswer).not.toHaveBeenCalled();
  });
  it('问题截止后即使仍有修改次数也不能编辑', async () => {
    const mine = publishedAnswer({ can_edit: false });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), state: 'completed', can_answer: false, ends_at: new Date(Date.now() - 1000).toISOString(), answers: [mine] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: false });
    detailPage();
    expect(await screen.findByRole('button', { name: '编辑' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '查看/编辑我的解答' }));
    expect(await screen.findByLabelText('答案内容')).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('问题已截止或暂停');
  });
  it('改选最佳答案携带原因、答案及状态版本，不调用财务接口', async () => {
    const done = vi.fn();
    const result = { ...challenge(), state: 'completed', can_manage_state: true,
      answers: [{ id: 9, author: '匿名解答者2', content: '新的方案', like_count: 1, submitted_at: '', is_best: false, is_mine: false, liked_by_me: false, can_like: false, can_edit: false, remaining_edits: 0 }] };
    api.overrideChallengeResult.mockResolvedValue(result);
    wrapped(<ChallengeManagement challenge={result} onChanged={done} />);
    fireEvent.click(screen.getByText('更改状态'));
    fireEvent.change(screen.getByLabelText('操作'), { target: { value: 'replace' } });
    fireEvent.change(screen.getByLabelText('选择最佳回答'), { target: { value: '9' } });
    fireEvent.change(screen.getByLabelText('操作原因'), { target: { value: '原答案不适用' } });
    fireEvent.click(screen.getByText('确认操作'));
    await waitFor(() => expect(done).toHaveBeenCalled());
    expect(api.overrideChallengeResult).toHaveBeenCalledWith(1, { action: 'replace', reason: '原答案不适用', version: 2, answer_id: 9 });
  });
  it('金额解析拒绝超精度、负数和非数值', () => {
    expect(parseBountyFen('0.01')).toBe(1);
    expect(parseBountyFen('10.20')).toBe(1020);
    for (const value of ['-1', '1.001', 'NaN', '0']) expect(() => parseBountyFen(value)).toThrow();
  });
});
