import { describe, expect, it } from 'vitest';

import { KB_PORTAL_URL, MORE_FUNCTION_ENTRIES } from '../admin/moreEntries';

describe('后台管理「更多功能」入口清单', () => {
  it('包含智库（功能拓扑）入口，指向独立应用地址而非前端路由', () => {
    const entry = MORE_FUNCTION_ENTRIES.find((e) => e.kind === 'topology');
    expect(entry).toBeTruthy();
    expect(entry?.label).toBe('功能拓扑');
    // external 存在即走新标签页打开独立应用，不参与 react-router 导航
    expect(entry?.external).toBeTruthy();
    expect(entry?.external).toBe(KB_PORTAL_URL);
  });

  it('智库地址指向同源子路径 /kb/（与摇人吧同域，无跨域）', () => {
    expect(KB_PORTAL_URL).toContain('/kb/');
    expect(KB_PORTAL_URL.startsWith('http')).toBe(false);
  });

  it('原有四个入口不受影响：顺序与色调保持不变，功能拓扑只做追加', () => {
    const original = MORE_FUNCTION_ENTRIES.slice(0, 4);
    expect(original.map((e) => e.label)).toEqual(['项目管理', '数据资源', '日报周报', '其他']);
    expect(original.map((e) => e.tone)).toEqual(['blue-1', 'blue-2', 'blue-3', 'blue-4']);
    // 追加项排在最后
    expect(MORE_FUNCTION_ENTRIES[4].label).toBe('功能拓扑');
    expect(MORE_FUNCTION_ENTRIES[4].tone).toBe('blue-5');
  });
});