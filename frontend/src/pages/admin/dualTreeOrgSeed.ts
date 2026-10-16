// 组织树种子：本机试做页里各问题领域下的公司 / 部门，以及挂在组织上的人。
// 技能节点由 dualTreeDispatchSkills / dualTreeDomainSkills 生成，技能上的挂人由责任模块树推导，
// 所以这里只放「代码推不出来」的部分：组织节点 + 挂在组织上的人。
// 数据来源：2026-10-15 从本机浏览器 localStorage(dual-tree-trial-v2) 恢复出来的那份组织树
//（当时页面被 dual-tree-trial-v3 那份没有组织的树带偏，v2 里这棵还完整）。
export type OrgSeedRole = 'member' | 'owner' | 'specialist' | 'leader';
export type OrgSeedKind = 'company' | 'department';

export interface OrgSeedPerson {
  id: string;
  role: OrgSeedRole;
}

export interface OrgSeedNode {
  name: string;
  orgKind?: OrgSeedKind;
  people?: OrgSeedPerson[];
  children?: OrgSeedNode[];
}

export interface OrgSeedGroup {
  domain: string;
  orgs: OrgSeedNode[];
}

export const DUAL_TREE_ORG_SEED: OrgSeedGroup[] = [
  {
    domain: '调度系统',
    orgs: [
      {
        name: '智能规划研究院',
        orgKind: 'department',
        people: [
          { id: 'oD5oY3Zu0l5jFSOsorAqoTNrlleM', role: 'member' },
          { id: 'oD5oY3ZegPAIBfliE1PdQG3R6SU0', role: 'member' },
          { id: 'oD5oY3YT421uj6JL4cqc1QAU4V7U', role: 'member' },
          { id: 'oD5oY3YFm77vT_qe3Z9b0IpgKrLI', role: 'member' },
          { id: 'oD5oY3YF_JieCp7bXyCaFA26lsrs', role: 'member' },
          { id: 'oD5oY3WrfvAMoMi7STw8uFy4lHJY', role: 'member' },
          { id: 'oD5oY3X3zAm_chmq7XVtyhrbNjFk', role: 'member' },
          { id: 'oD5oY3UFIDX0JjxxINgJKPPdfdwA', role: 'owner' },
          { id: 'oD5oY3a6ru85QY_WObaZ4Lvye_XM', role: 'member' },
          { id: 'oD5oY3bb9zbatEt4xXZf5HBffJW0', role: 'member' },
          { id: 'oD5oY3bC57Jug8w_uzmAHB375NJI', role: 'member' },
          { id: 'oD5oY3cU84mPxIn6cQFyHt2vNUUc', role: 'member' },
          { id: 'oD5oY3d06jOMSf_bkd-1DjDkZByY', role: 'member' },
          { id: 'oD5oY3dkpTOSM4_eVMhgeAJwC7nY', role: 'member' },
          { id: 'oD5oY3dpnlcz0RxuUAcr94niKaO0', role: 'member' },
          { id: 'oD5oY3e8Lpa689OQHmGRV-kVcVIc', role: 'member' },
          { id: 'oD5oY3ehhsk124jvTqhpNSX6xLqo', role: 'member' },
          { id: 'oD5oY3erOs9Tku1jOpFCpASvNu28', role: 'member' },
          { id: 'oD5oY3f_XqVL75qkCns1hblJ-8UM', role: 'member' },
          { id: 'oD5oY3RNsDC5cN0igT7h-j2sdu9Q', role: 'member' },
          { id: 'oD5oY3S7gz7vt7c-unlV67_Yym18', role: 'member' },
          { id: 'oD5oY3ScbUCoqVgyVfYBnfEMwJa4', role: 'member' },
          { id: 'oD5oY3T0vqcFTr9act5aFFPD3v0E', role: 'member' },
          { id: 'oD5oY3UQttdWFOnN14pDx-R9z7vM', role: 'member' },
          { id: 'oD5oY3Vy77OFJhhuPoLQ-yUVlPZE', role: 'member' },
          { id: 'oD5oY3WmUBkYm1ea7fkLg9Igp4r0', role: 'member' },
        ],
      },
    ],
  },
  {
    domain: '车体硬件',
    orgs: [
      {
        name: '浙江中力机械股份有限公司',
        orgKind: 'company',
        children: [
          {
            name: '机器人事业部',
            orgKind: 'department',
            people: [
              { id: 'user_77d3ac3f', role: 'owner' },
            ],
          },
          {
            name: '前移式事业部',
            orgKind: 'department',
          },
          {
            name: '电动车事业部',
            orgKind: 'department',
          },
          {
            name: '电叉事业部',
          },
          {
            name: 'E车事业部',
          },
          {
            name: 'EZ事业部',
          },
        ],
      },
    ],
  },
  {
    domain: '车端软件',
    orgs: [
      {
        name: '浙江科钛机器人股份有限公司',
        orgKind: 'company',
      },
      {
        name: '浙江中力机械股份有限公司',
        orgKind: 'company',
        children: [
          {
            name: '智能移动研究院',
            orgKind: 'department',
          },
        ],
      },
    ],
  },
  {
    domain: '上游对接',
    orgs: [
      {
        name: 'DAS部',
        orgKind: 'department',
      },
    ],
  },
];

export interface OrgSeedNodeLike {
  id: string;
  parentId: string | null;
  kind: string;
  name: string;
}

export interface OrgSeedMountLike {
  nodeId: string;
  userId: string;
  role: string;
}

/** 树里一个组织节点都没有，说明本机那份组织树没了（清缓存 / 换浏览器 / 被别的 key 盖掉），该用种子兜底 */
export function needsOrgSeed(nodes: OrgSeedNodeLike[]): boolean {
  return !nodes.some((node) => node.kind === 'org');
}

/**
 * 把内置组织树并进现有节点：按域名找挂载点，同名组织直接复用，挂人只补不删。
 * 域不在树里就跳过那一组，不往别的域里硬塞。
 */
export function applyOrgSeed<TNode extends OrgSeedNodeLike, TMount extends OrgSeedMountLike>(
  nodes: TNode[],
  mounts: TMount[],
  makeNode: (parentId: string, name: string, orgKind?: OrgSeedKind) => TNode,
  makeMount: (nodeId: string, userId: string, role: OrgSeedRole) => TMount,
): { nodes: TNode[]; mounts: TMount[] } {
  const nextNodes = [...nodes];
  const addedMounts: TMount[] = [];
  const hasMount = (nodeId: string, userId: string, role: string) => (
    mounts.some((m) => m.nodeId === nodeId && m.userId === userId && m.role === role)
    || addedMounts.some((m) => m.nodeId === nodeId && m.userId === userId && m.role === role)
  );

  const addList = (parentId: string, list: OrgSeedNode[]) => {
    list.forEach((seed) => {
      const found = nextNodes.find(
        (node) => node.kind === 'org' && node.parentId === parentId && node.name === seed.name,
      );
      const target = found || makeNode(parentId, seed.name, seed.orgKind);
      if (!found) nextNodes.push(target);
      (seed.people || []).forEach((person) => {
        if (!hasMount(target.id, person.id, person.role)) {
          addedMounts.push(makeMount(target.id, person.id, person.role));
        }
      });
      if (seed.children?.length) addList(target.id, seed.children);
    });
  };

  DUAL_TREE_ORG_SEED.forEach((group) => {
    const domain = nextNodes.find((node) => node.kind === 'domain' && node.name === group.domain);
    if (!domain) return;
    addList(domain.id, group.orgs);
  });
  return { nodes: nextNodes, mounts: [...mounts, ...addedMounts] };
}
