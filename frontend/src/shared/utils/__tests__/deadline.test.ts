import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { formatOverdueDuration } from '../deadline';

// 固定「当前时间」，避免真实时钟让断言随时钟漂移。
// 后端 deadline_at 为 naive DateTime 列（存 UTC、序列化无时区后缀），
// 故这里的入参与之同口径：无后缀 ISO 字符串，由 parseBackendDayjs 补 Z 当 UTC 解析。
const NOW = new Date('2026-09-15T12:00:00Z');

describe('formatOverdueDuration', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('超过一天：天 + 小时', () => {
    expect(formatOverdueDuration('2026-09-12T10:00:00')).toBe('3天2小时');
  });

  it('整天时不带小时', () => {
    expect(formatOverdueDuration('2026-09-13T12:00:00')).toBe('2天');
  });

  it('不足一天：小时 + 分', () => {
    expect(formatOverdueDuration('2026-09-15T06:48:00')).toBe('5小时12分');
  });

  it('整小时不带分', () => {
    expect(formatOverdueDuration('2026-09-15T08:00:00')).toBe('4小时');
  });

  it('不足一小时：分钟', () => {
    expect(formatOverdueDuration('2026-09-15T11:23:00')).toBe('37分钟');
  });

  it('未超时（截止时间未到 / 恰好到期）→ 空串', () => {
    expect(formatOverdueDuration('2026-09-15T13:00:00')).toBe('');
    expect(formatOverdueDuration('2026-09-15T12:00:00')).toBe('');
  });

  it('缺失或非法 → 空串', () => {
    expect(formatOverdueDuration(null)).toBe('');
    expect(formatOverdueDuration(undefined)).toBe('');
    expect(formatOverdueDuration('')).toBe('');
    expect(formatOverdueDuration('not-a-date')).toBe('');
  });
});
