// 微信公众号用户数据接口 - 供 AdminEntries「其他」页用户统计卡片使用
//
// 对应后端路由：
//   - POST /api/wechat/batch-user-info  当前用户构成（真实/虚拟）
//   - POST /api/wechat/user-summary     用户增减趋势 / 关注来源分布
//
// 鉴权：用户 JWT（createRequest 默认带 Authorization: Bearer <token>）
// 缓存：默认开启 5 分钟 GET 缓存，但本文件两个接口都是 POST 且 10 秒轮询，
//       故调用方需传 skipCache:true 防止缓存堆积，已在内部默认 skipCache。

import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

const wechatRequest = createRequest(API_CONFIG.WECHAT.BASE_URL, 'Wechat');

// ============================================================
// 一、用户来源渠道标签（与后端 wechat.py:user-summary docstring 对齐）
// ============================================================
// 0=其他合计, 1=公众号搜索, 17=名片分享, 30=扫描二维码, 57=文章内账号名称,
// 100=微信广告, 161=他人转载, 149=小程序关注, 200=视频号, 201=直播
export const USER_SOURCE_LABELS: Record<number, string> = {
  0: '其他合计',
  1: '公众号搜索',
  17: '名片分享',
  30: '扫描二维码',
  57: '文章内账号名称',
  100: '微信广告',
  149: '小程序关注',
  161: '他人转载',
  200: '视频号',
  201: '直播',
};

// ============================================================
// 二、user-summary 响应类型
// ============================================================
export interface UserSummaryItem {
  /** 统计日期，格式 yyyy-MM-dd */
  ref_date: string;
  /** 用户来源渠道（USER_SOURCE_LABELS 中的 key） */
  user_source: number;
  /** 当日新增用户数 */
  new_user: number;
  /** 当日取消关注用户数 */
  cancel_user: number;
}

export interface UserSummaryResponse {
  success: boolean;
  list: UserSummaryItem[];
  total: number;
}

/**
 * 获取用户增减数据（柱状图/饼图数据源）。
 *
 * 后端会自动把跨度 >7 天的区间拆成多段调用微信 API 再聚合 list 返回，
 * 调用方传任意跨度均可。微信数据 T+1 延迟，最早可查昨日。
 *
 * @param beginDate yyyy-MM-dd
 * @param endDate   yyyy-MM-dd
 */
export function fetchUserSummary(beginDate: string, endDate: string): Promise<UserSummaryResponse> {
  return wechatRequest<UserSummaryResponse>(
    '/user-summary',
    {
      method: 'POST',
      body: JSON.stringify({ begin_date: beginDate, end_date: endDate }),
      skipCache: true, // POST 不走 GET 缓存；10 秒轮询需保证每次都打到后端
    },
  );
}

// ============================================================
// 三、batch-user-info 响应类型
// ============================================================
export interface WechatUserInfo {
  /** 0=未关注 1=已关注（用于区分真实/虚拟用户） */
  subscribe: number;
  /** 用户 openid（users.id 即微信 openid） */
  openid?: string;
  /** 关注时间戳（秒） */
  subscribe_time?: number;
  /** unionid，可能为空 */
  unionid?: string;
  /** 用户备注 */
  remark?: string;
  /** 用户标签 id 列表 */
  tagid_list?: number[];
  /** 关注场景（与 USER_SOURCE_LABELS 不同，微信 subscribe_scene 取值见微信文档） */
  subscribe_scene?: number;
}

export interface BatchUserInfoResponse {
  success: boolean;
  user_info_list: WechatUserInfo[];
  total: number;
}

/**
 * 批量获取用户基本信息（环形图数据源）。
 *
 * 不传请求体时后端自动查 users 表获取全部真实微信用户 openid（已过滤 user_ 前缀的
 * 虚拟账号），再调用微信 batchget 拉取 subscribe 状态。前端调用方据此把
 * `subscribe === 1` 计为真实用户，其余计为虚拟用户。
 */
export function fetchBatchUserInfo(): Promise<BatchUserInfoResponse> {
  return wechatRequest<BatchUserInfoResponse>(
    '/batch-user-info',
    {
      method: 'POST',
      body: JSON.stringify({}), // 显式空 body，触发后端「查全部用户」分支
      skipCache: true,           // 10 秒轮询需保证每次都打到后端
    },
  );
}
