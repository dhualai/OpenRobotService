/**
 * 讨论区消息转发到微信 —— API
 *
 * - 接收人列表：GET /api/tasks/{id}/comments/forward-targets（自己+同事，标注是否已绑定微信）
 * - 转发单条：POST /api/tasks/{id}/comments/forward-to-wechat（文本 / 链接卡片）
 * - 微信 open_id 绑定：GET/POST/DELETE /api/auth/me/wechat-openid
 *
 * 初版仅支持单条文本/链接转发到自己或他人微信（公众号客服消息）。
 * 合并转发（长图）、图片/文件、企业微信/微信群 留待后续阶段。
 */
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

const tasksRequest = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');
const authRequest = createRequest(API_CONFIG.AUTH.BASE_URL, '认证服务');

export interface ForwardTarget {
  id: string;
  username: string;
  name?: string | null;
  is_self: boolean;
  wechat_bound: boolean;
}

export interface ForwardResult {
  username: string;
  name: string;
  status: 'delivered' | 'failed' | 'skipped';
  reason?: string;
  error?: string | null;
}

export interface ForwardResponse {
  code: number;
  message: string;
  results: ForwardResult[];
}

/** 拉取转发接收人列表（自己置顶，标注是否已绑定微信 open_id） */
export const getForwardTargets = (taskId: number | string) =>
  tasksRequest<ForwardTarget[]>(`/${Number(taskId)}/comments/forward-targets`, { skipCache: true });

/** 转发单条评论到微信（asLink=true 发链接卡片，false 发纯文本） */
export const forwardCommentToWechat = (
  taskId: number | string,
  commentId: number | string,
  targetUsernames: string[],
  asLink = false,
) =>
  tasksRequest<ForwardResponse>(
    `/${Number(taskId)}/comments/forward-to-wechat`,
    {
      method: 'POST',
      body: JSON.stringify({
        comment_id: Number(commentId),
        target_usernames: targetUsernames,
        as_link: asLink,
      }),
    },
  );

/** 查询当前账号的微信 open_id 绑定状态 */
export const getMyWechatBind = () =>
  authRequest<{ bound: boolean; open_id_masked: string | null }>(`/me/wechat-openid`, {
    skipCache: true,
  });

/** 绑定当前账号的微信 open_id */
export const bindMyWechatOpenid = (openId: string) =>
  authRequest<{ code: number; message: string; open_id: string }>(`/me/wechat-openid`, {
    method: 'POST',
    body: JSON.stringify({ open_id: openId }),
  });

/** 解绑当前账号的微信 open_id */
export const unbindMyWechatOpenid = () =>
  authRequest<{ code: number; message: string }>(`/me/wechat-openid`, { method: 'DELETE' });
