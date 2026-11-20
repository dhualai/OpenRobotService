// 公司工作日历 API —— 工作时间 + 法定节假日/调休补班
// 用于「按接收人工作时间推送」：决定工单逾期/临期提醒在哪个时区、哪几个时段发出。
// 范围：仅公司级（不做部门/用户级覆盖），工作时段单段，不支持夜班。
import { createRequest } from './client';
import API_CONFIG from '@/config/api';

const adminRequest = createRequest(API_CONFIG.ADMIN.BASE_URL, '后台管理');

/** 后台「其他」入口与接口共用的权限码（常量放这里，避免入口页为拿常量加载整页） */
export const PERM_WORK_CALENDAR = 'frontend:admin:work-calendar:manage';

export interface WorkCalendar {
  company_id: string;
  company_name: string;
  /** 工作开始 HH:MM（接收人本地时间） */
  work_start: string;
  /** 工作结束 HH:MM */
  work_end: string;
  /** 工作日，如 "1,2,3,4,5"（1=周一） */
  workdays: string;
}

export interface WorkCalendarUpdate {
  work_start?: string;
  work_end?: string;
  workdays?: string;
}

export interface Holiday {
  id: string;
  /** yyyy-MM-dd */
  date: string;
  name: string | null;
  /** holiday=放假；workday_override=调休补班 */
  type: 'holiday' | 'workday_override';
}

export interface HolidayInput {
  date: string;
  name?: string;
  type?: 'holiday' | 'workday_override';
}

export function getWorkCalendar(companyId: string): Promise<WorkCalendar> {
  return adminRequest<WorkCalendar>(`/companies/${companyId}/work-calendar`, { skipCache: true });
}

export function updateWorkCalendar(companyId: string, data: WorkCalendarUpdate): Promise<unknown> {
  return adminRequest(`/companies/${companyId}/work-calendar`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function listHolidays(companyId: string, year?: number): Promise<Holiday[]> {
  const query = year ? `?year=${year}` : '';
  return adminRequest<Holiday[]>(`/companies/${companyId}/holidays${query}`, { skipCache: true });
}

export function createHoliday(companyId: string, data: HolidayInput): Promise<unknown> {
  return adminRequest(`/companies/${companyId}/holidays`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

/** 批量导入（同日期覆盖） */
export function batchUpsertHolidays(companyId: string, items: HolidayInput[]): Promise<{ created: number; updated: number }> {
  return adminRequest(`/companies/${companyId}/holidays/batch`, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
}

export function deleteHoliday(companyId: string, holidayId: string): Promise<unknown> {
  return adminRequest(`/companies/${companyId}/holidays/${holidayId}`, { method: 'DELETE' });
}

/** ISO 星期 → 中文名（1=周一） */
export const WEEKDAY_LABELS: { value: number; label: string }[] = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
];

export function parseWorkdays(workdays: string): number[] {
  return (workdays || '')
    .split(',')
    .map((s) => Number(s.trim()))
    .filter((n) => n >= 1 && n <= 7);
}

export function formatWorkdays(days: number[]): string {
  return [...days].sort((a, b) => a - b).join(',');
}
