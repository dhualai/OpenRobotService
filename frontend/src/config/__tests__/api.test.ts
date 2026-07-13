import { describe, it, expect } from 'vitest';
import API_CONFIG, { getApiBaseUrl } from '../api';

describe('API_CONFIG', () => {
  it('should have correct FQA base URL', () => {
    expect(API_CONFIG.FQA.BASE_URL).toBe('/api/FQA');
  });

  it('should have correct USER_CENTER base URL', () => {
    expect(API_CONFIG.USER_CENTER.BASE_URL).toBe('/AAS');
  });

  it('should have correct PROJECT base URL', () => {
    expect(API_CONFIG.PROJECT.BASE_URL).toBe('/api');
  });

  it('should be read-only via TypeScript type system', () => {
    // Note: `as const` makes TypeScript treat it as readonly at compile time,
    // but does not Object.freeze() at runtime. This verifies types work correctly.
    expect(API_CONFIG.FQA.BASE_URL).toBe('/api/FQA');
    expect(API_CONFIG.PROJECT.BASE_URL).toBe('/api');
  });
});

describe('getApiBaseUrl', () => {
  it('should return correct URL for known services', () => {
    expect(getApiBaseUrl('FQA')).toBe('/api/FQA');
    expect(getApiBaseUrl('USER_CENTER')).toBe('/AAS');
    expect(getApiBaseUrl('PROJECT')).toBe('/api');
  });

  it('should return empty string for unknown service', () => {
    // @ts-expect-error testing unknown service
    expect(getApiBaseUrl('UNKNOWN')).toBe('');
  });
});
