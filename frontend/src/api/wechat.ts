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

export interface SceneDistributionItem {
  /** 关注渠道编码，如 ADD_SCENE_SEARCH；中文含义见 SUBSCRIBE_SCENE_LABELS */
  scene: string;
  /** 该渠道的已关注用户数 */
  value: number;
}

export interface BatchUserInfoResp {
  success: boolean;
  /** 当前用户总数 */
  total: number;
  /** 已关注用户数（subscribe===1） */
  real: number;
  /** 已取关用户数（虚拟用户） */
  virtual: number;
  /** 已关注用户的关注渠道分布（按 value 降序） */
  scene_distribution: SceneDistributionItem[];
}

/**
 * 获取当前用户构成聚合统计（读 user_info 表最新快照：整点快照任务落库，
 * 最长滞后 1 小时）。后端已在 DB 侧完成 real/virtual 与渠道分布聚合，
 * 响应体仅含统计值，不再返回全量 user_info_list。
 *
 * 客户端缓存：后端每整点刷新，数据最长滞后 1 小时，此处缓存 10 分钟，
 * 避免每次进入「其他」页都重复请求。
 */
const BATCH_USER_INFO_CACHE_TTL = 10 * 60 * 1000; // 10 分钟
let _batchUserInfoCache: { data: BatchUserInfoResp; timestamp: number } | null = null;

export async function fetchBatchUserInfo(force = false): Promise<BatchUserInfoResp> {
  if (!force && _batchUserInfoCache && Date.now() - _batchUserInfoCache.timestamp < BATCH_USER_INFO_CACHE_TTL) {
    return _batchUserInfoCache.data;
  }
  const data = await request<BatchUserInfoResp>('/batch-user-info-db', {
    method: 'POST',
    headers: { 'X-API-Key': SYNC_API_KEY },
    skipCache: true,
  });
  _batchUserInfoCache = { data, timestamp: Date.now() };
  return data;
}
