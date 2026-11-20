/**
 * react-markdown 的 urlTransform 扩展：放行 data:image/ 协议 + 裸 /api/ 引用补部署前缀。
 *
 * 背景1：react-markdown v9+ 内置 defaultUrlTransform 做协议白名单
 * （http/https/mailto 等），data: 一律清空 src——聊天记录附件 md 里
 * 内嵌的 base64 图片（data:image/...;base64）在工单详情/附件预览中
 * 全部裂图。这里只对 data:image/ 前缀放行（不放开任意 data:，如
 * data:text/html 有脚本执行面），其余 URL 仍走默认白名单清洗。
 *
 * 背景2：后端附件代理 URL（spec-doc 图片、/api/tasks/files、/api/call/files）
 * 统一返回裸 /api/... 相对路径，md 正文里也这么存（与环境无关）。
 * dev 走 vite 代理裸路径可用；但 test/prod 网关只路由 /t/api/、/p/api/
 * 带前缀路径，裸 /api/ 直接 404（编辑器预览/附件预览裂图根因）。
 * 故在此统一补 ENV_PREFIX（dev ''、test '/t'、prod '/p'），
 * 覆盖 md 里所有 img src / a href 的相对 /api/ 引用。
 */
import { defaultUrlTransform } from 'react-markdown';
import { ENV_PREFIX } from '@/config/api';

/**
 * 裸 /api/ 相对引用 → 补部署环境前缀（/t、/p）。
 * 已带前缀（/t/api/、/p/api/）、绝对 URL、其他路径原样返回。
 */
export function toAppUrl(url: string): string {
  return url.startsWith('/api/') ? `${ENV_PREFIX}${url}` : url;
}

/**
 * react-markdown urlTransform 统一出口：
 * data:image/ 放行 → 裸 /api/ 补环境前缀 → 其余走默认协议白名单清洗。
 */
export function appUrlTransform(url: string): string {
  if (url.startsWith('data:image/')) return url;
  if (url.startsWith('clipboard:')) return url;
  const appUrl = toAppUrl(url);
  if (appUrl !== url) return appUrl;
  return defaultUrlTransform(url);
}
