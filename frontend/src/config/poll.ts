// 前端轮询间隔（毫秒）：系统任务页列表/分类角标自动刷新 与 底部导航「待我处理」角标
// 共用同一份配置，避免多处硬编码间隔不一致。
//
// 通过 VITE_POLL_INTERVAL_MS 配置（.env.production / .env.staging），缺省 10s；
// 配置缺失、非法或小于下限（5s）时回退默认值，防止误配把后端打爆。
const MIN_POLL_INTERVAL_MS = 5000;
const DEFAULT_POLL_INTERVAL_MS = 10000;

const raw = Number(import.meta.env.VITE_POLL_INTERVAL_MS);
export const POLL_INTERVAL_MS =
  Number.isFinite(raw) && raw >= MIN_POLL_INTERVAL_MS ? raw : DEFAULT_POLL_INTERVAL_MS;
