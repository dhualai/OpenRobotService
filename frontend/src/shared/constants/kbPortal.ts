/**
 * 智库（功能拓扑）入口地址。
 *
 * 智库是独立微服务（doc-service），作为摇人吧的子页面挂在 <摇人吧 base>/kb/ 下，
 * 与摇人吧**同源**（浏览器只看到 usp.ep-zl.com，无跨域）。地址按环境注入：
 * staging=/t/app/kb/、production=/p/app/kb/，由 VITE_KB_PORTAL_URL 提供。
 *
 * 入口位置：后台管理 →「更多功能」→「其他」→ 一级菜单「功能拓扑」。
 */
export const KB_PORTAL_URL: string = import.meta.env.VITE_KB_PORTAL_URL || '/t/app/kb/';