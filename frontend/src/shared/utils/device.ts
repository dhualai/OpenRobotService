// 设备类型判定工具
// 原生 datetime-local 在 PC 与移动端行为不同（PC 为浏览器日期面板，移动端唤起系统滚轮），
// 各页面涉及日期/时间选择组件时按本判定分流，保证口径一致。

/**
 * 判断当前是否为 PC（桌面）端。
 * UA 不含常见移动端关键字即视为 PC。
 * SSR / 无 navigator 环境回退为非 PC（移动端组件更安全）。
 */
export const isPC = (): boolean => {
  if (typeof navigator === 'undefined') return false;
  return !/Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
};
