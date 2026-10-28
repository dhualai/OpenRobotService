// 空库首次灌数：用试做页旧种子生成 domain/org/skill 快照，再走 import-trial。
import {
  DISPATCH_OBSOLETE_SHELL_NAMES,
  DISPATCH_SKILL_SEEDS,
  INTERFACE_DETAIL_NAMES,
  dropInterfaceDetails,
  ensureSkillSeeds,
} from './dualTreeDispatchSkills';
import { DOMAIN_OBSOLETE_SKILL_NAMES, DOMAIN_SKILL_SEED_MAP } from './dualTreeDomainSkills';
import { applyOrgSeed, needsOrgSeed } from './dualTreeOrgSeed';

type NodeKind = 'domain' | 'org' | 'skill';
type OrgKind = 'company' | 'department';
type MountRole = 'member' | 'owner' | 'specialist' | 'leader';

interface BootNode {
  id: string;
  parentId: string | null;
  kind: NodeKind;
  orgKind?: OrgKind;
  name: string;
  description: string;
  vehicleModels: string;
}

interface BootMount {
  nodeId: string;
  userId: string;
  role: MountRole;
}

const STARTER_DOMAINS: { name: string; description: string }[] = [
  { name: '调度系统', description: '调度、任务、地图、交通与车端适配。六域里的 DISP。' },
  { name: '车体硬件', description: '机械、驱动、电气与安全件。六域里的 HW。' },
  { name: '车端软件', description: '定位、感知、运动控制与车载协议栈。六域里的 SW。' },
  { name: '网络通信', description: '现场无线、有线与链路质量。六域里的 NET。' },
  { name: '上游对接', description: 'WMS/MES、PLC/产线信号、许可与部署。六域里的 IF。' },
  { name: '现场环境', description: '地面、光照、障碍、温湿度粉尘。六域里的 ENV。' },
  { name: '摇人吧', description: '服务号、工单与平台自身问题。' },
  { name: '实施工具', description: '现场实施、部署和调试用的工具与流程。' },
  { name: '售前', description: '方案、选型、报价，以及交付前的咨询。' },
];

function uid(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 生成可交给 POST /resp-trees/import-trial 的试做快照 */
export function buildBootstrapSnapshot(): { nodes: BootNode[]; mounts: BootMount[] } {
  const starters: BootNode[] = STARTER_DOMAINS.map((item) => ({
    id: uid('dom'),
    parentId: null,
    kind: 'domain',
    name: item.name,
    description: item.description,
    vehicleModels: '',
  }));
  const makeSkill = (parentId: string, name: string, description: string): BootNode => ({
    id: uid('sk'),
    parentId,
    kind: 'skill',
    name,
    description,
    vehicleModels: '',
  });
  let seeded = ensureSkillSeeds(starters, '调度系统', DISPATCH_SKILL_SEEDS, makeSkill);
  seeded = dropInterfaceDetails(seeded, '调度系统', DISPATCH_OBSOLETE_SHELL_NAMES);
  Object.entries(DOMAIN_SKILL_SEED_MAP).forEach(([domainName, seeds]) => {
    seeded = ensureSkillSeeds(seeded, domainName, seeds, makeSkill);
    const obsolete = DOMAIN_OBSOLETE_SKILL_NAMES[domainName];
    if (obsolete?.length) seeded = dropInterfaceDetails(seeded, domainName, obsolete);
  });
  seeded = dropInterfaceDetails(seeded, '调度系统', INTERFACE_DETAIL_NAMES);

  let mounts: BootMount[] = [];
  if (needsOrgSeed(seeded)) {
    const applied = applyOrgSeed(
      seeded,
      mounts,
      (draft) => ({
        id: uid('org'),
        parentId: draft.parentId,
        kind: 'org' as const,
        orgKind: draft.orgKind,
        name: draft.name,
        description: draft.description || '',
        vehicleModels: draft.vehicleModels || '',
      }),
      (nodeId, userId, role) => ({ nodeId, userId, role }),
    );
    seeded = applied.nodes;
    mounts = applied.mounts;
  }
  return { nodes: seeded, mounts };
}
