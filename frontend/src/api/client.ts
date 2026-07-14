// 通用API客户端 - 从 HelpDesk apiClient.js 移植，增加 TypeScript 类型
// 功能：Token管理 / 请求缓存 / 自动重试 / 超时控制 / 发布-订阅Token刷新

const MAX_RETRIES = 2;
const RETRY_DELAY = 1000;
const REQUEST_TIMEOUT = 30000;
const CACHE_EXPIRY_TIME = 5 * 60 * 1000; // 5分钟

type Subscriber = (token: string | null) => void;

// 请求缓存
const requestCache = new Map<string, { data: unknown; timestamp: number }>();

// Token管理（全局共享）
let userToken: string | null = null;
let isRefreshing = false;
let refreshSubscribers: Subscriber[] = [];

export interface ApiErrorType {
  name: string;
  message: string;
  statusCode: number;
  originalError?: unknown;
}

export class ApiError extends Error {
  statusCode: number;
  originalError?: unknown;

  constructor(message: string, statusCode: number, originalError?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.statusCode = statusCode;
    this.originalError = originalError;
  }
}

export function initToken(): string {
  if (userToken) return userToken;
  try {
    const savedToken = localStorage.getItem('auth_token');
    if (savedToken) {
      userToken = savedToken;
      return userToken;
    }
  } catch { /* SSR safe */ }
  throw new Error('用户未登录，请先登录');
}

export function getToken(): string | null {
  return userToken;
}

export function setToken(token: string): void {
  userToken = token;
}

export function clearToken(): void {
  userToken = null;
}

async function refreshTokenRequest(): Promise<string> {
  const storedRefreshToken = localStorage.getItem('refresh_token');
  if (!storedRefreshToken) {
    throw new Error('No refresh token available');
  }
  const response = await fetch('/api/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: storedRefreshToken }),
  });
  if (!response.ok) throw new Error('Token refresh failed');
  const data = await response.json();
  if (!data.access_token) throw new Error('No access token in refresh response');
  const expiresAt = Date.now() + data.expires_in * 1000;
  userToken = data.access_token;
  localStorage.setItem('auth_token', data.access_token);
  localStorage.setItem('refresh_token', data.refresh_token);
  localStorage.setItem('token_expires_at', String(expiresAt));
  return data.access_token;
}

function onRefreshSuccess(newToken: string): void {
  refreshSubscribers.forEach((cb) => cb(newToken));
  refreshSubscribers = [];
}

function onRefreshFailed(_error: unknown): void {
  refreshSubscribers.forEach((cb) => cb(null));
  refreshSubscribers = [];
}

function subscribeToRefresh(callback: Subscriber): void {
  refreshSubscribers.push(callback);
}

export interface RequestOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: BodyInit | null;
  skipCache?: boolean;
  responseType?: 'json' | 'arrayBuffer';
  skipAuth?: boolean;  // 跳过Token校验（用于登录等无需认证的接口）
}

// createRequest 工厂函数
export function createRequest(baseUrl: string, _serviceName = 'API') {
  const request = async <T = unknown>(
    endpoint: string,
    options: RequestOptions = {},
    retries = 0,
  ): Promise<T> => {
    if (!options.skipAuth && !userToken) {
      initToken();
    }

    const url = `${baseUrl}${endpoint}`;
    const method = (options.method || 'GET').toUpperCase();

    // GET 请求缓存检查
    if (method === 'GET' && !options.skipCache) {
      const cached = requestCache.get(url);
      if (cached && Date.now() - cached.timestamp < CACHE_EXPIRY_TIME) {
        return cached.data as T;
      }
    }

    const defaultHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (userToken) {
      defaultHeaders['Authorization'] = `Bearer ${userToken}`;
    }

    const requestOptions: RequestInit = {
      method: options.method,
      headers: { ...defaultHeaders, ...options.headers },
      body: options.body,
    };

    // FormData 不设置 Content-Type
    if (options.body instanceof FormData) {
      delete (requestOptions.headers as Record<string, string>)['Content-Type'];
    }

    try {
      // 超时控制
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

      const response = await fetch(url, {
        ...requestOptions,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (!response.ok) {
        let errorMessage = `HTTP错误! 状态码: ${response.status}`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.detail || errorData.message || errorData.error || errorMessage;
        } catch { /* ignore */ }

        // 401 → Token刷新
        if (response.status === 401 && retries < MAX_RETRIES) {
          if (!isRefreshing) {
            isRefreshing = true;
            try {
              const newToken = await refreshTokenRequest();
              onRefreshSuccess(newToken);
              return request<T>(endpoint, options, retries + 1);
            } catch (refreshError) {
              onRefreshFailed(refreshError);
              window.location.href = '/login';
              throw refreshError;
            } finally {
              isRefreshing = false;
            }
          } else {
            return new Promise<T>((resolve, reject) => {
              subscribeToRefresh((newToken) => {
                if (newToken) {
                  resolve(request<T>(endpoint, options, retries + 1));
                } else {
                  reject(new Error('Token refresh failed'));
                }
              });
            });
          }
        }

        // 500+ → 重试
        if (response.status >= 500 && retries < MAX_RETRIES) {
          await new Promise((r) => setTimeout(r, RETRY_DELAY * (retries + 1)));
          return request<T>(endpoint, options, retries + 1);
        }

        throw new ApiError(errorMessage, response.status);
      }

      // 解析响应
      let data: T;
      if (options.responseType === 'arrayBuffer') {
        data = (await response.arrayBuffer()) as unknown as T;
      } else {
        data = (await response.json()) as T;
      }

      // 缓存 GET 响应
      if (method === 'GET' && !options.skipCache) {
        requestCache.set(url, { data, timestamp: Date.now() });
      }

      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiError('请求超时，请稍后重试', 408, error);
      }
      if (error instanceof TypeError && error.message.includes('Failed to fetch')) {
        throw new ApiError('无法连接到服务器，请检查后端服务是否正常运行', 0, error);
      }
      throw new ApiError('请求过程中发生未知错误', 0, error);
    }
  };

  return request;
}

export function clearCache(endpoint?: string, baseUrl?: string): void {
  if (endpoint && baseUrl) {
    requestCache.delete(`${baseUrl}${endpoint}`);
  } else {
    requestCache.clear();
  }
}

export function validateResponse<T extends Record<string, unknown>>(
  data: T,
  expectedFields: string[] = []
): T {
  if (!data || typeof data !== 'object') {
    throw new ApiError('无效的响应格式', 200);
  }
  for (const field of expectedFields) {
    if (!(field in data)) {
      throw new ApiError(`响应缺少必需字段: ${field}`, 200);
    }
  }
  return data;
}
