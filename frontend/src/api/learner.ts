// 做题人端学习中心 API — 挂在 /api/tasks/learning/* 下，登录即可访问
import { createRequest, type RequestOptions } from '@/api/client';
import API_CONFIG from '@/config/api';
import type { QuestionContent } from '@/api/learning';

const request = <Result>(path: string, options: RequestOptions = {}) =>
  createRequest(API_CONFIG.TASKS.BASE_URL, '学习中心')<Result>(`/learning${path}`, { skipCache: true, ...options });
const write = <Result>(path: string, body: unknown, method = 'POST') =>
  request<Result>(path, { method, body: JSON.stringify(body) });

// ============ 类型定义 ============

export interface LearnerArticleSummary {
  id: string;
  title: string;
  category: string;
  summary: string;
  active_version: number;
  updated_at: string;
  total_count: number;
  answered_count: number;
  completed: boolean;
}

export interface LearnerAnswer {
  question_id: string;
  answers: string[];
  is_correct: boolean;
  created_at: string;
}

export interface ReadingArticle {
  title: string;
  category: string;
  tags: string[];
  summary: string;
  applicability: string;
  key_points: { text: string }[];
  method: string;
  cautions: string;
  example: string;
}

export interface LearnerQuestionBrief {
  id: string;
  question_type: QuestionContent['question_type'];
  stem: string;
  options: { key: string; text: string }[];
  has_answer: boolean;
}

export interface LearnerArticleDetail {
  article: {
    id: string;
    title: string;
    category: string;
    active_version: number;
    content: ReadingArticle;
  };
  questions: LearnerQuestionBrief[];
  answers: LearnerAnswer[];
}

export interface SubmitAnswerResult {
  is_correct: boolean;
  correct_answer: string[];
  explanation: string;
  source_excerpt: string;
}

export interface LearnerProgress {
  total_articles: number;
  completed_articles: number;
  total_questions: number;
  answered_questions: number;
  correct_questions: number;
}

export interface PageResult<Item> { items: Item[]; total: number }

// ============ 接口函数 ============

/** 获取启用中的文章列表（带每篇的答题进度） */
export const listLearnerArticles = (keyword = '', page = 1) =>
  request<PageResult<LearnerArticleSummary>>(
    `/articles?${new URLSearchParams({ keyword, page: String(page) })}`
  );

/** 获取单篇文章详情（脱敏内容 + 题目列表 + 我的答题记录） */
export const getLearnerArticle = (id: string) =>
  request<LearnerArticleDetail>(`/articles/${id}`);

/** 提交答案，返回对错 + 解析 + 正确答案 */
export const submitLearnerAnswer = (articleId: string, questionId: string, answers: string[]) =>
  write<SubmitAnswerResult>(`/articles/${articleId}/answer`, { question_id: questionId, answers });

/** 获取当前用户整体学习进度统计 */
export const getLearnerProgress = () =>
  request<LearnerProgress>(`/progress`);
