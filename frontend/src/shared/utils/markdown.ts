/**
 * react-markdown 的 urlTransform 扩展：放行 data:image/ 协议。
 *
 * 背景：react-markdown v9+ 内置 defaultUrlTransform 做协议白名单
 * （http/https/mailto 等），data: 一律清空 src——聊天记录附件 md 里
 * 内嵌的 base64 图片（data:image/...;base64）在工单详情/附件预览中
 * 全部裂图。这里只对 data:image/ 前缀放行（不放开任意 data:，如
 * data:text/html 有脚本执行面），其余 URL 仍走默认白名单清洗。
 */
import { defaultUrlTransform } from 'react-markdown';

export function urlTransformAllowDataImage(url: string): string {
  if (url.startsWith('data:image/')) return url;
  return defaultUrlTransform(url);
}
