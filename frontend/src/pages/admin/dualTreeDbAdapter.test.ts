import { describe, expect, it } from 'vitest';
import type { RespTreePayload } from '@/api/respTree';
import {
  dedupeMounts,
  payloadsToTrial,
  resolveOrgParentId,
  trialKindOf,
} from './dualTreeDbAdapter';

function payload(
  kind: 'skill' | 'org',
  treeId: number,
  revision: number,
  nodes: RespTreePayload['nodes'],
  links: RespTreePayload['links'] = [],
): RespTreePayload {
  return {
    tree: { id: treeId, kind, name: kind, status: 'draft', revision },
    nodes,
    links,
  };
}

describe('dualTreeDbAdapter', () => {
  it('dedupeMounts 保留更高角色', () => {
    const mounts = dedupeMounts([
      { user_id: 'u1', node_id: 10, link_type: 'solid', role: 'member', source: 'manual' },
      { user_id: 'u1', node_id: 10, link_type: 'solid', role: 'owner', source: 'manual' },
      { user_id: 'u2', node_id: 10, link_type: 'solid', role: 'specialist', source: 'manual' },
    ]);
    expect(mounts).toEqual([
      { nodeId: 10, userId: 'u1', role: 'owner' },
      { nodeId: 10, userId: 'u2', role: 'specialist' },
    ]);
  });

  it('payloadsToTrial 合成逻辑域并把顶层组织挂到 skill 域 id', () => {
    const skill = payload('skill', 1, 3, [
      {
        id: 100, tree_id: 1, parent_id: null, name: '车体硬件', description: 'HW',
        keywords: [], anchor: '', owners: [], linked_node_id: null,
        node_kind: 'umbrella', org_kind: null, vehicle_models: '', sort_order: 1,
      },
      {
        id: 101, tree_id: 1, parent_id: 100, name: '驱动', description: '',
        keywords: [], anchor: '', owners: [], linked_node_id: null,
        node_kind: 'leaf', org_kind: null, vehicle_models: '', sort_order: 0,
      },
    ], [
      { user_id: 'eng1', node_id: 101, link_type: 'solid', role: 'specialist', source: 'manual' },
    ]);
    const org = payload('org', 2, 4, [
      {
        id: 200, tree_id: 2, parent_id: null, name: '车体硬件', description: 'HW',
        keywords: [], anchor: '', owners: [], linked_node_id: 100,
        node_kind: 'umbrella', org_kind: null, vehicle_models: '', sort_order: 1,
      },
      {
        id: 201, tree_id: 2, parent_id: 200, name: '中力', description: '',
        keywords: [], anchor: '', owners: [], linked_node_id: null,
        node_kind: 'org', org_kind: 'company', vehicle_models: 'XQE', sort_order: 0,
      },
      {
        id: 202, tree_id: 2, parent_id: 201, name: '硬件组', description: '',
        keywords: [], anchor: '', owners: [], linked_node_id: null,
        node_kind: 'org', org_kind: 'department', vehicle_models: '', sort_order: 0,
      },
    ], [
      { user_id: 'eng2', node_id: 201, link_type: 'solid', role: 'owner', source: 'manual' },
    ]);

    const view = payloadsToTrial(skill, org);
    expect(view.skillRevision).toBe(3);
    expect(view.orgRevision).toBe(4);
    expect(view.orgDomainBySkillDomain[100]).toBe(200);

    const domain = view.nodes.find((n) => n.kind === 'domain');
    expect(domain).toMatchObject({ id: 100, name: '车体硬件', orgDomainId: 200 });

    const company = view.nodes.find((n) => n.id === 201);
    expect(company).toMatchObject({
      kind: 'org', parentId: 100, orgKind: 'company', vehicleModels: 'XQE', tree: 'org',
    });

    const dept = view.nodes.find((n) => n.id === 202);
    expect(dept?.parentId).toBe(201);

    const skillLeaf = view.nodes.find((n) => n.id === 101);
    expect(skillLeaf).toMatchObject({ kind: 'skill', parentId: 100, tree: 'skill' });

    expect(view.mounts).toEqual(expect.arrayContaining([
      { nodeId: 101, userId: 'eng1', role: 'specialist' },
      { nodeId: 201, userId: 'eng2', role: 'owner' },
    ]));
  });

  it('resolveOrgParentId 把逻辑域映射到 org 域', () => {
    const nodes = [
      {
        id: 100, parentId: null, kind: 'domain' as const, name: '域', description: '',
        vehicleModels: '', tree: 'skill' as const, orgDomainId: 200,
      },
      {
        id: 201, parentId: 100, kind: 'org' as const, name: '公司', description: '',
        vehicleModels: '', tree: 'org' as const, orgKind: 'company' as const,
      },
    ];
    expect(resolveOrgParentId(100, { 100: 200 }, nodes)).toBe(200);
    expect(resolveOrgParentId(201, { 100: 200 }, nodes)).toBe(201);
  });

  it('trialKindOf', () => {
    expect(trialKindOf({
      id: 1, tree_id: 1, parent_id: null, name: 'a', description: '',
      keywords: [], anchor: '', owners: [], linked_node_id: null,
      node_kind: 'umbrella', org_kind: null, vehicle_models: '', sort_order: 0,
    })).toBe('domain');
    expect(trialKindOf({
      id: 2, tree_id: 2, parent_id: 1, name: 'b', description: '',
      keywords: [], anchor: '', owners: [], linked_node_id: null,
      node_kind: 'org', org_kind: 'company', vehicle_models: '', sort_order: 0,
    })).toBe('org');
  });
});
