// 最晚解决时间（deadline_at）区间计算：与工单优先级映射（紧急 24h / 高 72h / 中 120h / 低 336h）。
// 基准时间 = 工单创建时间（提单弹窗为提单时刻、编辑弹窗为 DB created_at），而非用户操作时刻，
// 确保「最晚解决时间 = 创建时间 + 优先级时长」不随用户切换优先级/编辑操作漂移。
import dayjs, { type Dayjs } from 'dayjs';
import { PRIORITY_DEADLINE_HOURS, normalizePriority } from '@/shared/constants/ticket';
import { parseBackendDayjs } from '@/shared/utils/time';

export interface DeadlineRange {
  min: Dayjs;
  max: Dayjs;
  hours: number;
}

/**
 * 把后端时间解析为本地时区 Dayjs。统一委托 `parseBackendDayjs`（唯一解析入口）。
 *
 * 后端 naive DateTime 列存 UTC、序列化输出**无时区后缀**的 ISO（如 "2026-08-30T02:00:00"），
 * dayjs 会把无时区字符串当本地时间解析导致少 8 小时，故补 Z 当 UTC 解析、由浏览器按本地时区 +8 转换。
 *
 * 用于：① getDeadlineRange 计算基准；② DatePicker 回显 deadline_at（编辑弹窗的 editForm.deadline_at）。
 */
export function parseDeadlineString(input: string | number | null | undefined): Dayjs | null {
  return parseBackendDayjs(input);
}

/**
 * 根据优先级 + 工单创建时间，计算「最晚解决时间」可选区间 [min, max]（小时精度）。
 * - min = 创建时间（向上取整到下一个整点，如 09:20 → 10:00；已是整点则保持不变）
 * - max = 创建时间 + 优先级对应小时数（紧急24 / 高72 / 中120 / 低336）
 * 优先级无法识别或创建时间非法时返回 null（调用方回退为不限制）。
 * createdAt 支持：ISO 字符串（DB 口径）、Unix 秒 number（AI 接口口径）、Dayjs（提单基准）。
 */
export function getDeadlineRange(
  priority: string | null | undefined,
  createdAt: string | number | Dayjs | null | undefined,
): DeadlineRange | null {
  const key = normalizePriority(priority);
  if (!key) return null;
  let base: Dayjs;
  if (dayjs.isDayjs(createdAt)) {
    // 提单基准（提单弹窗传 dayjs()）：已是本地时区对象，原样使用
    base = createdAt;
  } else {
    // ISO 字符串（DB 口径，naive UTC）或 Unix 秒（AI 接口口径）：统一走 parseBackendDayjs 解析
    const parsed = parseDeadlineString(createdAt as string | number | null | undefined);
    if (!parsed) return null;
    base = parsed;
  }
  if (!base.isValid()) return null;
  const hours = PRIORITY_DEADLINE_HOURS[key];
  // 向上取整到下一个整点：09:20 → 10:00；已是整点（09:00）则保持 09:00 不变
  const min = base.minute(0).second(0).millisecond(0).add(base.minute() > 0 || base.second() > 0 || base.millisecond() > 0 ? 1 : 0, 'hour');
  const max = min.add(hours, 'hour');
  return { min, max, hours };
}

/** antd DatePicker disabledDate：仅允许 [min, max] 之间的日期。 */
export function makeDisabledDate(min: Dayjs, max: Dayjs) {
  return (current: Dayjs) =>
    !!current && (current.isBefore(min, 'day') || current.isAfter(max, 'day'));
}

/** antd DatePicker disabledTime：小时精度限制（当天边界按 min/max 的小时截断，分钟固定整点）。 */
export function makeDisabledTime(min: Dayjs, max: Dayjs) {
  return (current: Dayjs | null) => {
    const c = current ?? dayjs();
    const startHour = c.isSame(min, 'day') ? min.hour() : 0;
    const endHour = c.isSame(max, 'day') ? max.hour() : 23;
    const disabledHours: number[] = [];
    for (let h = 0; h < 24; h++) {
      if (h < startHour || h > endHour) disabledHours.push(h);
    }
    return {
      disabledHours: () => disabledHours,
      disabledMinutes: () =>
        Array.from({ length: 60 }, (_, m) => m).filter((m) => m !== 0),
    };
  };
}
