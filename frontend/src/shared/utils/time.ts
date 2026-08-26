// 时间统一解析入口 —— 全项目唯一允许解析「后端时间」的地方。
//
// 背景（2026-08-25 时区根治后统一约定）：
// - 后端 DB naive DateTime 列统一存 UTC，序列化输出**无时区后缀**的 ISO（如 "2026-08-25T01:42:00"）。
// - 前端对这类字符串必须「补 Z 当 UTC」解析，再由浏览器按本地时区 +8 转换，不能写死 +8。
//
// 铁律：任何后端时间字段，禁止直接 `new Date(str)` / `dayjs(str)`，必须经 `parseBackendDate`。

import dayjs, { type Dayjs } from 'dayjs';

/**
 * 把后端时间解析为本地时区 Date。兼容三种口径：
 * - ISO 字符串（无时区后缀）：来自 naive DateTime 列存 UTC → 补 Z 当 UTC 解析。
 * - ISO 字符串（带 Z / ±HH:MM）：aware datetime 序列化输出 → 原样解析。
 * - number：Unix 秒（AI 接口历史口径，现已逐步统一为 ISO，保留兼容）→ 转 UTC ISO 再解析。
 *
 * 返回 null 表示无法解析。不写死 +8，跨时区浏览器同样正确。
 */
export function parseBackendDate(input: string | number | null | undefined): Date | null {
  if (input === null || input === undefined || input === '') return null;

  let s: string;
  if (typeof input === 'number') {
    if (!isFinite(input)) return null;
    // Unix 秒 → 带 Z 的 UTC ISO（由浏览器按本地时区转换）
    s = new Date(input * 1000).toISOString();
  } else {
    s = String(input).trim().replace(' ', 'T');
    if (!s) return null;
    const hasTz = /([+-]\d{2}:?\d{2}|Z)$/.test(s);
    if (!hasTz && /T\d{2}:\d{2}/.test(s)) s += 'Z';
  }

  const d = new Date(s);
  return isNaN(d.getTime()) ? null : d;
}

/**
 * 把后端时间解析为本地时区 Dayjs（供 DatePicker / dayjs 计算用）。
 * 语义与 parseBackendDate 一致，仅返回类型为 dayjs 对象。
 */
export function parseBackendDayjs(input: string | number | null | undefined): Dayjs | null {
  const d = parseBackendDate(input);
  if (!d) return null;
  return dayjs(d);
}

/**
 * 把后端时间格式化为「YYYY/MM/DD HH:mm」本地时区字符串（最常用显示格式）。
 */
export function formatBackendTime(input: string | number | null | undefined): string {
  const d = parseBackendDate(input);
  if (!d) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
