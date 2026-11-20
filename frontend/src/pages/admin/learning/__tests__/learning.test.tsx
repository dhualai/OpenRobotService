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

vi.mock('@/api/learning', async (original) => ({
  ...await original<typeof import('@/api/learning')>(),
  listLearningArticles: vi.fn(), listLearningQuestions: vi.fn(), listSourceTasks: vi.fn(),
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

  it('允许仅用技术材料创建，并保留输入直到成功', async () => {
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
    fireEvent.click(dialog.getByRole('checkbox', { name: '已核对材料使用授权和敏感信息；生成结果仍须人工确认' }));
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    expect(await screen.findByText('保存失败')).toBeInTheDocument();
    expect(dialog.getByLabelText('知识主题')).toHaveValue('供电排查');
    fireEvent.click(dialog.getByRole('button', { name: '创建草稿并继续' }));
    await waitFor(() => expect(api.createLearningArticle).toHaveBeenCalledTimes(2));
    expect(api.createLearningArticle).toHaveBeenLastCalledWith('供电排查', { task_ids: [], materials: [{ name: '维护规范', provenance: '规范v1', text: '先检查供电，再检查触点。' }], verified: true });
    expect(await screen.findByText('知识点与题目维护')).toBeInTheDocument();
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
