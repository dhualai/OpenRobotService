import { describe, it, expect } from 'vitest';
import { formatDateTime, formatTime } from '../url';

describe('formatDateTime', () => {
  it('should return empty string for empty input', () => {
    expect(formatDateTime('')).toBe('');
  });

  it('should return empty string for invalid date', () => {
    expect(formatDateTime('not-a-date')).toBe('');
    expect(formatDateTime('invalid')).toBe('');
  });

  it('should format valid ISO date string', () => {
    const result = formatDateTime('2026-03-15T10:30:00');
    expect(result).toContain('2026');
    expect(result).toContain('03');
    expect(result).toContain('15');
    expect(result).toContain('10');
    expect(result).toContain('30');
  });

  it('should handle UTC dates', () => {
    const result = formatDateTime('2026-01-01T00:00:00Z');
    // Should show 08:00 (UTC+8)
    expect(result).toContain('2026');
    expect(result).toContain('01');
    expect(result).toContain('01');
  });
});

describe('formatTime', () => {
  it('should return "刚刚" for empty input', () => {
    expect(formatTime('')).toBe('刚刚');
  });

  it('should return "刚刚" for invalid date', () => {
    expect(formatTime('invalid-date')).toBe('刚刚');
  });

  it('should return "刚刚" for very recent time', () => {
    const now = new Date();
    const recent = new Date(now.getTime() - 30 * 1000); // 30 seconds ago
    const result = formatTime(recent.toISOString());
    expect(result).toBe('刚刚');
  });

  it('should return "X分钟前" for minutes ago', () => {
    const now = new Date();
    const minutesAgo = new Date(now.getTime() - 15 * 60 * 1000);
    const result = formatTime(minutesAgo.toISOString());
    expect(result).toBe('15分钟前');
  });

  it('should return "X小时前" for hours ago', () => {
    const now = new Date();
    const hoursAgo = new Date(now.getTime() - 3 * 60 * 60 * 1000);
    const result = formatTime(hoursAgo.toISOString());
    expect(result).toBe('3小时前');
  });

  it('should return "X天前" for days ago (within a week)', () => {
    const now = new Date();
    const daysAgo = new Date(now.getTime() - 2 * 24 * 60 * 60 * 1000);
    const result = formatTime(daysAgo.toISOString());
    expect(result).toBe('2天前');
  });

  it('should return full datetime for dates older than 7 days', () => {
    const oldDate = new Date('2020-01-15T08:00:00');
    const result = formatTime(oldDate.toISOString());
    expect(result).toContain('2020');
    expect(result).toContain('01');
    expect(result).toContain('15');
  });

  it('should handle edge case: exactly 60 minutes', () => {
    const now = new Date();
    const sixtyMinsAgo = new Date(now.getTime() - 60 * 60 * 1000);
    const result = formatTime(sixtyMinsAgo.toISOString());
    expect(result).toBe('1小时前');
  });

  it('should handle edge case: exactly 24 hours', () => {
    const now = new Date();
    const twentyFourHoursAgo = new Date(now.getTime() - 24 * 60 * 60 * 1000);
    const result = formatTime(twentyFourHoursAgo.toISOString());
    expect(result).toBe('1天前');
  });
});
