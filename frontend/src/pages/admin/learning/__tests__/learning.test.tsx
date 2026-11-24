import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '@/api/learning';
import { useAuthStore } from '@/stores/auth';
import QuestionGeneration from '../QuestionGeneration';
import ArticleEditor from '../ArticleEditor';
import LearningPreviewModal from '../LearningPreview';
import QuestionList from '../QuestionList';
import QuestionEditor from '../QuestionEditor';
import SourceModal from '../SourceModal';

vi.mock('@/api/learning', async (original) => ({
  ...await original<typeof import('@/api/learning')>(),
  listLearningArticles: vi.fn(), listLearningQuestions: vi.fn(), listSourceTasks: vi.fn(), getSimilarSourceTasks: vi.fn(),
  getLearningArticle: vi.fn(), createLearningArticle: vi.fn(), saveLearningArticle: vi.fn(),
  generateLearning: vi.fn(), getLearningGeneration: vi.fn(), adoptLearningArticle: vi.fn(),
  confirmLearningArticle: vi.fn(), getLearningPreview: vi.fn(), answerLearningPreview: vi.fn(),
  actLearningQuestion: vi.fn(), saveLearningQuestion: vi.fn(),
}));
vi.mock('@/shared/components/MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <p>{content}</p> }));

const content: api.ArticleContent = {
  title: '充电故障排查', category: '设备维护', tags: ['维护'], summary: '先检查供电，再检查触点。',
  applicability: '断电且具备资质时适用。', key_points: [{ id: 'KP1', text: '先检查供电，再检查触点。', source_markers: ['S1'], source_excerpt: '先检查供电，再检查触点。' }],
  method: '按先供电后触点的顺序检查。', cautions: '不得带电操作。', example: '', warnings: [],
};
const question: api.LearningQuestion = {
  id: 'question-1', article_id: 'article-1', article_version: 1, review_status: 'draft', enabled: false, revision: 1, updated_at: '2026-11-20T01:00:00',
  payload: { question_type: 'single_choice', stem: '首先检查什么？', options: [{ key: 'A', text: '供电' }, { key: 'B', text: '触点' }],
    correct_answer: ['A'], explanation: '文章要求先检查供电。', point_ids: ['KP1'], source_excerpt: '先检查供电，再检查触点。', difficulty: 'medium' },
};
const detail = (overrides: Partial<api.ArticleDetail> = {}): api.ArticleDetail => ({
  id: 'article-1', title: content.title, category: content.category, draft: structuredClone(content), sources: [{ type: 'manual', marker: 'S1', label: '维护规范', content: '先检查供电，再检查触点。', provenance: 'v1' }],
  active_version: 0, enabled: false, revision: 1, created_at: '2026-11-20T01:00:00', versions: [], questions: [], generations: [], audits: [], ...overrides,
});
const summary: api.ArticleSummary = { id: 'article-1', title: content.title, category: content.category, has_draft: true, active_version: 0, enabled: false, question_count: 0, updated_at: '2026-11-20T01:00:00' };
const sourceTask: api.SourceTask = { id: '1', title: '充电故障', status: 'archived', summary: '充电桩断电，检查供电后仍无法启动。' };
const relatedTask: api.SourceTask = { id: '2', title: '充电接触不良', status: 'closed', summary: '充电触点松动，设备无法正常充电。' };
const taskSnapshots = (tasks: api.SourceTask[]): api.SourceSnapshot[] => tasks.map((task) => ({ type: 'work_order', id: task.id, marker: `S${task.id}`, label: task.title, content: task.summary }));
const wrap = (element: React.ReactNode) => render(<MemoryRouter>{element}</MemoryRouter>);
const getComputedStyle = window.getComputedStyle;

beforeAll(() => { vi.spyOn(window, 'getComputedStyle').mockImplementation((element) => getComputedStyle(element)); });
afterAll(() => { vi.mocked(window.getComputedStyle).mockRestore(); });

beforeEach(() => {
  Object.values(api).forEach((value) => { if (vi.isMockFunction(value)) value.mockReset(); });
  useAuthStore.setState({ permissions: [api.PERM_LEARNING] });
  vi.mocked(api.listLearningArticles).mockResolvedValue({ items: [summary], total: 1 });
  vi.mocked(api.listLearningQuestions).mockResolvedValue({ items: [question], total: 1 });
  vi.mocked(api.listSourceTasks).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.getSimilarSourceTasks).mockResolvedValue({ source: sourceTask, items: [], matching_method: 'text' });
  vi.mocked(api.getLearningArticle).mockResolvedValue(detail());
});

describe('题目生成管理页', () => {
  it('无权限时拒绝直接访问，且不请求内容', async () => {
    useAuthStore.setState({ permissions: [] });
    wrap(<QuestionGeneration />);
    expect(screen.getByText('暂无题目生成与维护权限')).toBeInTheDocument();
    expect(api.listLearningArticles).not.toHaveBeenCalled();
    expect(api.getLearningArticle).not.toHaveBeenCalled();
  });

  it('加载文章并切换到日常题库维护', async () => {
    wrap(<QuestionGeneration />);
    expect(await screen.findByRole('heading', { name: content.title })).toBeInTheDocument();
    expect(screen.getByText('后台管理 / 其他 / 运营与数据')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '题库维护' }));
    expect(await screen.findByRole('heading', { name: question.payload.stem })).toBeInTheDocument();
    expect(api.listLearningQuestions).toHaveBeenCalledWith('', '', 1);
  });

  it('请求失败可重试，不伪装成空列表', async () => {
    vi.mocked(api.listLearningArticles).mockRejectedValueOnce(new Error('服务暂不可用'));
    wrap(<QuestionGeneration />);
    expect(await screen.findByRole('alert')).toHaveTextContent('服务暂不可用');
    expect(screen.queryByText('还没有知识点，从一组相似工单或技术材料开始')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    expect(await screen.findByRole('heading', { name: content.title })).toBeInTheDocument();
  });

  it('无需来源核验勾选即可仅用技术材料创建，并保留输入直到成功', async () => {
    vi.mocked(api.createLearningArticle).mockRejectedValueOnce(new Error('保存失败')).mockResolvedValueOnce(detail());
    wrap(<QuestionGeneration />);
    await screen.findByRole('heading', { name: content.title });
    fireEvent.click(screen.getByRole('button', { name: '新建知识点' }));
    const dialog = within(screen.getByRole('dialog', { name: '新建知识点 · 选择来源材料' }));
    fireEvent.change(dialog.getByLabelText('知识主题'), { target: { value: '供电排查' } });
    fireEvent.click(dialog.getByRole('button', { name: '添加技术材料' }));
    fireEvent.change(dialog.getByLabelText('材料名称'), { target: { value: '维护规范' } });
    fireEvent.change(dialog.getByLabelText('出处及版本'), { target: { value: '规范v1' } });
    fireEvent.change(dialog.getByLabelText('材料内容'), { target: { value: '先检查供电，再检查触点。' } });
    expect(dialog.queryByRole('checkbox')).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    expect(await screen.findByText('保存失败')).toBeInTheDocument();
    expect(dialog.getByLabelText('知识主题')).toHaveValue('供电排查');
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    await waitFor(() => expect(api.createLearningArticle).toHaveBeenCalledTimes(2));
    expect(api.createLearningArticle).toHaveBeenLastCalledWith('供电排查', { task_ids: [], materials: [{ name: '维护规范', provenance: '规范v1', text: '先检查供电，再检查触点。' }] });
    expect(await screen.findByText('知识点与题目维护')).toBeInTheDocument();
  });

  it('调整来源无需核验勾选，保留已有材料并可直接保存', async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<SourceModal open replacing initialTitle={content.title} initialSources={detail().sources} onClose={vi.fn()} onSubmit={submit} />);
    const dialog = within(screen.getByRole('dialog', { name: '调整知识点来源' }));
    expect(dialog.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(dialog.getByLabelText('材料名称')).toHaveValue('维护规范');
    fireEvent.change(dialog.getByLabelText('出处及版本'), { target: { value: '规范v2' } });
    fireEvent.click(dialog.getByRole('button', { name: '保存来源' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(content.title, {
      task_ids: [], materials: [{ name: '维护规范', provenance: '规范v2', text: '先检查供电，再检查触点。' }],
    }));
  });

  it('移除来源核验后仍要求至少一份来源材料', async () => {
    const submit = vi.fn();
    render(<SourceModal open initialTitle={content.title} onClose={vi.fn()} onSubmit={submit} />);
    const dialog = within(screen.getByRole('dialog', { name: '新建知识点 · 选择来源材料' }));
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    expect(await dialog.findByText('至少选择一个工单或填写一份技术材料')).toBeInTheDocument();
    expect(submit).not.toHaveBeenCalled();
  });
});

describe('来源工单摘要与相似推荐', () => {
  it('选中后展示摘要并查询相似工单，仅手动加入的推荐项进入来源', async () => {
    vi.mocked(api.listSourceTasks).mockResolvedValue({ items: [sourceTask], total: 1 });
    vi.mocked(api.getSimilarSourceTasks).mockResolvedValue({ source: sourceTask, items: [sourceTask, relatedTask], matching_method: 'text' });
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<SourceModal open initialTitle={content.title} onClose={vi.fn()} onSubmit={submit} />);
    const dialog = within(screen.getByRole('dialog'));
    expect(api.getSimilarSourceTasks).not.toHaveBeenCalled();
    fireEvent.mouseDown(dialog.getByRole('combobox', { name: '来源工单' }));
    fireEvent.click(await screen.findByText('1 · 充电故障（已归档）'));
    const selected = within(await dialog.findByRole('article', { name: '已选工单 1' }));
    expect(selected.getByText(sourceTask.summary)).toBeInTheDocument();
    const recommendation = within(await dialog.findByRole('article', { name: '相似工单 2' }));
    expect(recommendation.getByText(relatedTask.summary)).toBeInTheDocument();
    expect(dialog.queryByRole('article', { name: '相似工单 1' })).not.toBeInTheDocument();
    expect(dialog.queryByRole('article', { name: '已选工单 2' })).not.toBeInTheDocument();
    fireEvent.click(recommendation.getByRole('button', { name: '加入来源工单 2' }));
    expect(await dialog.findByRole('article', { name: '已选工单 2' })).toBeInTheDocument();
    expect(dialog.queryByRole('article', { name: '相似工单 2' })).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(content.title, { task_ids: ['1', '2'], materials: [] }));
  });

  it('切换参照工单后忽略旧请求，移除全部工单后清空推荐', async () => {
    const third = { ...sourceTask, id: '3', title: '新的相似工单' };
    const stale = { ...sourceTask, id: '4', title: '旧请求推荐' };
    let resolveOld!: (result: api.SourceRecommendations) => void;
    const oldRequest = new Promise<api.SourceRecommendations>((resolve) => { resolveOld = resolve; });
    vi.mocked(api.getSimilarSourceTasks).mockImplementation((id) => id === '2' ? oldRequest : Promise.resolve({ source: sourceTask, items: [third], matching_method: 'text' }));
    render(<SourceModal open initialTitle={content.title} initialSources={taskSnapshots([sourceTask, relatedTask])} onClose={vi.fn()} onSubmit={vi.fn()} />);
    const dialog = within(screen.getByRole('dialog'));
    await waitFor(() => expect(api.getSimilarSourceTasks).toHaveBeenCalledWith('2'));
    fireEvent.click(dialog.getByRole('button', { name: '查找工单 1 的相似工单' }));
    expect(await dialog.findByRole('article', { name: '相似工单 3' })).toBeInTheDocument();
    await act(async () => { resolveOld({ source: relatedTask, items: [stale], matching_method: 'text' }); });
    expect(dialog.queryByText('旧请求推荐')).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: '移除来源工单 2' }));
    fireEvent.click(dialog.getByRole('button', { name: '移除来源工单 1' }));
    await waitFor(() => expect(dialog.queryByRole('heading', { name: '相似工单推荐' })).not.toBeInTheDocument());
    expect(dialog.queryByRole('article', { name: '相似工单 3' })).not.toBeInTheDocument();
  });

  it('推荐失败可重试，不阻止用已选来源创建草稿', async () => {
    vi.mocked(api.getSimilarSourceTasks).mockRejectedValueOnce(new Error('推荐暂不可用')).mockResolvedValueOnce({ source: sourceTask, items: [relatedTask], matching_method: 'text' });
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<SourceModal open initialTitle={content.title} initialSources={taskSnapshots([sourceTask])} onClose={vi.fn()} onSubmit={submit} />);
    const dialog = within(screen.getByRole('dialog'));
    expect(await dialog.findByText('推荐暂不可用')).toBeInTheDocument();
    expect(dialog.getByText(sourceTask.summary)).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(content.title, { task_ids: ['1'], materials: [] }));
    fireEvent.click(dialog.getByRole('button', { name: '重试相似工单' }));
    expect(await dialog.findByRole('article', { name: '相似工单 2' })).toBeInTheDocument();
  });

  it('来源达到十份时禁用推荐加入，无匹配时显示空状态', async () => {
    const tasks = Array.from({ length: 9 }, (_, index) => ({ ...sourceTask, id: String(index + 1) }));
    vi.mocked(api.getSimilarSourceTasks).mockResolvedValueOnce({ source: tasks[8], items: [{ ...relatedTask, id: '11' }], matching_method: 'text' })
      .mockResolvedValue({ source: tasks[7], items: [], matching_method: 'text' });
    render(<SourceModal open initialTitle={content.title} initialSources={[...taskSnapshots(tasks), detail().sources[0]]} onClose={vi.fn()} onSubmit={vi.fn()} />);
    const dialog = within(screen.getByRole('dialog'));
    expect(await dialog.findByRole('button', { name: '加入来源工单 11' })).toBeDisabled();
    fireEvent.click(dialog.getByRole('button', { name: '移除' }));
    expect(dialog.getByRole('button', { name: '加入来源工单 11' })).toBeEnabled();
    fireEvent.click(dialog.getByRole('button', { name: '移除来源工单 9' }));
    expect(await dialog.findByText('暂无相似的可用工单，可继续搜索或添加技术材料。')).toBeInTheDocument();
  });
});

describe('文章编辑与候选采用', () => {
  const editor = () => wrap(<ArticleEditor articleId="article-1" refreshKey={0} onClose={vi.fn()} onChanged={vi.fn()} onQuestionEdit={vi.fn()} onPreview={vi.fn()} />);

  it('AI生成只展示候选，不自动覆盖，需手动采用', async () => {
    const job: api.Generation = { id: 'request-1', kind: 'article', state: 'succeeded', input_revision: 1, created_at: '2026-11-20T01:00:00', error: null, result: { content: { ...content, title: 'AI候选标题' } } };
    vi.mocked(api.generateLearning).mockResolvedValue(job);
    vi.mocked(api.getLearningArticle).mockResolvedValueOnce(detail()).mockResolvedValue(detail({ generations: [job] }));
    vi.mocked(api.adoptLearningArticle).mockResolvedValue(detail({ revision: 2, draft: job.result!.content! }));
    editor();
    await screen.findByLabelText('文章标题');
    fireEvent.click(screen.getByRole('button', { name: 'AI提炼文章' }));
    fireEvent.click(await screen.findByRole('button', { name: '查看候选内容' }));
    expect(api.adoptLearningArticle).not.toHaveBeenCalled();
    expect(screen.getByLabelText('文章标题')).toHaveValue(content.title);
    fireEvent.click(screen.getByRole('button', { name: '采用到编辑草稿' }));
    await waitFor(() => expect(api.adoptLearningArticle).toHaveBeenCalledWith('article-1', 'request-1', 1));
    await waitFor(() => expect(screen.getByLabelText('文章标题')).toHaveValue('AI候选标题'));
  });

  it('并发保存冲突保留未保存内容', async () => {
    vi.mocked(api.saveLearningArticle).mockRejectedValue(new Error('文章已变化，请刷新后重试'));
    editor();
    fireEvent.change(await screen.findByLabelText('文章标题'), { target: { value: '尚未保存的修改' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿' }));
    expect(await screen.findByText('文章已变化，请刷新后重试')).toBeInTheDocument();
    expect(screen.getByLabelText('文章标题')).toHaveValue('尚未保存的修改');
  });

  it('未完成的草稿可以先保存主题再生成，不强迫手动填完文章', async () => {
    const draft = { ...content, summary: '', applicability: '', key_points: [], method: '', cautions: '' };
    vi.mocked(api.getLearningArticle).mockResolvedValue(detail({ draft }));
    vi.mocked(api.saveLearningArticle).mockResolvedValue(detail({ revision: 2, draft: { ...draft, title: '新主题' } }));
    vi.mocked(api.generateLearning).mockResolvedValue({ id: 'request-new', kind: 'article', state: 'failed', input_revision: 2, created_at: '2026-11-20T01:00:00', error: '模拟生成失败', result: null });
    editor();
    fireEvent.change(await screen.findByLabelText('文章标题'), { target: { value: '新主题' } });
    fireEvent.click(screen.getByRole('button', { name: 'AI提炼文章' }));
    await waitFor(() => expect(api.generateLearning).toHaveBeenCalledWith('article-1', expect.objectContaining({ revision: 2, kind: 'article' })));
    expect(api.saveLearningArticle).toHaveBeenCalledWith('article-1', 1, expect.objectContaining({ title: '新主题', summary: '' }), undefined);
  });

  it('确认文章必须先人工核验', async () => {
    vi.mocked(api.confirmLearningArticle).mockResolvedValue(detail({ draft: null, active_version: 1, enabled: true, revision: 2, versions: [{ version: 1, content, created_at: '2026-11-20T01:00:00' }] }));
    editor();
    await screen.findByLabelText('文章标题');
    fireEvent.click(screen.getByRole('button', { name: '确认文章版本' }));
    expect(screen.getByRole('button', { name: '确认并生成不可变版本' })).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', { name: '已核验知识准确性、来源证据、适用边界及完整脱敏结果' }));
    fireEvent.click(screen.getByRole('button', { name: '确认并生成不可变版本' }));
    await waitFor(() => expect(api.confirmLearningArticle).toHaveBeenCalledWith('article-1', 1));
  });
});

describe('题目审核及开卷预览', () => {
  it('编辑被驳回题目时显示原因，并保存为待重新审核的草稿', async () => {
    vi.mocked(api.getLearningArticle).mockResolvedValue(detail({ active_version: 1, enabled: true,
      versions: [{ version: 1, content, created_at: '2026-11-20T01:00:00' }],
      audits: [{ action: 'question.reject', question_id: question.id, operator_id: 'manager', created_at: '2026-11-20T01:00:00', snapshot: { reason: '题干需要说明排查前提' } }],
    }));
    vi.mocked(api.saveLearningQuestion).mockResolvedValue({ ...question, revision: 2 });
    const saved = vi.fn();
    render(<QuestionEditor question={{ ...question, review_status: 'rejected' }} onClose={vi.fn()} onSaved={saved} />);
    expect(await screen.findByText('驳回原因：题干需要说明排查前提')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('题干'), { target: { value: '依据文章，在断电条件下首先检查什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '保存草稿，重新审核' }));
    await waitFor(() => expect(api.saveLearningQuestion).toHaveBeenCalledWith('question-1', 1, expect.objectContaining({ stem: '依据文章，在断电条件下首先检查什么？' })));
    await waitFor(() => expect(saved).toHaveBeenCalled());
  });

  it('审核通过前需要人工核验，默认不直接启用', async () => {
    const pending = { ...question, review_status: 'pending_review' as const };
    vi.mocked(api.actLearningQuestion).mockResolvedValue({ ...pending, review_status: 'approved' });
    render(<QuestionList questions={[pending]} onEdit={vi.fn()} onChanged={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '审核通过' }));
    const dialog = within(screen.getByRole('dialog'));
    expect(dialog.getByRole('button', { name: /确\s*认/ })).toBeDisabled();
    fireEvent.click(dialog.getByRole('checkbox'));
    fireEvent.click(dialog.getByRole('button', { name: /确\s*认/ }));
    await waitFor(() => expect(api.actLearningQuestion).toHaveBeenCalledWith('question-1', 1, 'approve', ''));
  });

  it('文章先于问题展示，首次不显示答案，模拟提交后显示反馈', async () => {
    vi.mocked(api.getLearningPreview).mockResolvedValue({ article: content, version: 1, questions: [{ id: question.id, question_type: 'single_choice', stem: question.payload.stem, options: question.payload.options }] });
    vi.mocked(api.answerLearningPreview).mockResolvedValue({ is_correct: true, correct_answer: ['A'], explanation: question.payload.explanation, source_excerpt: question.payload.source_excerpt });
    render(<LearningPreviewModal articleId="article-1" onClose={vi.fn()} />);
    const article = await screen.findByRole('heading', { name: content.title });
    const stem = screen.getByText(/首先检查什么/);
    expect(article.compareDocumentPosition(stem) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText(/正确答案/)).not.toBeInTheDocument();
    expect(screen.queryByText('维护规范')).not.toBeInTheDocument();
    expect(screen.queryByText(/来源依据/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'A. 供电' }));
    fireEvent.click(screen.getByRole('button', { name: '模拟提交' }));
    expect(await screen.findByText('回答正确')).toBeInTheDocument();
    expect(api.answerLearningPreview).toHaveBeenCalledWith('article-1', 'question-1', ['A']);
    expect(screen.getByRole('heading', { name: content.title })).toBeInTheDocument();
  });

  it('可选仅查看当前已审核启用的题目', async () => {
    vi.mocked(api.getLearningPreview).mockResolvedValue({ article: content, version: 1, questions: [] });
    render(<LearningPreviewModal articleId="article-1" onClose={vi.fn()} />);
    await screen.findByRole('heading', { name: content.title });
    fireEvent.click(screen.getByRole('checkbox', { name: '仅预览已审核且启用的题目' }));
    await waitFor(() => expect(api.getLearningPreview).toHaveBeenLastCalledWith('article-1', true));
    await act(async () => {});
  });
});
