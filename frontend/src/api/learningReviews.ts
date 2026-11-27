import { createRequest, type RequestOptions } from '@/api/client';
import API_CONFIG from '@/config/api';
import type { QuestionContent, ReadingArticle, PageResult } from './learning';

export interface LearningReviewTask {
  id: string; title: string; status: string; assigned_to: string | null; created_at: string;
  outcome: 'approved' | 'rejected' | 'canceled' | null; reason: string; can_review: boolean;
}
export interface LearningReviewDetail extends LearningReviewTask {
  revision: number; stale: boolean; question: QuestionContent | null; article: ReadingArticle | null;
}
const request = <Result>(path: string, options: RequestOptions = {}) =>
  createRequest(API_CONFIG.TASKS.BASE_URL, '学习审核')<Result>(`/learning/reviews${path}`, { skipCache: true, ...options });

export const listLearningReviews = (page = 1) => request<PageResult<LearningReviewTask>>(`?page=${page}`);
export const getLearningReview = (id: string) => request<LearningReviewDetail>(`/${encodeURIComponent(id)}`);
export const decideLearningReview = (id: string, body: {
  revision: number; action: 'approve' | 'reject'; verified: boolean; reason: string;
}) => request<LearningReviewDetail>(`/${encodeURIComponent(id)}/decision`, { method: 'POST', body: JSON.stringify(body) });
