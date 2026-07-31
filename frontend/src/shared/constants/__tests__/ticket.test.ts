import { describe, it, expect } from 'vitest';
import {
  STATUS_DISPLAY_MAP,
  STATUS_VALUE_MAP,
  PRIORITY_VALUE_MAP,
  PRIORITY_DISPLAY_MAP,
  TICKET_TYPE_DISPLAY_MAP,
  TICKET_TYPE_VALUE_MAP,
  normalizeStatus,
  isTerminalTicketStatus,
  canUrgeTicket,
  canReportTicket,
  canCancelTicket,
} from '../ticket';

describe('STATUS_DISPLAY_MAP', () => {
  it('should map all statuses to Chinese display', () => {
    expect(STATUS_DISPLAY_MAP['new']).toBe('新建');
    expect(STATUS_DISPLAY_MAP['in_progress']).toBe('进行中');
    expect(STATUS_DISPLAY_MAP['pending']).toBe('待处理');
    expect(STATUS_DISPLAY_MAP['resolved']).toBe('已解决');
    expect(STATUS_DISPLAY_MAP['closed']).toBe('已关闭');
  });
});

describe('STATUS_VALUE_MAP', () => {
  it('should map Chinese display back to values', () => {
    expect(STATUS_VALUE_MAP['新建']).toBe('new');
    expect(STATUS_VALUE_MAP['进行中']).toBe('in_progress');
    expect(STATUS_VALUE_MAP['待处理']).toBe('pending');
    expect(STATUS_VALUE_MAP['已解决']).toBe('resolved');
    expect(STATUS_VALUE_MAP['已关闭']).toBe('closed');
  });
});

describe('PRIORITY maps', () => {
  it('should map priority values to display', () => {
    expect(PRIORITY_DISPLAY_MAP['low']).toBe('低');
    expect(PRIORITY_DISPLAY_MAP['medium']).toBe('中');
    expect(PRIORITY_DISPLAY_MAP['high']).toBe('高');
    expect(PRIORITY_DISPLAY_MAP['urgent']).toBe('紧急');
  });

  it('should map Chinese priority to value', () => {
    expect(PRIORITY_VALUE_MAP['低']).toBe('low');
    expect(PRIORITY_VALUE_MAP['中']).toBe('medium');
    expect(PRIORITY_VALUE_MAP['高']).toBe('high');
    expect(PRIORITY_VALUE_MAP['紧急']).toBe('urgent');
  });
});

describe('TICKET_TYPE maps', () => {
  it('should map ticket types to display', () => {
    expect(TICKET_TYPE_DISPLAY_MAP['bug']).toBe('缺陷');
    expect(TICKET_TYPE_DISPLAY_MAP['feature']).toBe('功能');
    expect(TICKET_TYPE_DISPLAY_MAP['support']).toBe('支持');
    expect(TICKET_TYPE_DISPLAY_MAP['problem']).toBe('问题');
    expect(TICKET_TYPE_DISPLAY_MAP['other']).toBe('其他');
  });

  it('should map Chinese ticket type to value', () => {
    expect(TICKET_TYPE_VALUE_MAP['缺陷']).toBe('bug');
    expect(TICKET_TYPE_VALUE_MAP['功能']).toBe('feature');
    expect(TICKET_TYPE_VALUE_MAP['支持']).toBe('support');
    expect(TICKET_TYPE_VALUE_MAP['问题']).toBe('problem');
    expect(TICKET_TYPE_VALUE_MAP['其他']).toBe('other');
  });
});

describe('normalizeStatus', () => {
  it('should normalize known statuses', () => {
    expect(normalizeStatus('new')).toBe('新建');
    expect(normalizeStatus('in_progress')).toBe('进行中');
    expect(normalizeStatus('closed')).toBe('已关闭');
  });

  it('should normalize case-insensitively', () => {
    expect(normalizeStatus('NEW')).toBe('新建');
    expect(normalizeStatus('In_Progress')).toBe('进行中');
    expect(normalizeStatus('Closed')).toBe('已关闭');
  });

  it('should return default for empty/null input', () => {
    expect(normalizeStatus('')).toBe('新建');
    // @ts-expect-error testing edge case
    expect(normalizeStatus(null)).toBe('新建');
    // @ts-expect-error testing edge case
    expect(normalizeStatus(undefined)).toBe('新建');
  });

  it('should return original value for unknown status', () => {
    expect(normalizeStatus('unknown_status')).toBe('unknown_status');
    expect(normalizeStatus('weird-value')).toBe('weird-value');
  });
});

describe('工单操作状态约束（催办/上报/撤回）', () => {
  it('终态（已解决/已取消/已关闭）判定', () => {
    expect(isTerminalTicketStatus('resolved')).toBe(true);
    expect(isTerminalTicketStatus('canceled')).toBe(true);
    expect(isTerminalTicketStatus('cancelled')).toBe(true);
    expect(isTerminalTicketStatus('closed')).toBe(true);
    expect(isTerminalTicketStatus('CLOSED')).toBe(true);
    expect(isTerminalTicketStatus('new')).toBe(false);
    expect(isTerminalTicketStatus('pending')).toBe(false);
    expect(isTerminalTicketStatus('in_progress')).toBe(false);
    expect(isTerminalTicketStatus('')).toBe(false);
    expect(isTerminalTicketStatus(null)).toBe(false);
    expect(isTerminalTicketStatus(undefined)).toBe(false);
  });

  it('催办：新建/待处理可用，处理中禁用，终态禁用', () => {
    expect(canUrgeTicket('new')).toBe(true);
    expect(canUrgeTicket('pending')).toBe(true);
    // 历史值：待派单/已派单按「新建/待处理」处理
    expect(canUrgeTicket('pending_dispatch')).toBe(true);
    expect(canUrgeTicket('dispatched')).toBe(true);
    expect(canUrgeTicket('')).toBe(true);
    expect(canUrgeTicket('in_progress')).toBe(false);
    expect(canUrgeTicket('resolved')).toBe(false);
    expect(canUrgeTicket('canceled')).toBe(false);
    expect(canUrgeTicket('closed')).toBe(false);
  });

  it('上报：仅处理中可用', () => {
    expect(canReportTicket('in_progress')).toBe(true);
    expect(canReportTicket('new')).toBe(false);
    expect(canReportTicket('pending')).toBe(false);
    expect(canReportTicket('resolved')).toBe(false);
    expect(canReportTicket('canceled')).toBe(false);
    expect(canReportTicket('closed')).toBe(false);
  });

  it('撤回：新建/待处理可用，其余禁用（规则同催办）', () => {
    expect(canCancelTicket('new')).toBe(true);
    expect(canCancelTicket('pending')).toBe(true);
    expect(canCancelTicket('dispatched')).toBe(true);
    expect(canCancelTicket('in_progress')).toBe(false);
    expect(canCancelTicket('resolved')).toBe(false);
    expect(canCancelTicket('canceled')).toBe(false);
    expect(canCancelTicket('closed')).toBe(false);
  });
});
