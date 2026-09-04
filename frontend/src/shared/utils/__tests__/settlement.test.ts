import { describe, it, expect } from 'vitest';
import { currentYearMonth, normalizeSettlementPeriod } from '../settlement';

describe('normalizeSettlementPeriod', () => {
  it('主格式 YYYYMM：202608 → 2026-08', () => {
    expect(normalizeSettlementPeriod('202608')).toBe('2026-08');
  });

  it('兼容 YYYY-MM：2026-08 → 2026-08', () => {
    expect(normalizeSettlementPeriod('2026-08')).toBe('2026-08');
  });

  it('兼容个位月份与其它分隔符：2026-8 / 2026/08 / 2026.08 → 2026-08', () => {
    expect(normalizeSettlementPeriod('2026-8')).toBe('2026-08');
    expect(normalizeSettlementPeriod('2026/08')).toBe('2026-08');
    expect(normalizeSettlementPeriod('2026.08')).toBe('2026-08');
  });

  it('容忍首尾空白', () => {
    expect(normalizeSettlementPeriod(' 202608 ')).toBe('2026-08');
  });

  it('非法/缺失 → 空串', () => {
    expect(normalizeSettlementPeriod(undefined)).toBe('');
    expect(normalizeSettlementPeriod(null)).toBe('');
    expect(normalizeSettlementPeriod('')).toBe('');
    expect(normalizeSettlementPeriod('2026-13')).toBe(''); // 月越界
    expect(normalizeSettlementPeriod('2026-00')).toBe(''); // 月为 0
    expect(normalizeSettlementPeriod('abc')).toBe('');
    expect(normalizeSettlementPeriod('20260801')).toBe(''); // 含日，无法识别
  });
});

describe('currentYearMonth', () => {
  it('返回 YYYY-MM 格式', () => {
    const m = currentYearMonth().match(/^\d{4}-(0[1-9]|1[0-2])$/);
    expect(m).not.toBeNull();
  });
});
