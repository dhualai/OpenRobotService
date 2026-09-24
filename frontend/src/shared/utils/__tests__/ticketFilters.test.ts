// 「待我处理」（relevance='mine'）代他人提单分支的口径回归：
// 被代理人只在「已确认跟进（acknowledged）」时才看得到 resolved 单，
// 避免 pending / declined 的被代理人「看得见工单、点不到按钮」。
import { describe, it, expect } from 'vitest';

import { buildRelevanceFilters } from '../ticketFilters';
import type { TicketFilterCondition } from '../ticketFilters';

type Cond = TicketFilterCondition;

/** 递归展平 or/and 树，便于在分支内部任意层级查找条件。 */
const flatten = (conds: Cond[]): Cond[] =>
  conds.flatMap((c) => [c, ...flatten(c.and ?? []), ...flatten(c.or ?? [])]);

/** 分支内（含嵌套 or/and）是否存在指定 field（可同时校验 value）。 */
const hasCond = (conds: Cond[], field: string, value?: unknown): boolean =>
  flatten(conds).some((c) => c.field === field && (value === undefined || c.value === value));

/** 取顶层 [{ or: [...] }] 里的各个分支。 */
const branchesOf = (conds: Cond[]): Cond[] => conds.flatMap((c) => c.or ?? []);

const WORKING_STATUSES = ['new', 'in_progress', 'pending'];

describe('buildRelevanceFilters', () => {
  it('global：不做任何相关性限制', () => {
    expect(buildRelevanceFilters('global', 'u1', ['p1'])).toEqual([]);
  });

  it('mine 缺 username：回退为项目维度，不退化成全量', () => {
    expect(buildRelevanceFilters('mine', '', ['p1', 'p2'])).toEqual([
      {
        or: [
          { field: 'projectId', op: 'eq', value: 'p1' },
          { field: 'projectId', op: 'eq', value: 'p2' },
        ],
      },
    ]);
  });

  it('mine：被代理人只在 acknowledged 时才看到 resolved 单（收紧「看得见无按钮」）', () => {
    const branches = branchesOf(buildRelevanceFilters('mine', 'u1', []));

    const principalResolved = branches.filter(
      (b) => hasCond([b], 'principalBy') && hasCond([b], 'status', 'resolved'),
    );

    // 恰好一个 resolved 分支，且必须带 proxyRelationStatus=acknowledged
    expect(principalResolved).toHaveLength(1);
    expect(hasCond([principalResolved[0]], 'proxyRelationStatus', 'acknowledged')).toBe(true);

    // 反向断言：不存在「principalBy + resolved 且不限关系状态」的宽松分支
    expect(
      branches.filter(
        (b) =>
          hasCond([b], 'principalBy') &&
          hasCond([b], 'status', 'resolved') &&
          !hasCond([b], 'proxyRelationStatus'),
      ),
    ).toHaveLength(0);
  });

  it('mine：被代理人处理中的单不限关系状态（pending 需我确认、acknowledged 我协办）', () => {
    const branches = branchesOf(buildRelevanceFilters('mine', 'u1', []));

    const principalWorking = branches.filter(
      (b) =>
        hasCond([b], 'principalBy') &&
        WORKING_STATUSES.every((s) => hasCond([b], 'status', s)) &&
        !hasCond([b], 'proxyRelationStatus'),
    );

    expect(principalWorking).toHaveLength(1);
  });

  it('mine：回合协商分支保留——接单人刚改过 step 且未协商一致', () => {
    const branches = branchesOf(buildRelevanceFilters('mine', 'u1', []));

    // 提单人侧
    expect(
      branches.some(
        (b) =>
          hasCond([b], 'createdBy', 'u1') &&
          hasCond([b], 'stepUpdatedBy', 'assigned') &&
          hasCond([b], 'currStepAgreed', false),
      ),
    ).toBe(true);

    // 被代理人侧：必须已确认跟进才参与协商
    expect(
      branches.some(
        (b) =>
          hasCond([b], 'principalBy') &&
          hasCond([b], 'proxyRelationStatus', 'acknowledged') &&
          hasCond([b], 'stepUpdatedBy', 'assigned') &&
          hasCond([b], 'currStepAgreed', false),
      ),
    ).toBe(true);
  });

  it('followup：仍为「我是被代理人且关系待确认」', () => {
    expect(buildRelevanceFilters('followup', 'u1', [])).toEqual([
      { field: 'principalBy', op: 'eq', value: true },
      { field: 'proxyRelationStatus', op: 'eq', value: 'pending' },
    ]);
  });

  it('related：被代理人（含 pending / declined）建立关系即「与我相关」', () => {
    const conds = buildRelevanceFilters('related', 'u1', []);
    expect(hasCond(conds, 'principalBy', true)).toBe(true);
  });

  it('followed：以 followedBy 占位，真实用户由后端按 token 解析', () => {
    expect(buildRelevanceFilters('followed', 'u1', [])).toEqual([
      { field: 'followedBy', op: 'eq', value: true },
    ]);
  });
});
