import { createRequest, type RequestOptions } from '@/api/client';
import API_CONFIG from '@/config/api';

export const PERM_LEARNING = 'frontend:admin:learning:manage';
export const QUESTION_TYPES = { single_choice: '单选题', multiple_choice: '多选题', true_false: '判断题' } as const;
export const REVIEW_STATES = { draft: '草稿', pending_review: '待审核', approved: '已审核', rejected: '已驳回' } as const;
export const DIFFICULTIES = { easy: '简单', medium: '中等', hard: '困难' } as const;
export type QuestionType = keyof typeof QUESTION_TYPES;
export type ReviewState = keyof typeof REVIEW_STATES;

export interface KnowledgePoint {
  id: string;
  text: string;
  source_markers: string[];
  source_excerpt: string;
}

export interface ArticleContent {
  title: string;
  category: string;
  tags: string[];
  summary: string;
  applicability: string;
  key_points: KnowledgePoint[];
  method: string;
  cautions: string;
  example: string;
  warnings: string[];
}

export interface SourceTask { id: string; title: string; status: 'closed' | 'archiving' | 'archived'; summary: string }
export interface SourceRecommendations { source: SourceTask; items: SourceTask[]; matching_method: 'text' }
export interface Material { name: string; text: string; provenance: string }
export interface SourceInput { task_ids: (number | string)[]; materials: Material[] }
export interface SourceSnapshot {
  type: 'work_order' | 'manual';
  id?: string;
  marker: string;
  label: string;
  content: string;
  provenance?: string;
}

export interface QuestionContent {
  question_type: QuestionType;
  stem: string;
  options: { key: string; text: string }[];
  correct_answer: string[];
  explanation: string;
  point_ids: string[];
  source_excerpt: string;
  difficulty: keyof typeof DIFFICULTIES;
}

export interface LearningQuestion {
  id: string;
  article_id: string;
  article_version: number;
  payload: QuestionContent;
  review_status: ReviewState;
  enabled: boolean;
  revision: number;
  updated_at: string;
  article_title?: string;
  current_version?: number;
  article_enabled?: boolean;
}

export interface Generation {
  id: string;
  kind: 'article' | 'questions';
  state: 'running' | 'succeeded' | 'failed';
  input_revision: number;
  created_at: string;
  error: string | null;
  result: { content?: ArticleContent; saved_count?: number; errors?: string[]; reason?: string } | null;
}

export interface ArticleSummary {
  id: string;
  title: string;
  category: string;
  active_version: number;
  enabled: boolean;
  has_draft: boolean;
  question_count: number;
  updated_at: string;
}

export interface ArticleDetail {
  id: string;
  title: string;
  category: string;
  active_version: number;
  enabled: boolean;
  revision: number;
  created_at: string;
  draft: ArticleContent | null;
  sources: SourceSnapshot[];
  versions: { version: number; content: ArticleContent; created_at: string }[];
  questions: LearningQuestion[];
  generations: Generation[];
  audits: { action: string; created_at: string; operator_id: string; question_id: string | null; snapshot: Record<string, unknown> }[];
}

export type ReadingArticle = Omit<ArticleContent, 'key_points' | 'warnings'> & { key_points: { text: string }[] };
export interface LearningPreview {
  article: ReadingArticle;
  version: number;
  questions: { id: string; question_type: QuestionType; stem: string; options: { key: string; text: string }[] }[];
}
export interface PreviewResult { is_correct: boolean; correct_answer: string[]; explanation: string; source_excerpt: string }
export interface PageResult<Item> { items: Item[]; total: number }

const request = <Result>(path: string, options: RequestOptions = {}) =>
  createRequest(API_CONFIG.ADMIN.BASE_URL, '知识点与题库')<Result>(`/learning${path}`, { skipCache: true, ...options });
const write = <Result>(path: string, body: unknown, method = 'POST', timeout?: number) =>
  request<Result>(path, { method, body: JSON.stringify(body), timeout });
const query = (keyword: string, state: string, page: number) => new URLSearchParams({ keyword, state, page: String(page) });

export const listSourceTasks = (keyword = '', page = 1) =>
  request<PageResult<SourceTask>>(`/sources?${new URLSearchParams({ keyword, page: String(page) })}`);
export const getSimilarSourceTasks = (id: string) => request<SourceRecommendations>(`/sources/${encodeURIComponent(id)}/similar`);
export const listLearningArticles = (keyword = '', state = '', page = 1) =>
  request<PageResult<ArticleSummary>>(`/articles?${query(keyword, state, page)}`);
export const getLearningArticle = (id: string) => request<ArticleDetail>(`/articles/${id}`);
export const createLearningArticle = (title: string, sources: SourceInput) => write<ArticleDetail>('/articles', { title, sources });
export const saveLearningArticle = (id: string, revision: number, content: ArticleContent, sources?: SourceInput) =>
  write<ArticleDetail>(`/articles/${id}`, { revision, content, ...(sources ? { sources } : {}) }, 'PUT');
export const forkLearningArticle = (id: string, revision: number) => write<ArticleDetail>(`/articles/${id}/fork`, { revision });
export const confirmLearningArticle = (id: string, revision: number) =>
  write<ArticleDetail>(`/articles/${id}/confirm`, { revision, verified: true });
export const toggleLearningArticle = (id: string, revision: number, enabled: boolean) =>
  write<ArticleDetail>(`/articles/${id}/toggle`, { revision, enabled });
export const generateLearning = (id: string, body: {
  request_id: string; revision: number; kind: 'article' | 'questions'; count?: number;
  question_types?: QuestionType[]; difficulty?: keyof typeof DIFFICULTIES;
}) => write<Generation>(`/articles/${id}/generate`, body, 'POST', 180000);
export const getLearningGeneration = (id: string, requestId: string) => request<Generation>(`/articles/${id}/generations/${requestId}`);
export const adoptLearningArticle = (id: string, requestId: string, revision: number) =>
  write<ArticleDetail>(`/articles/${id}/generations/${requestId}/adopt`, { revision });
export const listLearningQuestions = (keyword = '', state = '', page = 1) =>
  request<PageResult<LearningQuestion>>(`/questions?${query(keyword, state, page)}`);
export const saveLearningQuestion = (id: string, revision: number, content: QuestionContent) =>
  write<LearningQuestion>(`/questions/${id}`, { revision, content }, 'PUT');
export const actLearningQuestion = (id: string, revision: number, action: 'submit' | 'approve' | 'reject' | 'enable' | 'disable', reason = '') =>
  write<LearningQuestion>(`/questions/${id}/actions`, { revision, action, reason, verified: action === 'approve' });
export const getLearningPreview = (id: string, availableOnly = false) =>
  request<LearningPreview>(`/articles/${id}/preview?available_only=${availableOnly}`);
export const answerLearningPreview = (id: string, questionId: string, answers: string[]) =>
  write<PreviewResult>(`/articles/${id}/preview/answer`, { question_id: questionId, answers });
