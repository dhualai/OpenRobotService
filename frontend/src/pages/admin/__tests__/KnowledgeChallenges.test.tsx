import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChallengeAnswer, ChallengeAttachment, ChallengeDetail } from '@/api/knowledgeChallenges';

const api = vi.hoisted(() => ({
  fetchChallengePreview: vi.fn(), fetchChallenges: vi.fn(), createChallenge: vi.fn(),
  fetchChallengeSource: vi.fn(), publishChallenge: vi.fn(), fetchChallenge: vi.fn(),
  fetchMyChallengeAnswer: vi.fn(), saveChallengeAnswer: vi.fn(), changeChallengeState: vi.fn(),
  overrideChallengeResult: vi.fn(), likeChallenge: vi.fn(), likeChallengeAnswer: vi.fn(),
  uploadChallengeAnswerImage: vi.fn(), uploadChallengeAnswerAttachment: vi.fn(), downloadChallengeAnswerAttachment: vi.fn(),
}));
vi.mock('@/api/knowledgeChallenges', () => api);
vi.mock('@/shared/components/UserAvatarMenu', () => ({ default: () => null }));
vi.mock('@/shared/components/ImageLightbox', () => ({ default: ({ open, src, onClose }: { open: boolean; src?: string; onClose: () => void }) => open ? <div role="dialog" aria-label="图片预览"><img src={src} alt="预览大图" /><button type="button" onClick={onClose}>关闭预览</button></div> : null }));
vi.mock('tdesign-mobile-react', () => ({ Navbar: ({ title }: { title: string }) => <div>{title}</div> }));

import HighValueQuestionCard, { ChallengeSummary } from '../HighValueQuestionCard';
import ChallengeAnswerAttachments from '../ChallengeAnswerAttachments';
import KnowledgeChallenges, { ChallengePublishForm, parseBountyFen } from '../KnowledgeChallenges';
import KnowledgeChallengeDetail, { ChallengeAnswerEditor, ChallengeManagement } from '../KnowledgeChallengeDetail';

function challenge(id = 1): ChallengeDetail {
  return { id, title: `高价值问题${id}`, content: '请协助排查', bounty_fen: 10000,
    ends_at: new Date(Date.now() + 86400000).toISOString(), asked_at: new Date().toISOString(),
    state: 'active', hidden: false, version: 2, source: 'standalone', questioner: '平台发布',
    like_count: 2, liked_by_me: false, can_like: true, can_answer: true, can_manage_state: false,
    answers: [], best_answer: null, knowledge_source: 'community', professionally_verified: false, my_reward_status: null };
}

function wrapped(element: React.ReactNode) { return render(<MemoryRouter>{element}</MemoryRouter>); }

function documentAttachment(name = '排查说明.pdf', index = 1): ChallengeAttachment {
  return { id: index.toString(16).padStart(32, '0'), name, size: 5120, content_type: 'application/pdf' };
}

function publishedAnswer(overrides: Partial<ChallengeAnswer> = {}): ChallengeAnswer {
  return { id: 9, author: '张三', content: '已发布的解答', like_count: 2,
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
  it('零元悬赏可以正常发布', async () => {
    api.createChallenge.mockResolvedValue({ ...challenge(), bounty_fen: 0, state: 'draft', version: 1 });
    api.publishChallenge.mockResolvedValue({ ...challenge(), bounty_fen: 0 });
    const done = vi.fn();
    wrapped(<ChallengePublishForm onDone={done} onCancel={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('问题标题'), { target: { value: '零元悬赏问题' } });
    fireEvent.change(screen.getByLabelText('问题内容'), { target: { value: '请提供排查思路' } });
    fireEvent.change(screen.getByLabelText('悬赏奖金（元）'), { target: { value: '0' } });
    fireEvent.click(screen.getByText('确认发布'));
    await waitFor(() => expect(done).toHaveBeenCalledWith(1));
    expect(api.createChallenge).toHaveBeenCalledWith(expect.objectContaining({ bounty_fen: 0 }));
  });
  it.each([true, false])('详情页按服务端授权显示奖金下方的发布者名称：%s', async (allowed) => {
    api.fetchChallenge.mockResolvedValue({ ...challenge(), ...(allowed ? { publisher_username: '张三' } : {}) });
    detailPage();
    await screen.findByRole('heading', { name: '高价值问题1' });
    if (allowed) {
      const publisher = screen.getByText('发布者：张三');
      expect(publisher).toBeVisible();
      expect(publisher.closest('.challenge-prize')).toHaveTextContent('最佳解答悬赏');
    } else expect(screen.queryByText(/发布者：/)).toBeNull();
  });
  it.each([true, false])('列表摘要按服务端授权显示奖金下方的发布者名称：%s', (allowed) => {
    wrapped(<ChallengeSummary challenge={{ ...challenge(), ...(allowed ? { publisher_username: '张三' } : {}) }} />);
    if (allowed) expect(screen.getByText('发布者：张三').closest('.knowledge-ranking-prize')).toHaveTextContent('悬赏奖金');
    else expect(screen.queryByText(/发布者：/)).toBeNull();
  });
  it.each([null, ''])('发布者名称为空时不显示账号或微信ID：%s', async (name) => {
    const detail = { ...challenge(), publisher_username: name };
    api.fetchChallenge.mockResolvedValue(detail);
    detailPage();
    await screen.findByRole('heading', { name: '高价值问题1' });
    expect(screen.queryByText(/发布者：/)).toBeNull();
    wrapped(<ChallengeSummary challenge={detail} />);
    expect(screen.queryByText(/发布者：/)).toBeNull();
  });
  it('文档上传完成后随答案发布，上传中禁止保存和提交', async () => {
    const attachment = documentAttachment();
    let resolveUpload!: (value: ChallengeAttachment) => void;
    api.uploadChallengeAnswerAttachment.mockReturnValue(new Promise<ChallengeAttachment>((resolve) => { resolveUpload = resolve; }));
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', attachments: [attachment], version: 1, submitted_at: new Date().toISOString(), remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    const input = await screen.findByLabelText('上传解答文档');
    const file = new File(['%PDF-1.7'], attachment.name, { type: attachment.content_type });
    fireEvent.change(input, { target: { files: [file] } });
    expect(api.uploadChallengeAnswerAttachment).toHaveBeenCalledWith('1', file);
    expect(screen.getByRole('button', { name: '发布答案' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
    expect(screen.getByLabelText('上传解答图片')).toBeDisabled();
    await act(async () => { resolveUpload(attachment); });
    expect(await screen.findByRole('button', { name: `下载附件 ${attachment.name}` })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '发布答案' }));
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', 0, true, [], [attachment.id]));
  });
  it('草稿附件回填并可删除，附件变化触发自动保存', async () => {
    const attachment = documentAttachment();
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', attachments: [attachment], version: 3, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', attachments: [], version: 4, submitted_at: null, remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: `删除附件 ${attachment.name}` }));
    expect(screen.queryByRole('button', { name: `下载附件 ${attachment.name}` })).toBeNull();
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', 3, false, [], []), { timeout: 2500 });
  });
  it('文档数量不限，一次可以上传超过九个', async () => {
    const attachments = Array.from({ length: 12 }, (_, index) => documentAttachment(`说明${index}.md`, index + 1));
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.uploadChallengeAnswerAttachment.mockImplementation(async (_id: string, file: File) => attachments.find((attachment) => attachment.name === file.name));
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', attachments, version: 1, submitted_at: new Date().toISOString(), remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText('上传解答文档'), { target: { files: attachments.map((attachment) => new File(['# guide'], attachment.name)) } });
    await screen.findByText(/已上传12个文档，数量不限/);
    expect(api.uploadChallengeAnswerAttachment).toHaveBeenCalledTimes(12);
    fireEvent.click(screen.getByRole('button', { name: '发布答案' }));
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', expect.any(Number), true, [], attachments.map((attachment) => attachment.id)));
  });
  it('文档上传失败保留文字及成功附件，允许重试', async () => {
    const attachment = documentAttachment();
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '不能丢失的文字', attachments: [attachment], version: 1, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.uploadChallengeAnswerAttachment.mockRejectedValue(new Error('文档上传失败'));
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText('上传解答文档'), { target: { files: [new File(['# guide'], '新文件.md')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('文档上传失败');
    expect(screen.getByLabelText('答案内容')).toHaveValue('不能丢失的文字');
    expect(screen.getByRole('button', { name: `下载附件 ${attachment.name}` })).toBeVisible();
    expect(screen.getByLabelText('上传解答文档')).toBeEnabled();
  });
  it('不支持的文件和超过20MB的文档在上传前拦截', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    const input = await screen.findByLabelText('上传解答文档');
    fireEvent.change(input, { target: { files: [new File(['run'], 'run.exe')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('仅支持');
    const large = new File(['guide'], '大文档.pdf');
    Object.defineProperty(large, 'size', { value: 20 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [large] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('单个文档不能超过20MB');
    expect(api.uploadChallengeAnswerAttachment).not.toHaveBeenCalled();
  });
  it('已发布及最佳答案展示可下载附件', async () => {
    const attachment = documentAttachment();
    const answer = publishedAnswer({ attachments: [attachment], is_best: true });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [answer], best_answer: answer });
    api.downloadChallengeAnswerAttachment.mockResolvedValue(undefined);
    detailPage();
    fireEvent.click(await screen.findByRole('button', { name: `下载附件 ${attachment.name}` }));
    await waitFor(() => expect(api.downloadChallengeAnswerAttachment).toHaveBeenCalledWith('1', attachment));
    expect(screen.queryByRole('button', { name: `删除附件 ${attachment.name}` })).toBeNull();
  });
  it('附件下载失败显示错误且可重试', async () => {
    const attachment = documentAttachment();
    api.downloadChallengeAnswerAttachment.mockRejectedValue(new Error('附件暂时无法下载'));
    wrapped(<ChallengeAnswerAttachments challengeId="1" attachments={[attachment]} />);
    fireEvent.click(screen.getByRole('button', { name: `下载附件 ${attachment.name}` }));
    expect(await screen.findByRole('alert')).toHaveTextContent('附件暂时无法下载');
    expect(screen.getByRole('button', { name: `下载附件 ${attachment.name}` })).toBeEnabled();
  });
  it('修改机会用完后不可上传或删除文档', async () => {
    const attachment = documentAttachment();
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '已发布答案', attachments: [attachment], version: 2, submitted_at: new Date().toISOString(), remaining_edits: 0, can_submit: false });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    expect(await screen.findByLabelText('上传解答文档')).toBeDisabled();
    expect(screen.getByRole('button', { name: `删除附件 ${attachment.name}` })).toBeDisabled();
  });
  it.each([false, true])('解答列表及最佳解答显示回答用户名，最佳状态：%s', async (isBest) => {
    const answer = publishedAnswer({ is_mine: false, is_best: isBest, can_edit: false });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [answer], best_answer: isBest ? answer : null });
    detailPage();
    const card = await screen.findByRole('article');
    expect(card).toHaveTextContent(isBest ? '张三 · 最佳解答' : '张三');
    expect(screen.getByRole('heading', { name: `${isBest ? '社区优选解答' : '解答'} · 1` })).toBeVisible();
    expect(screen.queryByText(/匿名解答/)).toBeNull();
  });
  it.each([false, true])('解答及最佳解答保留普通链接和Markdown链接并支持安全打开：%s', async (isBest) => {
    const manual = 'https://docs.example.com/robot/manual.pdf?version=2&lang=zh';
    const guide = 'https://docs.example.com/guide';
    const answer = publishedAnswer({ is_mine: false, is_best: isBest, content: `手册 ${manual}\n参考[操作指南](${guide})` });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [answer], best_answer: isBest ? answer : null });
    detailPage();
    const guideLink = await screen.findByRole('link', { name: '操作指南' });
    expect(guideLink).toHaveAttribute('href', guide);
    const manualLink = screen.getByRole('link', { name: manual });
    expect(manualLink).toHaveAttribute('href', manual);
    for (const link of [guideLink, manualLink]) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
  });
  it('回答中的HTML、危险协议和外部图片不会执行或自动加载', async () => {
    const answer = publishedAnswer({ is_mine: false, content: '[危险](javascript:alert(1)) [内联](data:text/html,test) [带凭据](https://test:secret@docs.example.com) <script>alert(1)</script> ![外部图片](https://docs.example.com/track.png)' });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [answer] });
    const view = detailPage();
    await screen.findByText('危险');
    expect(screen.queryByRole('link', { name: '危险' })).toBeNull();
    expect(screen.queryByRole('link', { name: '内联' })).toBeNull();
    expect(screen.queryByRole('link', { name: '带凭据' })).toBeNull();
    expect(view.container.querySelector('script')).toBeNull();
    expect(view.container.querySelector('img[src="https://docs.example.com/track.png"]')).toBeNull();
  });
  it('发布答案前提示展示用户名，不再提示匿名展示', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    await screen.findByLabelText('答案内容');
    expect(screen.getByText(/回答将展示你的用户名/)).toBeVisible();
    expect(screen.queryByText(/匿名展示/)).toBeNull();
  });
  it.each(['高价值问题1', '按问题点赞排序 · 到期按答案有效赞选出最佳解答'])('首页最多展示三题及奖金，点击%s进入完整榜单', async (content) => {
    api.fetchChallengePreview.mockResolvedValue({ items: [1, 2, 3, 4].map(challenge), total: 4, can_manage: false });
    const rendered = render(<MemoryRouter><Routes>
      <Route path="/" element={<HighValueQuestionCard />} />
      <Route path="/admin/knowledge/challenges" element={<p>高价值问题完整榜单</p>} />
    </Routes></MemoryRouter>);
    await screen.findByText('高价值问题1');
    expect(screen.queryByText('高价值问题4')).toBeNull();
    expect(screen.getAllByText('¥100')).toHaveLength(3);
    const card = screen.getByRole('region', { name: '高价值问题榜单' });
    expect(card).toHaveClass('mac-card', 'mac-card--pad', 'knowledge-clickable-card');
    expect(within(card).getAllByRole('link')).toHaveLength(1);
    expect(within(card).getByRole('link', { name: '查看高价值问题榜单' })).toHaveAttribute('href', '/admin/knowledge/challenges');
    expect(screen.queryByText(/查看全部/)).toBeNull();
    expect(rendered.container.querySelector('a a')).toBeNull();
    fireEvent.click(screen.getByText(content));
    expect(await screen.findByText('高价值问题完整榜单')).toBeVisible();
  });
  it('首页空态仍可整卡进入完整榜单', async () => {
    api.fetchChallengePreview.mockResolvedValue({ items: [], total: 0, can_manage: false });
    render(<MemoryRouter><Routes>
      <Route path="/" element={<HighValueQuestionCard />} />
      <Route path="/admin/knowledge/challenges" element={<p>高价值问题完整榜单</p>} />
    </Routes></MemoryRouter>);
    fireEvent.click(await screen.findByText('暂无进行中的高价值问题'));
    expect(await screen.findByText('高价值问题完整榜单')).toBeVisible();
  });
  it('首页加载失败时重试独立生效，不跳转榜单', async () => {
    api.fetchChallengePreview.mockRejectedValueOnce(new Error('暂时无法加载'))
      .mockResolvedValueOnce({ items: [challenge()], total: 1, can_manage: false });
    render(<MemoryRouter><Routes>
      <Route path="/" element={<HighValueQuestionCard />} />
      <Route path="/admin/knowledge/challenges" element={<p>高价值问题完整榜单</p>} />
    </Routes></MemoryRouter>);
    await screen.findByRole('alert');
    const retry = screen.getByRole('button', { name: '重新加载' });
    expect(retry.closest('a')).toBeNull();
    fireEvent.click(retry);
    expect(await screen.findByText('高价值问题1')).toBeVisible();
    expect(api.fetchChallengePreview).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('高价值问题完整榜单')).toBeNull();
  });
  it.each(['高价值问题1', '已暂停'])('列表去掉查看问题与解答，点击卡片%s进入详情', async (content) => {
    api.fetchChallenges.mockResolvedValue({ items: [{ ...challenge(), state: 'paused' }], total: 1, can_manage: false });
    const rendered = render(<MemoryRouter><Routes>
      <Route path="/" element={<KnowledgeChallenges />} />
      <Route path="/admin/knowledge/challenges/:challengeId" element={<p>问题解答详情</p>} />
    </Routes></MemoryRouter>);
    await screen.findByText('高价值问题1');
    expect(screen.queryByText(/查看问题与解答/)).toBeNull();
    expect(screen.getAllByRole('link')).toHaveLength(1);
    expect(screen.getByRole('link')).toHaveAttribute('href', '/admin/knowledge/challenges/1');
    expect(screen.getByRole('link')).toHaveClass('knowledge-clickable-card__link');
    expect(rendered.container.querySelector('a a')).toBeNull();
    fireEvent.click(screen.getByText(content));
    expect(await screen.findByText('问题解答详情')).toBeVisible();
  });
  it('列表移除顶部蓝色提示，三个分类在同一横向切换组内并重置页码', async () => {
    api.fetchChallenges.mockResolvedValue({ items: [], total: 0, can_manage: false });
    const rendered = render(<MemoryRouter initialEntries={['/admin/knowledge/challenges?page=2']}><KnowledgeChallenges /></MemoryRouter>);
    await waitFor(() => expect(api.fetchChallenges).toHaveBeenCalledWith('active', 2));
    await screen.findByText('暂无符合条件的高价值问题');
    expect(rendered.container.querySelector('.knowledge-note')).toBeNull();
    const navigation = screen.getByRole('group', { name: '高价值问题分类' });
    expect(navigation).toHaveClass('knowledge-tabs', 'challenge-view-switch');
    expect(within(navigation).getAllByRole('button').map((button) => button.textContent)).toEqual(['进行中', '我的参与', '已结束']);
    expect(within(navigation).getByRole('button', { name: '进行中' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(navigation).getByRole('button', { name: '我的参与' }));
    await waitFor(() => expect(api.fetchChallenges).toHaveBeenLastCalledWith('mine', 1));
    expect(within(navigation).getByRole('button', { name: '我的参与' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(navigation).getByRole('button', { name: '已结束' }));
    await waitFor(() => expect(api.fetchChallenges).toHaveBeenLastCalledWith('ended', 1));
    expect(within(navigation).getByRole('button', { name: '已结束' })).toHaveAttribute('aria-pressed', 'true');
  });
  it('管理员管理入口独立于三个用户分类并可进入管理视图', async () => {
    api.fetchChallenges.mockResolvedValue({ items: [], total: 0, can_manage: true });
    wrapped(<KnowledgeChallenges />);
    const management = await screen.findByRole('link', { name: '管理问题' });
    const navigation = screen.getByRole('group', { name: '高价值问题分类' });
    expect(navigation).not.toContainElement(management);
    expect(within(navigation).getAllByRole('button')).toHaveLength(3);
    expect(screen.getByRole('button', { name: '发布问题' })).toBeVisible();
    fireEvent.click(management);
    await waitFor(() => expect(api.fetchChallenges).toHaveBeenLastCalledWith('manage', 1));
    expect(await screen.findByRole('link', { name: '管理问题' })).toHaveAttribute('aria-current', 'page');
  });
  it.each([false, 'true'])('未获服务端明确管理授权时不显示管理或发布入口：%s', async (canManage) => {
    api.fetchChallenges.mockResolvedValue({ items: [], total: 0, can_manage: canManage });
    wrapped(<KnowledgeChallenges />);
    await screen.findByText('暂无符合条件的高价值问题');
    expect(screen.queryByRole('link', { name: '管理问题' })).toBeNull();
    expect(screen.queryByRole('button', { name: '发布问题' })).toBeNull();
    expect(within(screen.getByRole('group', { name: '高价值问题分类' })).getAllByRole('button')).toHaveLength(3);
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
    expect(screen.queryByText(/到期按答案有效点赞/)).toBeNull();
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
  it('仅有私有草稿时保留问题区解答入口，取消编辑不发布', async () => {
    api.fetchChallenge.mockResolvedValue(challenge());
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '尚未发布的私有草稿', version: 1, submitted_at: null, remaining_edits: 1, can_submit: true });
    detailPage();
    fireEvent.click(await screen.findByRole('button', { name: '我来解答' }));
    expect(await screen.findByLabelText('答案内容')).toHaveValue('尚未发布的私有草稿');
    expect(screen.getByRole('button', { name: '收起我的解答' })).toBeVisible();
    expect(screen.queryByRole('article')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    expect(screen.getByRole('button', { name: '我来解答' })).toBeVisible();
    expect(api.saveChallengeAnswer).not.toHaveBeenCalled();
  });
  it('首次发布后隐藏问题区解答按钮，转为本人置顶卡片编辑', async () => {
    const mine = publishedAnswer();
    const other = publishedAnswer({ id: 10, author: '匿名解答者2', content: '其他人的解答', is_mine: false, can_like: true, can_edit: false, remaining_edits: 0 });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [other, mine] })
      .mockResolvedValueOnce({ ...challenge(), answers: [other] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: true });
    detailPage();
    fireEvent.click(await screen.findByRole('button', { name: '我来解答' }));
    fireEvent.change(await screen.findByLabelText('答案内容'), { target: { value: mine.content } });
    fireEvent.click(screen.getByRole('button', { name: '发布答案' }));
    expect(await screen.findByRole('button', { name: '编辑' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '收起我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    expect(screen.getAllByRole('article')[0]).toHaveTextContent(mine.content);
    expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', mine.content, 0, true, [], []);
  });
  it.each(['active', 'completed'])('%s状态下本人解答始终置顶，其余保留最佳解答和原获赞顺序', async (state) => {
    const mine = publishedAnswer({ content: '我的低赞解答', like_count: 0, can_edit: state === 'active' });
    const popular = publishedAnswer({ id: 10, author: '匿名解答者2', content: '其他人的高赞解答', like_count: 20, is_mine: false, can_like: true, can_edit: false });
    const best = publishedAnswer({ id: 11, author: '匿名解答者3', content: '其他人的最佳解答', like_count: 5, is_mine: false, is_best: true, can_like: true, can_edit: false });
    const other = publishedAnswer({ id: 12, author: '匿名解答者4', content: '其他人的低赞解答', like_count: 1, is_mine: false, can_like: true, can_edit: false });
    const detail = { ...challenge(), state, can_answer: state === 'active', answers: [popular, best, other, mine], best_answer: best };
    api.fetchChallenge.mockResolvedValue(detail);
    detailPage();
    const cards = await screen.findAllByRole('article');
    expect(cards).toHaveLength(4);
    expect(cards[0]).toHaveTextContent(mine.content);
    expect(cards[1]).toHaveTextContent(best.content);
    expect(cards[2]).toHaveTextContent(popular.content);
    expect(cards[3]).toHaveTextContent(other.content);
    expect(detail.answers[0]).toBe(popular);
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
  });
  it('草稿自动保存不会自动发布，手动发布沿用最新版本', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockImplementation(async (_id, content, version) => ({ content, version: version + 1, submitted_at: null, remaining_edits: 1, can_submit: true }));
    const submitted = vi.fn();
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={submitted} />);
    fireEvent.change(await screen.findByLabelText('答案内容'), { target: { value: '先检查供电' } });
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '先检查供电', 0, false, [], []), { timeout: 2500 });
    expect(submitted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('发布答案'));
    await waitFor(() => expect(submitted).toHaveBeenCalled());
    expect(api.saveChallengeAnswer).toHaveBeenLastCalledWith('1', '先检查供电', 1, true, [], []);
  });
  it('仅本人解答卡片显示编辑入口，直接带入内容并提示仅可修改一次', async () => {
    const mine = publishedAnswer();
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [mine, publishedAnswer({ id: 10, is_mine: false, can_like: true, can_edit: false, remaining_edits: 0 })] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: true });
    detailPage();
    const editButton = await screen.findByRole('button', { name: '编辑' });
    expect(screen.getAllByRole('button', { name: '编辑' })).toHaveLength(1);
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
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
  it('在本人卡片内提交修改后刷新内容并收起编辑区，不恢复问题区入口', async () => {
    const mine = publishedAnswer();
    const updated = { ...mine, content: '补充验证后的完整解答', can_edit: false, remaining_edits: 0 };
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [updated] })
      .mockResolvedValueOnce({ ...challenge(), answers: [mine] });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: mine.content, version: 1, submitted_at: mine.submitted_at, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: updated.content, version: 2, submitted_at: mine.submitted_at, remaining_edits: 0, can_submit: false });
    detailPage();
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }));
    const editor = await screen.findByLabelText('答案内容');
    expect(editor.closest('article')).toBe(screen.getAllByRole('article')[0]);
    fireEvent.change(editor, { target: { value: updated.content } });
    fireEvent.click(screen.getByRole('button', { name: '提交修改' }));
    expect(await screen.findByText(updated.content, { selector: 'p' })).toBeVisible();
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    expect(screen.getByRole('button', { name: '编辑' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
    expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', updated.content, 1, true, [], []);
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
    expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '补充后的完整解答', 1, true, [], []);
    expect(screen.getByLabelText('答案内容')).toBeDisabled();
    expect(screen.getByRole('note')).toHaveTextContent('本题解答已修改过一次，不能再次编辑');
    expect(screen.getByRole('button', { name: '提交修改' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  });
  it('修改次数用完后仅展示本人解答卡片，编辑禁用且不再提供顶部入口', async () => {
    const mine = publishedAnswer({ can_edit: false, remaining_edits: 0 });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [mine] });
    detailPage();
    expect(await screen.findByRole('button', { name: '编辑' })).toBeDisabled();
    expect(screen.getByRole('article')).toHaveTextContent(mine.content);
    expect(screen.getByRole('article')).toHaveTextContent('本题修改机会已用完');
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    expect(api.fetchMyChallengeAnswer).not.toHaveBeenCalled();
    expect(api.saveChallengeAnswer).not.toHaveBeenCalled();
  });
  it('问题截止后即使仍有修改次数也不能编辑', async () => {
    const mine = publishedAnswer({ can_edit: false });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), state: 'completed', can_answer: false, ends_at: new Date(Date.now() - 1000).toISOString(), answers: [mine] });
    detailPage();
    expect(await screen.findByRole('button', { name: '编辑' })).toBeDisabled();
    expect(screen.getByRole('article')).toHaveTextContent(mine.content);
    expect(screen.queryByRole('button', { name: '查看/编辑我的解答' })).toBeNull();
    expect(screen.queryByRole('button', { name: '我来解答' })).toBeNull();
    expect(screen.queryByLabelText('答案内容')).toBeNull();
    expect(api.fetchMyChallengeAnswer).not.toHaveBeenCalled();
    expect(api.saveChallengeAnswer).not.toHaveBeenCalled();
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
  it('金额解析支持零元，拒绝超精度、负数和非数值', () => {
    expect(parseBountyFen('0')).toBe(0);
    expect(parseBountyFen('0.00')).toBe(0);
    expect(parseBountyFen('0.01')).toBe(1);
    expect(parseBountyFen('10.20')).toBe(1020);
    for (const value of ['-1', '1.001', 'NaN', '', '21474836.48']) expect(() => parseBountyFen(value)).toThrow();
  });
  it('上传图片预览后随答案发布，上传中禁止提前提交', async () => {
    const url = '/api/tasks/files/helpdesk-comment/spec-doc/images/' + 'a'.repeat(32) + '.png';
    let resolveUpload!: (value: string) => void;
    api.uploadChallengeAnswerImage.mockReturnValue(new Promise<string>((resolve) => { resolveUpload = resolve; }));
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', images: [url], version: 1, submitted_at: new Date().toISOString(), remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    const input = await screen.findByLabelText('上传解答图片');
    const file = new File(['image'], '现场截图.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });
    expect(api.uploadChallengeAnswerImage).toHaveBeenCalledWith(file);
    expect(screen.getByRole('button', { name: '发布答案' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
    await act(async () => { resolveUpload(url); });
    expect(await screen.findByAltText('解答图片1')).toHaveAttribute('src', url);
    await waitFor(() => expect(screen.getByRole('button', { name: '发布答案' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '预览解答图片1' }));
    expect(screen.getByRole('dialog', { name: '图片预览' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '关闭预览' }));
    fireEvent.click(screen.getByRole('button', { name: '发布答案' }));
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', 0, true, [url], []));
  });
  it('草稿图片回填后可删除，删除也触发自动保存', async () => {
    const url = '/api/tasks/files/helpdesk-comment/spec-doc/images/' + 'a'.repeat(32) + '.png';
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', images: [url], version: 3, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', images: [], version: 4, submitted_at: null, remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    await screen.findByAltText('解答图片1');
    fireEvent.click(screen.getByRole('button', { name: '删除解答图片1' }));
    expect(screen.queryByAltText('解答图片1')).toBeNull();
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', 3, false, [], []), { timeout: 2500 });
  });
  it('图片上传失败保留答案文字，允许重试且不添加失败图片', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '不能丢失的文字', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.uploadChallengeAnswerImage.mockRejectedValue(new Error('图片上传失败'));
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    fireEvent.change(await screen.findByLabelText('上传解答图片'), { target: { files: [new File(['image'], '截图.png', { type: 'image/png' })] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('图片上传失败');
    expect(screen.getByLabelText('答案内容')).toHaveValue('不能丢失的文字');
    expect(screen.queryByAltText('解答图片1')).toBeNull();
    expect(screen.getByLabelText('上传解答图片')).toBeEnabled();
  });
  it('非图片和超过10MB的图片在上传前拦截', async () => {
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', version: 0, submitted_at: null, remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    const input = await screen.findByLabelText('上传解答图片');
    fireEvent.change(input, { target: { files: [new File(['file'], '附件.txt', { type: 'text/plain' })] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('仅支持');
    const oversized = new File(['image'], '过大图片.png', { type: 'image/png' });
    Object.defineProperty(oversized, 'size', { value: 10 * 1024 * 1024 + 1 });
    fireEvent.change(input, { target: { files: [oversized] } });
    expect(screen.getByRole('alert')).toHaveTextContent('单张图片不能超过10MB');
    expect(api.uploadChallengeAnswerImage).not.toHaveBeenCalled();
  });
  it('已有9张图片时仍可一次追加10张并完整发布', async () => {
    const images = Array.from({ length: 19 }, (_, index) => `/api/tasks/files/helpdesk-comment/spec-doc/images/${String(index).padStart(32, '0')}.png`);
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: '排查步骤', images: images.slice(0, 9), version: 1, submitted_at: null, remaining_edits: 1, can_submit: true });
    api.uploadChallengeAnswerImage.mockImplementation(async (file: File) => `/api/tasks/files/helpdesk-comment/spec-doc/images/${file.name.replace('.png', '').padStart(32, '0')}.png`);
    api.saveChallengeAnswer.mockResolvedValue({ content: '排查步骤', images, version: 2, submitted_at: new Date().toISOString(), remaining_edits: 1, can_submit: true });
    wrapped(<ChallengeAnswerEditor id="1" canAnswer onSubmitted={vi.fn()} />);
    const input = await screen.findByLabelText('上传解答图片');
    expect(input).toBeEnabled();
    const files = Array.from({ length: 10 }, (_, index) => new File(['image'], `${index + 9}.png`, { type: 'image/png' }));
    fireEvent.change(input, { target: { files } });
    expect(await screen.findByAltText('解答图片19')).toHaveAttribute('src', images[18]);
    await waitFor(() => expect(input).toBeEnabled());
    expect(api.uploadChallengeAnswerImage).toHaveBeenCalledTimes(10);
    expect(screen.getByText(/已上传19张，数量不限/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: '发布答案' }));
    await waitFor(() => expect(api.saveChallengeAnswer).toHaveBeenCalledWith('1', '排查步骤', expect.any(Number), true, images, []));
  });
  it('答案及最佳解答显示图片，修改次数用完后禁止上传和删除', async () => {
    const url = '/api/tasks/files/helpdesk-comment/spec-doc/images/' + 'a'.repeat(32) + '.png';
    const answer = publishedAnswer({ images: [url], is_best: true });
    api.fetchChallenge.mockResolvedValue({ ...challenge(), answers: [answer], best_answer: answer });
    api.fetchMyChallengeAnswer.mockResolvedValue({ content: answer.content, images: [url], version: 2, submitted_at: answer.submitted_at, remaining_edits: 0, can_submit: false });
    detailPage();
    expect(await screen.findByAltText('解答图片1')).toHaveAttribute('src', url);
    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    expect(await screen.findByLabelText('上传解答图片')).toBeDisabled();
    expect(screen.getByRole('button', { name: '删除解答图片1' })).toBeDisabled();
  });
});

/**
 * 转发卡片（ORS-1086）
 * 企微没有 JS-SDK 分享接口，卡片内容只能来自页面 head 的 og 标签；
 * 这里锁死「标题=问题标题、描述含悬赏奖金、缩略图为绝对地址」这条契约。
 */
describe('高价值问题转发卡片（ORS-1086）', () => {
  beforeEach(() => { vi.resetAllMocks(); document.head.innerHTML = ''; });
  afterEach(cleanup);

  it('详情页渲染后按问题生成卡片：标题=问题标题，描述带悬赏奖金与人数，缩略图是绝对地址', async () => {
    const image = '/api/tasks/files/helpdesk-comment/spec-doc/images/' + 'a'.repeat(32) + '.png';
    api.fetchChallenge.mockResolvedValue({ ...challenge(7), answers: [publishedAnswer({ images: [image] })] });
    detailPage();
    await screen.findByRole('heading', { name: '高价值问题7' });
    expect(screen.getByAltText('解答图片1')).toHaveAttribute('src', image);

    const ogTitle = document.head.querySelector('meta[property="og:title"]')?.getAttribute('content');
    const ogDesc = document.head.querySelector('meta[property="og:description"]')?.getAttribute('content');
    const ogImage = document.head.querySelector('meta[property="og:image"]')?.getAttribute('content');

    expect(ogTitle).toBe('高价值问题7');
    expect(ogDesc).toContain('¥100');
    expect(ogDesc).toContain('人也遇到');
    expect(ogImage).toMatch(/^https?:\/\//);
  });
});
