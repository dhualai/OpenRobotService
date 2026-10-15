import { describe, expect, it } from 'vitest';
import {
  DISPATCH_SKILL_SEEDS,
  INTERFACE_DETAIL_NAMES,
  applyLeafSpecialists,
  dropInterfaceDetails,
  dropRolledSpecialists,
  ensureSkillSeeds,
  type SkillNodeLike,
} from './dualTreeDispatchSkills';

function node(partial: SkillNodeLike): SkillNodeLike {
  return partial;
}

describe('调度系统技能树', () => {
  it('技能是领域能力，界面功能收成技能上的人', () => {
    let n = 0;
    const domain = node({ id: 'dom', parentId: null, kind: 'domain', name: '调度系统', description: '' });
    const monitor = node({ id: 'mon', parentId: 'dom', kind: 'skill', name: '监控', description: '界面' });
    const leaf = node({ id: 'plan', parentId: 'mon', kind: 'skill', name: '任务长期路径规划中', description: '界面功能' });
    const hardware = node({ id: 'hw', parentId: 'other', kind: 'skill', name: '机械结构', description: '已写好的车体技能' });
    const placed = ensureSkillSeeds(
      [domain, monitor, leaf, hardware],
      '调度系统',
      DISPATCH_SKILL_SEEDS,
      (parentId, name, description) => node({ id: `sk${n += 1}`, parentId, kind: 'skill', name, description }),
    );
    const kept = dropInterfaceDetails(placed, '调度系统', INTERFACE_DETAIL_NAMES);
    const algo = kept.find((item) => item.name === '调度算法');
    const route = kept.find((item) => item.name === '路径规划');
    const shallow = kept.find((item) => item.name === '单车路径规划');
    const parent = kept.find((item) => item.name === '调度统筹');
    expect(parent?.parentId).toBe('dom');
    expect(algo?.parentId).toBe(parent?.id);
    expect(route?.parentId).toBe(algo?.id);
    expect(shallow?.parentId).toBe(route?.id);
    const assign = kept.find((item) => item.name === '任务分配');
    const match = kept.find((item) => item.name === '业务分配');
    const map = kept.find((item) => item.name === '地图处理');
    const locate = kept.find((item) => item.name === '上轨定位');
    expect(assign?.parentId).toBe(algo?.id);
    expect(match?.parentId).toBe(assign?.id);
    expect(locate?.parentId).toBe(map?.id);
    expect(kept.some((item) => item.name === '最近车与优先级')).toBe(false);
    expect(kept.some((item) => item.name === '匈牙利匹配')).toBe(false);
    const central = kept.find((item) => item.name === '中心化多车路径规划');
    expect(central?.parentId).toBe(route?.id);
    const adapter = kept.find((item) => item.name === '科钛适配');
    const vehicleLink = kept.find((item) => item.name === '车端对接');
    const model = kept.find((item) => item.name === '车型差异');
    const storage = kept.find((item) => item.name === '库位与载具');
    expect(adapter?.parentId).toBe(vehicleLink?.id);
    expect(model?.parentId).toBe(storage?.id);
    expect(kept.some((item) => item.name === '多厂家并存')).toBe(false);
    expect(kept.some((item) => item.name === '任务长期路径规划中')).toBe(false);
    expect(kept.some((item) => item.name === '监控')).toBe(false);
    expect(kept.filter((item) => item.name === '机械结构')).toHaveLength(1);
    const mounts = applyLeafSpecialists(
      kept,
      [],
      [{ name: '单车路径规划', userId: 'u-xu' }],
      '调度系统',
      (nodeId, userId) => ({ nodeId, userId, role: 'specialist' }),
    );
    expect(mounts).toEqual([{ nodeId: shallow?.id, userId: 'u-xu', role: 'specialist' }]);
    const moved = applyLeafSpecialists(
      kept,
      dropRolledSpecialists(
        kept,
        [{ nodeId: algo?.id || '', userId: 'u-wang', role: 'specialist' }, { nodeId: 'hw', userId: 'u-wang', role: 'leader' }],
        ['u-wang'],
        '调度系统',
      ),
      [{ name: '业务分配', userId: 'u-wang' }],
      '调度系统',
      (nodeId, userId) => ({ nodeId, userId, role: 'specialist' }),
    );
    expect(moved).toEqual([
      { nodeId: 'hw', userId: 'u-wang', role: 'leader' },
      { nodeId: match?.id, userId: 'u-wang', role: 'specialist' },
    ]);
  });
});
