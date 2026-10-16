import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const admin = createRequest(`${API_CONFIG.ADMIN.BASE_URL}/knowledge`, '知识贡献');
const tasks = createRequest(API_CONFIG.TASKS.BASE_URL, '知识审核');
const fresh = { skipCache: true };

export interface KnowledgeUser {
  user_id: string;
  name: string;
  avatar_resource_id: number | null;
}

export interface Contributor extends KnowledgeUser {
  rank: number;
  question_count: number;
  answer_count: number;
  contribution_count: number;
  valid_like_count: number;
  estimated_reward_fen: number;
  tied: boolean;
}

export interface KnowledgeOverview {
  month: string;
  months: string[];
  starts_at: string;
  ends_at: string;
  campaign_status: 'upcoming' | 'active' | 'ended';
  settlement_status: string;
  top_three: Contributor[];
  can_review: boolean;
  reviewer_configured?: boolean;
  pending_review_count?: number;
  rule_notice: string;
}

export interface KnowledgeQuestion {
  task_id: number;
  title: string;
  problem: string;
  content: string;
  review_comment: string;
  value_point: string;
  archived_at: string;
  valid_like_count: number;
  liked_by_me: boolean;
  can_like: boolean;
  questioner: KnowledgeUser;
  answerer: KnowledgeUser;
}

export interface KnowledgeReview {
  report_id: number;
  task_id: number;
  version: number;
  revision: number;
  submit_status: string;
  title: string;
  problem: string;
  content: string;
  review_comment: string;
  value_point: string;
}

export interface KnowledgeDiscussionEntry {
  id: number;
  task_id: number;
  author: KnowledgeUser | null;
  content: string;
  created_at: string;
}

export interface ContributorQuestions {
  user: KnowledgeUser;
  month: string;
  question_count: number;
  answer_count: number;
  items: KnowledgeQuestion[];
  total: number;
}

export interface ReviewHistory {
  id: number;
  report_id: number;
  task_id: number;
  version: number;
  outcome: string;
  created_at: string;
  review_comment: string;
  value_point: string;
  content: string;
}

export interface SimilarQuestion {
  task_id: number;
  title: string;
  score: number;
  report: { content: string; version: number } | null;
}

export const fetchKnowledgeOverview = (month?: string) => admin<KnowledgeOverview>(`/overview${month ? `?month=${encodeURIComponent(month)}` : ''}`, fresh);
export const fetchContributors = (month: string) => admin<{ items: Contributor[] }>(`/rankings?month=${encodeURIComponent(month)}`, fresh);
export const fetchKnowledgeQuestions = (month: string, sort: string, page: number, size = 20) => admin<{ items: KnowledgeQuestion[]; total: number }>(`/questions?sort=${sort}&page=${page}&size=${size}${month ? `&month=${encodeURIComponent(month)}` : ''}`, fresh);
export const fetchKnowledgeQuestion = (taskId: string) => admin<KnowledgeQuestion>(`/questions/${encodeURIComponent(taskId)}`, fresh);
export const fetchContributorQuestions = (userId: string, month: string | undefined, role: string, page: number) => admin<ContributorQuestions>(`/contributors/${encodeURIComponent(userId)}/questions?role=${encodeURIComponent(role)}&page=${page}&size=20${month ? `&month=${encodeURIComponent(month)}` : ''}`, fresh);
export const fetchKnowledgeDiscussions = (taskId: string, page: number) => admin<{ items: KnowledgeDiscussionEntry[]; total: number }>(`/questions/${encodeURIComponent(taskId)}/discussions?page=${page}&size=20`, fresh);
export const createKnowledgeDiscussion = (taskId: string, content: string) => admin<KnowledgeDiscussionEntry>(`/questions/${encodeURIComponent(taskId)}/discussions`, { method: 'POST', body: JSON.stringify({ content }) });
export const toggleKnowledgeLike = (question: KnowledgeQuestion) => admin<KnowledgeQuestion>(`/questions/${question.task_id}/like`, { method: question.liked_by_me ? 'DELETE' : 'PUT' });
export const fetchPendingReviews = () => tasks<{ items: KnowledgeReview[]; total: number }>('/archive-report/reviews', fresh);
export const fetchKnowledgeReview = (reportId: string) => tasks<KnowledgeReview>(`/archive-report/reviews/${encodeURIComponent(reportId)}`, fresh);
export const fetchMyReviewHistory = () => tasks<{ items: ReviewHistory[]; total: number }>('/archive-report/my-review-history', fresh);
export const fetchReviewSimilar = (taskId: number) => tasks<{ similar: SimilarQuestion[]; retrieval_status: 'ok' | 'unavailable' }>(`/${taskId}/similar?limit=8&min_score=0.02&scope=review`, fresh);
export const submitKnowledgeReview = (report: KnowledgeReview, outcome: 'approve' | 'reject', reviewComment: string, valuePoint: string) => tasks(`/archive-report/archive-reports/${report.report_id}/${outcome}`, {
  method: 'POST', body: JSON.stringify({ review_comment: reviewComment, value_point: valuePoint, revision: report.revision }),
});
