import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const request = createRequest(`${API_CONFIG.ADMIN.BASE_URL}/knowledge/challenges`, '高价值问题');
const fresh = { skipCache: true };

export interface ChallengeAnswer {
  id: number;
  author: string;
  content: string;
  like_count: number;
  submitted_at: string;
  is_mine: boolean;
  is_best: boolean;
  liked_by_me: boolean;
  can_like: boolean;
  can_edit: boolean;
  remaining_edits: number;
}

export interface Challenge {
  id: number;
  title: string;
  bounty_fen: number;
  ends_at: string;
  asked_at: string | null;
  state: string;
  hidden: boolean;
  version: number;
  source: 'standalone' | 'task';
  questioner: string;
  like_count: number;
  liked_by_me: boolean;
  can_like: boolean;
  can_answer: boolean;
  can_manage_state: boolean;
}

export interface ChallengeDetail extends Challenge {
  content: string;
  answers: ChallengeAnswer[];
  best_answer: ChallengeAnswer | null;
  knowledge_source: 'community';
  professionally_verified: boolean;
  my_reward_status: string | null;
  reward_status?: string | null;
  reward_attention?: boolean;
  task_id?: number | null;
  last_error?: string | null;
}

export interface ChallengeList {
  items: Challenge[];
  total: number;
  can_manage: boolean;
}

export interface MyChallengeAnswer {
  content: string;
  version: number;
  submitted_at: string | null;
  remaining_edits: number;
  can_submit: boolean;
}

export interface CreateChallengeBody {
  request_key: string;
  task_id?: number | null;
  title: string;
  content: string;
  bounty_fen: number;
  ends_at: string;
}

export const fetchChallengePreview = () => request<ChallengeList>('/overview', fresh);
export const fetchChallenges = (view: string, page = 1) => request<ChallengeList>(`?view=${encodeURIComponent(view)}&page=${page}&size=20`, fresh);
export const fetchChallenge = (id: string | number) => request<ChallengeDetail>(`/${encodeURIComponent(id)}`, fresh);
export const fetchChallengeSource = (taskId: string) => request<{ title: string; content: string }>(`/source-tasks/${encodeURIComponent(taskId)}`, fresh);
export const createChallenge = (body: CreateChallengeBody) => request<ChallengeDetail>('', { method: 'POST', body: JSON.stringify(body) });
export const publishChallenge = (id: number, version: number) => request<ChallengeDetail>(`/${id}/publish`, { method: 'POST', body: JSON.stringify({ version }) });
export const fetchMyChallengeAnswer = (id: string) => request<MyChallengeAnswer>(`/${encodeURIComponent(id)}/my-answer`, fresh);
export const saveChallengeAnswer = (id: string, content: string, version: number, submit = false) => request<MyChallengeAnswer>(`/${encodeURIComponent(id)}/my-answer${submit ? '/submit' : ''}`, { method: submit ? 'POST' : 'PUT', body: JSON.stringify({ content, version }) });
export const likeChallenge = (challenge: Challenge) => request<ChallengeDetail>(`/${challenge.id}/like`, { method: challenge.liked_by_me ? 'DELETE' : 'PUT' });
export const likeChallengeAnswer = (id: number, answer: ChallengeAnswer) => request<ChallengeDetail>(`/${id}/answers/${answer.id}/like`, { method: answer.liked_by_me ? 'DELETE' : 'PUT' });
export const changeChallengeState = (id: number, body: { action: string; reason: string; version: number; ends_at?: string }) => request<ChallengeDetail>(`/${id}/state`, { method: 'PATCH', body: JSON.stringify(body) });
export const overrideChallengeResult = (id: number, body: { action: string; reason: string; version: number; answer_id?: number }) => request<ChallengeDetail>(`/${id}/result/override`, { method: 'POST', body: JSON.stringify(body) });
