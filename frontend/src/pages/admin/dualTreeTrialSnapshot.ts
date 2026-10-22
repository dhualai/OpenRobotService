// 双树试做的本机树快照：页面「导出 JSON」「导入 JSON」与服务端 `.local/责任双树/` 快照共用一份格式。
// 浏览器里只有 localStorage，清缓存/换端口就没了，所以导出与快照都按同一份结构落盘，可以互相当备份。
export const SNAPSHOT_FORMAT = 'dual-tree-trial';
export const SNAPSHOT_VERSION = 1;

export interface TrialSnapshot<TNode = Record<string, unknown>, TMount = Record<string, unknown>> {
  format: string;
  version: number;
  saved_at: string;
  nodes: TNode[];
  mounts: TMount[];
}

export interface SnapshotCounts {
  domains: number;
  orgs: number;
  skills: number;
  mounts: number;
  nodes: number;
}

/** 只关心 nodes / mounts 两个数组的形态，导出结果、服务端快照、旧裸对象都能套 */
export interface SnapshotLike<TNode = unknown, TMount = unknown> {
  nodes: TNode[];
  mounts: TMount[];
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** 本地时间，形如 2026-10-14 15:52:07（跟服务端快照 saved_at 同一写法） */
export function formatSnapshotTime(date: Date = new Date()): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
    + `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** 下载文件名：双树试做-20261014-1552.json */
export function snapshotFileName(savedAt: string = formatSnapshotTime()): string {
  const digits = (savedAt.match(/\d+/g) || []).join('');
  const stamp = digits.length >= 12 ? `${digits.slice(0, 8)}-${digits.slice(8, 12)}` : digits;
  return `双树试做-${stamp || 'snapshot'}.json`;
}

export function buildSnapshot<TNode, TMount>(
  nodes: TNode[],
  mounts: TMount[],
  savedAt: string = formatSnapshotTime(),
): TrialSnapshot<TNode, TMount> {
  return {
    format: SNAPSHOT_FORMAT,
    version: SNAPSHOT_VERSION,
    saved_at: savedAt,
    nodes: [...nodes],
    mounts: [...mounts],
  };
}

/**
 * 读一份快照：本页导出的新格式、服务端快照文件、旧的 localStorage 裸 {nodes, mounts} 都认。
 * 认不出来（不是对象 / 两个数组缺一个）返回 null，调用方据此提示不要覆盖现有树。
 */
export function parseSnapshot<TNode = Record<string, unknown>, TMount = Record<string, unknown>>(
  input: unknown,
): TrialSnapshot<TNode, TMount> | null {
  let data: unknown = input;
  if (typeof input === 'string') {
    try {
      data = JSON.parse(input);
    } catch {
      return null;
    }
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null;
  const raw = data as Record<string, unknown>;
  if (!Array.isArray(raw.nodes) || !Array.isArray(raw.mounts)) return null;
  return {
    format: typeof raw.format === 'string' ? raw.format : SNAPSHOT_FORMAT,
    version: typeof raw.version === 'number' ? raw.version : SNAPSHOT_VERSION,
    saved_at: typeof raw.saved_at === 'string' ? raw.saved_at : '',
    nodes: raw.nodes as TNode[],
    mounts: raw.mounts as TMount[],
  };
}

export function snapshotCounts(snapshot: SnapshotLike): SnapshotCounts {
  const counts: SnapshotCounts = { domains: 0, orgs: 0, skills: 0, mounts: snapshot.mounts.length, nodes: snapshot.nodes.length };
  snapshot.nodes.forEach((node) => {
    const kind = (node as { kind?: string })?.kind;
    if (kind === 'domain') counts.domains += 1;
    else if (kind === 'org') counts.orgs += 1;
    else if (kind === 'skill') counts.skills += 1;
  });
  return counts;
}

/** 一句话说明这份快照有多少东西，用于 Toast */
export function snapshotBrief(snapshot: SnapshotLike): string {
  const c = snapshotCounts(snapshot);
  return `${c.domains} 个问题领域 / ${c.orgs} 个组织 / ${c.skills} 个技能 / ${c.mounts} 个挂人`;
}
