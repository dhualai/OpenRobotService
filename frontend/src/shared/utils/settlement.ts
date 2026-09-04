// 业绩核算期 settlement_period 工具 —— 与后端 dashboard.py `_parse_settlement_period` 口径保持一致。
//
// 业绩核算期是企业微信项目表「业绩核算期」列手工填写的内容，常见写法：
// - YYYYMM   → 202608（2026年8月）
// - YYYY-MM  → 2026-08
// 兼容分隔符 - / . 及个位月份（如 2026/8）。统一归一化为 YYYY-MM 用于展示与比对。

/** 当前年月，格式 YYYY-MM（与后端 monthly 接口 key、柱状图 active key 一致）。 */
export function currentYearMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/**
 * 业绩核算期归一化为 YYYY-MM；非法/缺失返回空串，调用方按「无核算期」处理。
 */
export function normalizeSettlementPeriod(period?: string | null): string {
  const v = (period ?? '').trim();
  const m = v.match(/^(\d{4})[-/.]?(\d{1,2})$/);
  if (!m) return '';
  const month = Number(m[2]);
  if (!(month >= 1 && month <= 12)) return '';
  return `${m[1]}-${String(month).padStart(2, '0')}`;
}
