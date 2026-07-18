// API 基础URL配置 - 集中管理所有后端服务地址
//
// 路由约定（与 deploy/nginx/conf/conf.d/app_gateway.conf 对齐）：
//   - dev：前端走相对 /api/*，由 vite.config.ts 代理拆分
//          （/api/ai/* → AI 服务 8401，其余 /api/* → 业务后端 8400）。
//   - 打包后：由 nginx 按环境前缀分发（/t/api/* → 测试，/p/api/* → 生产），
//          且 nginx 用最长前缀把 /api/ai/* 单独转发到 AI 服务。
//          故前端只需在请求路径上带环境前缀，无需关心 AI/业务分流。
//
// 环境前缀由 vite 的 base 自动推导：
//   base='/'        (dev)           → 前缀 ''  → API_ROOT='/api'
//   base='/t/app/'  (构建-测试)     → 前缀 '/t' → API_ROOT='/t/api'
//   base='/p/app/'  (构建-生产)     → 前缀 '/p' → API_ROOT='/p/api'
// （构建时用 `vite build --base=/t/app/` 或 `--base=/p/app/` 指定，见 package.json 的 build:test/build:prod）

const RAW_BASE = import.meta.env.BASE_URL; // dev:'/'，构建:'/t/app/' 或 '/p/app/'
const ENV_PREFIX = RAW_BASE === '/' ? '' : RAW_BASE.replace(/\/app\/?$/, ''); // '' | '/t' | '/p'
const API_ROOT = `${ENV_PREFIX}/api`;

const API_CONFIG = {
  /** 认证服务 - /api/auth (登录/注册/刷新令牌/用户信息) */
  AUTH: {
    BASE_URL: `${API_ROOT}/auth`,
  },
  /** Call服务 - /api/call (AI对话/会话/消息/我的任务) */
  CALL: {
    BASE_URL: `${API_ROOT}/call`,
  },
  /** AI 模块 - /api/ai (诊断问答/LLM对话/会话记忆)，独立服务（ai/run.py @8401） */
  AI: {
    BASE_URL: `${API_ROOT}/ai`,
  },
  /** 工单服务 - /api/tasks (工单CRUD/评论/附件/状态流转) */
  TASKS: {
    BASE_URL: `${API_ROOT}/tasks`,
  },
  /** 后台管理服务 - /api/admin (项目/风险/日报/用户/角色/权限/资源) */
  ADMIN: {
    BASE_URL: `${API_ROOT}/admin`,
  },
  /** 微信服务 - /api/wechat (菜单管理/标签管理/消息发送) */
  WECHAT: {
    BASE_URL: `${API_ROOT}/wechat`,
  },
} as const;

export default API_CONFIG;

export const getApiBaseUrl = (service: keyof typeof API_CONFIG): string => {
  return API_CONFIG[service]?.BASE_URL || '';
};
