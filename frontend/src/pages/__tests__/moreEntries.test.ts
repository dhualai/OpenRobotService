import { describe, expect, it } from 'vitest';

import { MORE_FUNCTION_ENTRIES } from '../admin/moreEntries';
import { KB_PORTAL_URL } from '@/shared/constants/kbPortal';

describe('后台管理「更多功能」入口清单', () => {
  it('只有四块内部路由入口，不含任何外部独立应用', () => {
    expect(MORE_FUNCTION_ENTRIES.map((e) => e.label)).toEqual([
      '项目管理',
      '数据资源',
      '日报周报',
      '其他',
    ]);
    expect(MORE_FUNCTION_ENTRIES.every((e) => !e.external)).toBe(true);
  });

  it('顺序与色调保持原有样子（智库入口收在「其他」里，不在这里占位）', () => {
    expect(MORE_FUNCTION_ENTRIES.map((e) => e.tone)).toEqual(['blue-1', 'blue-2', 'blue-3', 'blue-4']);
  });
});

describe('智库入口地址', () => {
  it('指向同源子路径 /kb/（与摇人吧同域，无跨域）', () => {
    expect(KB_PORTAL_URL).toContain('/kb/');
    expect(KB_PORTAL_URL.startsWith('http')).toBe(false);
  });
});