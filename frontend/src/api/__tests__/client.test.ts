import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  ApiError,
  setToken,
  clearToken,
  getToken,
  initToken,
  createRequest,
  clearCache,
  validateResponse,
} from '../client';

describe('ApiError', () => {
  it('should create an ApiError with correct properties', () => {
    const error = new ApiError('测试错误', 404);
    expect(error).toBeInstanceOf(Error);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.name).toBe('ApiError');
    expect(error.message).toBe('测试错误');
    expect(error.statusCode).toBe(404);
  });

  it('should store originalError', () => {
    const original = new Error('原始错误');
    const error = new ApiError('包装错误', 500, original);
    expect(error.originalError).toBe(original);
  });
});

describe('Token 管理', () => {
  beforeEach(() => {
    clearToken();
    localStorage.clear();
  });

  it('getToken should return null when no token set', () => {
    expect(getToken()).toBeNull();
  });

  it('setToken and getToken should work', () => {
    setToken('test-token-123');
    expect(getToken()).toBe('test-token-123');
  });

  it('clearToken should reset token to null', () => {
    setToken('test-token-123');
    expect(getToken()).toBe('test-token-123');
    clearToken();
    expect(getToken()).toBeNull();
  });

  it('initToken should read from localStorage', () => {
    localStorage.setItem('auth_token', 'saved-token');
    const token = initToken();
    expect(token).toBe('saved-token');
  });

  it('initToken should return existing token if already in memory', () => {
    setToken('memory-token');
    const token = initToken();
    expect(token).toBe('memory-token');
  });

  it('initToken should throw if no token anywhere', () => {
    expect(() => initToken()).toThrow('用户未登录，请先登录');
  });
});

describe('createRequest', () => {
  beforeEach(() => {
    clearToken();
    clearCache();
    vi.restoreAllMocks();
  });

  it('should create a request function', () => {
    const request = createRequest('/api/test', 'TEST');
    expect(typeof request).toBe('function');
  });

  it('should throw ApiError for aborted/timeout requests', async () => {
    // Simulate a generic error to test error wrapping
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Network aborted'));

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');

    await expect(request('/endpoint')).rejects.toBeInstanceOf(ApiError);
  });

  it('should throw ApiError for network failures', async () => {
    const networkError = new TypeError('Failed to fetch');
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(networkError);

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');

    await expect(request('/endpoint')).rejects.toMatchObject({
      statusCode: 0,
      message: '无法连接到服务器，请检查后端服务是否正常运行',
    });
  });

  it('should throw ApiError for non-ok responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: '禁止访问' }),
    } as Response);

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');

    await expect(request('/endpoint')).rejects.toMatchObject({
      statusCode: 403,
      message: '禁止访问',
    });
  });

  it('should succeed and parse JSON for ok responses', async () => {
    const responseData = { id: 1, name: 'test' };
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: true,
      json: async () => responseData,
      status: 200,
    } as Response);

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');
    const result = await request('/endpoint');

    expect(result).toEqual(responseData);
  });

  it('should include Authorization header when token is set', async () => {
    let capturedHeaders: HeadersInit | undefined;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      capturedHeaders = init?.headers;
      return { ok: true, json: async () => ({}), status: 200 } as Response;
    });

    setToken('bearer-token-abc');
    const request = createRequest('/api/test', 'TEST');
    await request('/endpoint');

    const headers = capturedHeaders as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer bearer-token-abc');
  });

  it('should retry on 5xx errors', async () => {
    let callCount = 0;
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      callCount++;
      if (callCount <= 2) {
        return {
          ok: false,
          status: 503,
          json: async () => ({ message: '服务暂时不可用' }),
        } as Response;
      }
      return { ok: true, json: async () => ({ success: true }), status: 200 } as Response;
    });

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');
    const result = await request('/endpoint');

    expect(callCount).toBe(3); // original + 2 retries
    expect(result).toEqual({ success: true });
  }, 10000);

  it('should fallback to generic error message when response has no detail', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: async () => ({}),
    } as Response);

    setToken('test-token');
    const request = createRequest('/api/test', 'TEST');

    await expect(request('/endpoint')).rejects.toMatchObject({
      statusCode: 400,
      message: 'HTTP错误! 状态码: 400',
    });
  });
});

describe('validateResponse', () => {
  it('should return data when all expected fields present', () => {
    const data = { id: 1, name: 'test', email: 'a@b.com' };
    const result = validateResponse(data, ['id', 'name']);
    expect(result).toBe(data);
  });

  it('should throw ApiError when required field missing', () => {
    const data = { id: 1 };
    expect(() => validateResponse(data, ['id', 'name'])).toThrow(ApiError);
    expect(() => validateResponse(data, ['id', 'name'])).toThrow('响应缺少必需字段: name');
  });

  it('should throw ApiError for non-object data', () => {
    expect(() => validateResponse(null as unknown as Record<string, unknown>)).toThrow('无效的响应格式');
    expect(() => validateResponse('string' as unknown as Record<string, unknown>)).toThrow('无效的响应格式');
  });

  it('should pass validation when no expected fields specified', () => {
    const data = { anything: 'works' };
    expect(validateResponse(data)).toBe(data);
  });
});

describe('clearCache', () => {
  it('should clear all cache when called without params', () => {
    // No error should be thrown
    expect(() => clearCache()).not.toThrow();
  });

  it('should clear specific cache entry', () => {
    expect(() => clearCache('/test', 'http://localhost')).not.toThrow();
  });
});
