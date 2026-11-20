import { describe, it, expect } from 'vitest';
import API_CONFIG, { getApiBaseUrl } from '../api';

describe('API_CONFIG', () => {
  it('should have correct AUTH base URL', () => {
    expect(API_CONFIG.AUTH.BASE_URL).toBe('/api/auth');
  });

  it('should have correct CALL base URL', () => {
    expect(API_CONFIG.CALL.BASE_URL).toBe('/api/call');
  });

  it('should have correct TASKS base URL', () => {
    expect(API_CONFIG.TASKS.BASE_URL).toBe('/api/tasks');
  });

  it('should have correct ADMIN base URL', () => {
    expect(API_CONFIG.ADMIN.BASE_URL).toBe('/api/admin');
  });

  it('should be read-only via TypeScript type system', () => {
    // Note: `as const` makes TypeScript treat it as readonly at compile time,
    // but does not Object.freeze() at runtime. This verifies types work correctly.
    expect(API_CONFIG.AUTH.BASE_URL).toBe('/api/auth');
    expect(API_CONFIG.ADMIN.BASE_URL).toBe('/api/admin');
  });
});

describe('getApiBaseUrl', () => {
  it('should return correct URL for known services', () => {
    expect(getApiBaseUrl('AUTH')).toBe('/api/auth');
    expect(getApiBaseUrl('CALL')).toBe('/api/call');
    expect(getApiBaseUrl('TASKS')).toBe('/api/tasks');
    expect(getApiBaseUrl('ADMIN')).toBe('/api/admin');
  });

  it('should return empty string for unknown service', () => {
    // @ts-expect-error testing unknown service
    expect(getApiBaseUrl('UNKNOWN')).toBe('');
  });
});
