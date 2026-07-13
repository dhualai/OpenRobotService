// API 基础URL配置 - 集中管理所有后端服务地址

const API_CONFIG = {
  FQA: {
    BASE_URL: '/api/FQA',
  },
  USER_CENTER: {
    BASE_URL: '/AAS',
  },
  PROJECT: {
    BASE_URL: '/api',
  },
} as const;

export default API_CONFIG;

export const getApiBaseUrl = (service: keyof typeof API_CONFIG): string => {
  return API_CONFIG[service]?.BASE_URL || '';
};
