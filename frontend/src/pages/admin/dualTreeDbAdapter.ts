// 责任双树 DB payload ↔ 试做页混合视图。
// UI 用 skill 树上的域 id 作「逻辑域」；org 树同名域通过 linked_node_id / 名称对齐，
// 顶层组织的 parentId 在 UI 上显示为逻辑域 id（库里仍挂 org 域 umbrella）。
import type { RespLinkDto, RespNodeDto, RespTreePayload } from '@/api/respTree';

export type TrialNodeKind = 'domain' | 'org' | 'skill';
export type TrialOrgKind = 'company' | 'department';
export type TrialMountRole = 'member' | 'owner' | 'specialist' | 'leader';

export interface TrialNode {
  id: number;
  parentId: number | null;
  kind: TrialNodeKind;
  orgKind?: TrialOrgKind;
  name: string;
  description: string;
  vehicleModels: string;
  /** 库树：skill / org */
  tree: 'skill' | 'org';
  /** org 树域节点 id（仅 kind=domain 有；新建域后两边各一条） */
  orgDomainId?: number;
}

export interface TrialMount {
  nodeId: number;
  userId: string;
  role: TrialMountRole;
}

export interface TrialView {
  nodes: TrialNode[];
  mounts: TrialMount[];
  skillTreeId: number;
  orgTreeId: number;
  skillRevision: number;
  orgRevision: number;
  /** skill 域 id → org 域 id */
  orgDomainBySkillDomain: Record<number, number>;
}

const ROLE_RANK: Record<string, number> = {
  leader: 4,
  owner: 3,
  specialist: 2,
  member: 1,
};

function asRole(raw: string): TrialMountRole {
  if (raw === 'leader' || raw === 'owner' || raw === 'specialist' || raw === 'member') {
    return raw;
  }
  return 'member';
}

function asOrgKind(raw: string | null | undefined): TrialOrgKind | undefined {
  if (raw === 'company' || raw === 'department') return raw;
  return undefined;
}

/** 同人同节点多角色：保留优先级更高的（与 migrate 一致） */
export function dedupeMounts(links: RespLinkDto[]): TrialMount[] {
  const picked = new Map<string, TrialMount>();
  links.forEach((link) => {
    const role = asRole(String(link.role || 'member'));
    const key = `${link.user_id}::${link.node_id}`;
    const prev = picked.get(key);
    if (!prev || (ROLE_RANK[role] || 0) > (ROLE_RANK[prev.role] || 0)) {
      picked.set(key, { nodeId: link.node_id, userId: link.user_id, role });
    }
  });
  return [...picked.values()];
}

/**
 * 两棵库树 → 试做页混合视图。
 * 域只展示 skill 侧 umbrella；org 侧 umbrella 记入 orgDomainBySkillDomain。
 */
export function payloadsToTrial(skill: RespTreePayload, org: RespTreePayload): TrialView {
  const skillUmbrellas = skill.nodes.filter((n) => n.node_kind === 'umbrella');
  const orgUmbrellas = org.nodes.filter((n) => n.node_kind === 'umbrella');

  const orgDomainBySkillDomain: Record<number, number> = {};
  const orgUmbrellaToSkill = new Map<number, number>();

  orgUmbrellas.forEach((ou) => {
    let skillId = ou.linked_node_id || 0;
    if (!skillId) {
      const hit = skillUmbrellas.find((su) => su.name === ou.name);
      skillId = hit?.id || 0;
    }
    if (skillId) {
      orgDomainBySkillDomain[skillId] = ou.id;
      orgUmbrellaToSkill.set(ou.id, skillId);
    }
  });

  const nodes: TrialNode[] = [];

  skillUmbrellas.forEach((n) => {
    nodes.push({
      id: n.id,
      parentId: null,
      kind: 'domain',
      name: n.name,
      description: n.description || '',
      vehicleModels: n.vehicle_models || '',
      tree: 'skill',
      orgDomainId: orgDomainBySkillDomain[n.id],
    });
  });

  skill.nodes.forEach((n) => {
    if (n.node_kind === 'umbrella') return;
    nodes.push({
      id: n.id,
      parentId: n.parent_id,
      kind: 'skill',
      name: n.name,
      description: n.description || '',
      vehicleModels: n.vehicle_models || '',
      tree: 'skill',
    });
  });

  org.nodes.forEach((n) => {
    if (n.node_kind === 'umbrella') return;
    let parentId = n.parent_id;
    if (parentId != null && orgUmbrellaToSkill.has(parentId)) {
      parentId = orgUmbrellaToSkill.get(parentId)!;
    }
    nodes.push({
      id: n.id,
      parentId,
      kind: 'org',
      orgKind: asOrgKind(n.org_kind),
      name: n.name,
      description: n.description || '',
      vehicleModels: n.vehicle_models || '',
      tree: 'org',
    });
  });

  const mounts = dedupeMounts([...skill.links, ...org.links]);

  return {
    nodes,
    mounts,
    skillTreeId: skill.tree.id,
    orgTreeId: org.tree.id,
    skillRevision: skill.tree.revision,
    orgRevision: org.tree.revision,
    orgDomainBySkillDomain,
  };
}

/** 写库时：UI 逻辑域 id → org 树真实父 id（顶层组织挂到 org 域） */
export function resolveOrgParentId(
  uiParentId: number | null,
  orgDomainBySkillDomain: Record<number, number>,
  nodes: TrialNode[],
): number | null {
  if (uiParentId == null) return null;
  const parent = nodes.find((n) => n.id === uiParentId);
  if (parent?.kind === 'domain') {
    return orgDomainBySkillDomain[uiParentId] ?? null;
  }
  return uiParentId;
}

export function treeForNode(node: TrialNode): 'skill' | 'org' {
  if (node.kind === 'org') return 'org';
  if (node.kind === 'skill') return 'skill';
  return 'skill';
}

export function revisionForTree(
  tree: 'skill' | 'org',
  skillRevision: number,
  orgRevision: number,
): number {
  return tree === 'skill' ? skillRevision : orgRevision;
}

/** 从 DB 节点 DTO 粗判试做 kind（单测 / 调试用） */
export function trialKindOf(node: RespNodeDto): TrialNodeKind {
  if (node.node_kind === 'umbrella') return 'domain';
  if (node.node_kind === 'org') return 'org';
  return 'skill';
}
