import { describe, expect, it } from 'vitest';
import { dropInterfaceDetails, ensureSkillSeeds, type SkillNodeLike } from './dualTreeDispatchSkills';
import {
  DOMAIN_OBSOLETE_SKILL_NAMES,
  DOMAIN_SKILL_SEED_MAP,
  HARDWARE_SKILL_SEEDS,
} from './dualTreeDomainSkills';

function node(partial: SkillNodeLike): SkillNodeLike {
  return partial;
}

describe('六域技能树种子', () => {
  it('车体硬件只有统筹下的粗模块，细节在详情不另开叶子', () => {
    const root = HARDWARE_SKILL_SEEDS[0];
    expect(root.name).toBe('车体统筹');
    expect(root.children?.map((c) => c.name)).toEqual([
      '机械结构', '驱动行走', '电气电源', '安全硬件',
    ]);
    root.children?.forEach((child) => {
      expect(child.children).toBeUndefined();
      expect(child.description.length).toBeGreaterThan(20);
    });
  });

  it('补种后清掉过碎旧叶子，组织节点不受影响', () => {
    let n = 0;
    const domain = node({ id: 'd0', parentId: null, kind: 'domain', name: '车体硬件', description: '' });
    const org = node({ id: 'org1', parentId: 'd0', kind: 'org', name: '某公司', description: '' });
    const crumb = node({ id: 'old', parentId: 'd0', kind: 'skill', name: '驱动电机', description: '碎' });
    let nodes = ensureSkillSeeds(
      [domain, org, crumb],
      '车体硬件',
      HARDWARE_SKILL_SEEDS,
      (parentId, name, description) => node({ id: `sk${n += 1}`, parentId, kind: 'skill', name, description }),
    );
    nodes = dropInterfaceDetails(nodes, '车体硬件', DOMAIN_OBSOLETE_SKILL_NAMES['车体硬件']);
    expect(nodes.some((item) => item.name === '驱动电机')).toBe(false);
    expect(nodes.some((item) => item.name === '驱动行走')).toBe(true);
    expect(nodes.find((item) => item.id === 'org1')?.name).toBe('某公司');
  });

  it('各非调度域都能挂上粗模块', () => {
    let n = 0;
    let nodes: SkillNodeLike[] = Object.keys(DOMAIN_SKILL_SEED_MAP).map((name, i) =>
      node({ id: `d${i}`, parentId: null, kind: 'domain', name, description: '' }),
    );
    Object.entries(DOMAIN_SKILL_SEED_MAP).forEach(([domainName, seeds]) => {
      nodes = ensureSkillSeeds(
        nodes,
        domainName,
        seeds,
        (parentId, name, description) => node({ id: `sk${n += 1}`, parentId, kind: 'skill', name, description }),
      );
    });
    expect(nodes.find((item) => item.name === '机械结构')).toBeTruthy();
    expect(nodes.find((item) => item.name === '现场无线')).toBeTruthy();
    expect(nodes.find((item) => item.name === '地面通道')).toBeTruthy();
  });
});
