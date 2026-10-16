import { describe, expect, it } from 'vitest';
import {
  DUAL_TREE_ORG_SEED,
  applyOrgSeed,
  needsOrgSeed,
  type OrgSeedKind,
  type OrgSeedNodeDraft,
  type OrgSeedNodeLike,
  type OrgSeedRole,
} from './dualTreeOrgSeed';

interface N extends OrgSeedNodeLike {
  kind: string;
  orgKind?: OrgSeedKind;
  vehicleModels?: string;
  description?: string;
}

interface M {
  nodeId: string;
  userId: string;
  role: string;
}

let seq = 0;
const makeNode = (draft: OrgSeedNodeDraft): N => {
  seq += 1;
  return {
    id: `org_${seq}`,
    parentId: draft.parentId,
    kind: 'org',
    name: draft.name,
    orgKind: draft.orgKind,
    vehicleModels: draft.vehicleModels,
    description: draft.description,
  };
};
const makeMount = (nodeId: string, userId: string, role: OrgSeedRole): M => ({ nodeId, userId, role });

const domains: N[] = [
  { id: 'd_disp', parentId: null, kind: 'domain', name: '调度系统' },
  { id: 'd_hw', parentId: null, kind: 'domain', name: '车体硬件' },
  { id: 'd_sw', parentId: null, kind: 'domain', name: '车端软件' },
  { id: 'd_up', parentId: null, kind: 'domain', name: '上游对接' },
];

describe('双树组织树种子', () => {
  it('本机树里没有组织节点时才兜底', () => {
    expect(needsOrgSeed(domains)).toBe(true);
    expect(needsOrgSeed([...domains, { id: 'o1', parentId: 'd_hw', kind: 'org', name: '已有公司' }])).toBe(false);
  });

  it('带出车体硬件的 7 个组织，并保留层级（公司下面挂事业部）', () => {
    const { nodes } = applyOrgSeed(domains, [], makeNode, makeMount);
    const hw = nodes.filter((n) => n.kind === 'org' && n.parentId === 'd_hw');
    expect(hw.map((n) => n.name)).toEqual(['浙江中力机械股份有限公司']);
    const company = hw[0];
    const depts = nodes.filter((n) => n.kind === 'org' && n.parentId === company.id).map((n) => n.name);
    expect(depts).toEqual(['机器人事业部', '前移式事业部', '电动车事业部', '电叉事业部', 'E车事业部', 'EZ事业部']);
    expect(nodes.filter((n) => n.kind === 'org')).toHaveLength(12);
  });

  it('负责车型与职责描述一起带回来', () => {
    const { nodes } = applyOrgSeed(domains, [], makeNode, makeMount);
    const dept = (name: string) => nodes.find((n) => n.name === name && n.parentId !== 'd_hw')!;
    expect(nodes.find((n) => n.name === '前移式事业部')!.vehicleModels).toBe('XQE122');
    expect(dept('电动车事业部').vehicleModels).toBe('XSC121、XS081、XSC201、XCS151、EXP20、XPL201P');
    expect(dept('EZ事业部').vehicleModels).toBe('EXP15、XCD101、XPG151、XFC001');
    const robotDept = nodes.find((n) => n.name === '机器人事业部')!;
    expect(robotDept.description).toContain('【负责】车体硬件');
    expect(robotDept.description).toContain('【不负责】调度系统');
    expect(dept('电叉事业部').vehicleModels).toBe('XFO151');
  });

  it('组织上的人一起带回来，且只补不删', () => {
    const existed: M[] = [{ nodeId: 'd_disp', userId: 'u-keep', role: 'member' }];
    const { nodes, mounts } = applyOrgSeed(domains, existed, makeNode, makeMount);
    const institute = nodes.find((n) => n.name === '智能规划研究院')!;
    const mine = mounts.filter((m) => m.nodeId === institute.id);
    expect(mine.length).toBeGreaterThan(20);
    expect(mine.some((m) => m.role === 'owner')).toBe(true);
    expect(mounts.find((m) => m.userId === 'u-keep')).toEqual({ nodeId: 'd_disp', userId: 'u-keep', role: 'member' });

    const hwCompany = nodes.find((n) => n.name === '浙江中力机械股份有限公司' && n.parentId === 'd_hw')!;
    const robotDept = nodes.find((n) => n.name === '机器人事业部')!;
    expect(mounts.filter((m) => m.nodeId === hwCompany.id)).toHaveLength(0);
    expect(mounts.find((m) => m.nodeId === robotDept.id && m.role === 'owner')).toEqual({ nodeId: robotDept.id, userId: 'user_77d3ac3f', role: 'owner' });
  });

  it('重复应用不会长出第二份组织，也不会重复挂人', () => {
    const once = applyOrgSeed(domains, [], makeNode, makeMount);
    const twice = applyOrgSeed(once.nodes, once.mounts, makeNode, makeMount);
    expect(twice.nodes.filter((n) => n.kind === 'org')).toHaveLength(12);
    expect(twice.mounts).toHaveLength(once.mounts.length);
  });

  it('同名组织已存在时复用它的 id，不新建重复项', () => {
    const existing: N = { id: 'd_hw_company', parentId: 'd_hw', kind: 'org', name: '浙江中力机械股份有限公司', orgKind: 'company' };
    const { nodes, mounts } = applyOrgSeed([...domains, existing], [], makeNode, makeMount);
    expect(nodes.filter((n) => n.name === '浙江中力机械股份有限公司')).toHaveLength(2); // 车体硬件 + 车端软件各一个
    expect(nodes.filter((n) => n.name === '浙江中力机械股份有限公司' && n.parentId === 'd_hw')).toHaveLength(1);
    const depts = nodes.filter((n) => n.parentId === 'd_hw_company').map((n) => n.name);
    expect(depts).toHaveLength(6);
    expect(mounts.find((m) => m.nodeId === 'd_hw_company' || m.userId === 'user_77d3ac3f')?.nodeId)
      .toBe(nodes.find((n) => n.name === '机器人事业部')!.id);
  });

  it('域不在树里就跳过那一组，不往别的域里硬塞', () => {
    const only = domains.filter((d) => d.name === '调度系统');
    const { nodes } = applyOrgSeed(only, [], makeNode, makeMount);
    const orgs = nodes.filter((n) => n.kind === 'org');
    expect(orgs.map((n) => n.name)).toEqual(['智能规划研究院']);
    expect(DUAL_TREE_ORG_SEED.some((g) => g.domain === '车体硬件')).toBe(true);
  });
});
