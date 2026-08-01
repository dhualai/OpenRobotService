import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

// 顶层（一级 Tab）路由：在这些页面右滑返回不应退出到微信服务号，而应被拦截停留。
const TOP_LEVEL_PATHS = ['/', '/call', '/tasks', '/admin'];

function isTopLevel(pathname: string): boolean {
  return TOP_LEVEL_PATHS.includes(pathname);
}

/**
 * 微信内置浏览器（尤其 iOS）的系统级边缘右滑会触发 history.back()。
 * 当 history 已处于 webview 根层（即一级 Tab 页面）时，右滑会直接退出 H5 回到服务号会话。
 *
 * 本守卫策略：
 *  - 进入应用时额外 push 一层占位历史（复制 react-router 当前 state 以保持其内部 history 兼容），
 *    保证 webview 内 history 深度 >= 2；
 *  - 监听 popstate：从一级 Tab 右滑（或已无可返回页面时）重新压栈「吃掉」返回手势，停留在应用内；
 *  - 从子页面（如工单详情 / 任务详情）右滑则放行，由 react-router 正常返回上一级（仍停留在应用内）。
 *
 * 全部使用原生 pushState 并复制 react-router 的 history.state，避免污染其内部状态、
 * 也不影响各页面返回按钮的 navigate(-1) 行为。
 */
export function useWechatBackGuard() {
  const location = useLocation();
  const locationRef = useRef(location);
  locationRef.current = location;

  useEffect(() => {
    // 进入应用时压一层占位历史，避免顶层右滑直接退出 webview。
    // 复制 react-router 当前 state，确保 popstate 后其仍能正确恢复 location。
    const seedState = window.history.state ?? null;
    window.history.pushState(seedState, '', window.location.href);

    const onPop = () => {
      const from = locationRef.current;
      // pop 之后若 history 只剩 1 层，说明已无可返回页面，再退即退出 webview（深链接兜底）。
      const willExit = window.history.length <= 1;
      if (isTopLevel(from.pathname) || willExit) {
        // 拦截退出：重新压栈，停留在当前页面（不回到微信服务号）。
        const curState = window.history.state ?? null;
        window.history.pushState(curState, '', window.location.href);
      }
      // 子页面右滑：放行，react-router 已随 popstate 渲染上一级。
    };

    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // 仅初始化一次（应用生命周期内常驻，MainLayout 不随子路由切换卸载）。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
