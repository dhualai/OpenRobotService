// 微信数据统计 API 封装
// 后端 /api/wechat/user-summary 走 X-API-Key 鉴权（与用户 JWT 分离），
// key 需与后端 HELPDESK_SYNC_API_KEY 一致（本地 .env 配置为 zentao）。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

/** 内部同步接口 X-API-Key（与后端 HELPDESK_SYNC_API_KEY 一致） */
const SYNC_API_KEY = 'zentao';

export interface UserSummaryItem {
  /** 数据日期 yyyy-MM-dd */
  ref_date: string;
  /** 渠道来源编码，中文含义见 USER_SOURCE_LABELS */
  user_source: number;
  /** 新增用户 */
  new_user: number;
  /** 取消用户 */
  cancel_user: number;
}

export interface UserSummaryResp {
  success: boolean;
  list: UserSummaryItem[];
  total: number;
}

/** user_source 渠道编码中文含义（微信官方渠道定义） */
export const USER_SOURCE_LABELS: Record<number, string> = {
  0: '其他合计',
  1: '公众号搜索',
  17: '名片分享',
  30: '扫描二维码',
  57: '文章内账号名称',
  100: '微信广告',
  161: '他人转载',
  149: '小程序关注',
  200: '视频号',
  201: '直播',
};

const request = createRequest(API_CONFIG.WECHAT.BASE_URL, 'Wechat');

/**
 * 获取公众号用户增减数据（读 user_statistics 表：每日凌晨 1:00 定时任务落库的微信渠道明细）。
 * 查询跨度不限；但数据 T+1 落库，end_date 不能为当天或未来（后端返回 400 提示）。
 */
export function fetchUserSummary(beginDate: string, endDate: string): Promise<UserSummaryResp> {
  return request<UserSummaryResp>('/user-summary-db', {
    method: 'POST',
    headers: { 'X-API-Key': SYNC_API_KEY },
    body: JSON.stringify({ begin_date: beginDate, end_date: endDate }),
    skipCache: true,
  });
}

export interface WechatUserInfo {
  /** 1=已关注（真实用户），非 1（0/缺失）=已取关（虚拟用户） */
  subscribe: number;
  openid: string;
  tagid_list?: number[];
  /** 关注渠道来源，如 ADD_SCENE_SEARCH（公众号搜索），完整编码见微信官方文档 */
  subscribe_scene?: string;
  [key: string]: unknown;
}

export interface BatchUserInfoResp {
  success: boolean;
  user_info_list: WechatUserInfo[];
  /** 当前用户总数 */
  total: number;
}

/**
 * 获取当前用户构成（读 user_info 表最新快照：整点快照任务落库的 batch-user-info 返回值，
 * 最长滞后 1 小时）。subscribe===1 为真实用户，否则为虚拟用户。
 */
export function fetchBatchUserInfo(): Promise<BatchUserInfoResp> {
  return request<BatchUserInfoResp>('/batch-user-info-db', {
    method: 'POST',
    headers: { 'X-API-Key': SYNC_API_KEY },
    skipCache: true,
  });
}
