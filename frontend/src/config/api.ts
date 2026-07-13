// API 基础URL配置 - 集中管理所有后端服务地址
// 后端地址: http://8.152.219.181:8008
// Swagger: http://8.152.219.181:8008/docs

const API_CONFIG = {
  /** 认证服务 - /api/auth (登录/注册/刷新令牌/用户信息) */
  AUTH: {
    BASE_URL: '/api/auth',
  },
  /** Call服务 - /api/call (AI对话/会话/消息/我的任务) */
  CALL: {
    BASE_URL: '/api/call',
  },
  /** 工单服务 - /api/tasks (工单CRUD/评论/附件/状态流转) */
  TASKS: {
    BASE_URL: '/api/tasks',
  },
  /** 后台管理服务 - /api/admin (项目/风险/日报/用户/角色/权限/资源) */
  ADMIN: {
    BASE_URL: '/api/admin',
  },
} as const;

export default API_CONFIG;

export const getApiBaseUrl = (service: keyof typeof API_CONFIG): string => {
  return API_CONFIG[service]?.BASE_URL || '';
};
