// 工单关系图工具：从 macaron-minimal-ui 移植，字段适配 OpenRobotService 后端
// 源文件：macaron-minimal-ui-main/src/lib/task-graph.ts
// 关键约束：后端 JSON id 是 number，但 parentKey/depKeys 返回 string —— 本文件所有
//          Map/Set key 统一用 strId(t.id)，杜绝 number/string 类型混淆。

/** graph 所需的最小 Ticket 接口（避免从 TasksView 循环引入） */
export interface GraphTicket {
  id: string | number;  // 后端 JSON 实际是 number，前端 interface 声明成 string
  status: string;
  parent_id?: number | null;
  predecessor_ids?: number[];
}

export type DepRef<T extends GraphTicket = GraphTicket> = {
  /** 展示的一个直接前置（可见、未用箭头表达） */
  prev?: T | undefined;
  prevMore: boolean;
  next?: T | undefined;
  nextMore: boolean;
};

export type RelationRow<T extends GraphTicket = GraphTicket> = {
  ticket: T;
  depth: number;
  childCount: number;
  childDone: number;
  /** 与上一行之间显示「上一行 → 本行」的小箭头 */
  arrowFromPrev: boolean;
  ref: DepRef<T> | null;
};

/** 终态集合 —— 已解决/已关闭/已归档（与 ticket.ts TERMINAL_STATUSES 保持一致） */
const DONE_STATUSES = new Set(['resolved', 'closed', 'archived']);

export function isDone(t: GraphTicket): boolean {
  return DONE_STATUSES.has((t.status || '').toLowerCase());
}

/** 统一 id → string（后端 JSON id 是 number，前端声明成 string —— 这里做归一） */
const strId = (id: number | string | null | undefined): string | null => {
  if (id == null) return null;
  return String(id);
};

const byNo = (a: GraphTicket, b: GraphTicket) => {
  const aId = strId(a.id) || '';
  const bId = strId(b.id) || '';
  return aId.localeCompare(bId, 'en', { numeric: true });
};

/** 从工单取「父工单 id」（string 形式，可用于 Map key） */
function parentKey(t: GraphTicket): string | null {
  return strId(t.parent_id);
}

/** 从工单取「前置依赖 id 列表」（全部转 string） */
function depKeys(t: GraphTicket): string[] {
  return (t.predecessor_ids ?? [])
    .map((id) => strId(id))
    .filter((s): s is string => Boolean(s));
}

/** 计算某工单的顶部依赖引用（完整显示所有 predecessor/successor，不做 arrow dedup） */
function buildRef<T extends GraphTicket>(
  t: T,
  all: Map<string, T>,
  visible: Set<string>,
  successors: Map<string, string[]>,
): DepRef<T> | null {
  const myId = strId(t.id)!;
  // 所有前置/后置关系都显示，不 dedup（箭头是相邻视觉通道，dep 行是完整关系链）
  const preds = depKeys(t).filter((p) => all.has(p));
  const succs = (successors.get(myId) ?? []);
  if (!preds.length && !succs.length) return null;
  const vp = preds.filter((p) => visible.has(p)).map((p) => all.get(p)!).sort(byNo);
  const vs = succs.filter((s) => visible.has(s)).map((s) => all.get(s)!).sort(byNo);
  return {
    prev: vp[0],
    prevMore: preds.length > (vp[0] ? 1 : 0),
    next: vs[0],
    nextMore: succs.length > (vs[0] ? 1 : 0),
  };
}

function successorMap<T extends GraphTicket>(all: T[]): Map<string, string[]> {
  const m = new Map<string, string[]>();
  for (const t of all) {
    const myId = strId(t.id)!;
    for (const p of depKeys(t)) {
      m.set(p, [...(m.get(p) ?? []), myId]);
    }
  }
  return m;
}

/** 普通列表：保持原排序，所有依赖都用顶部引用 */
export function buildFlatRefs<T extends GraphTicket>(
  all: T[],
  visibleList: T[],
): Map<string, DepRef<T> | null> {
  const map = new Map<string, T>();
  for (const t of all) {
    const sid = strId(t.id);
    if (sid) map.set(sid, t);
  }
  const visible = new Set<string>();
  for (const t of visibleList) {
    const sid = strId(t.id);
    if (sid) visible.add(sid);
  }
  const succ = successorMap(all);
  const result = new Map<string, DepRef<T> | null>();
  for (const t of visibleList) {
    const sid = strId(t.id);
    if (!sid) continue;
    result.set(sid, buildRef(t, map, visible, succ));
  }
  return result;
}

/** 同级排序：依赖连通的工单连续排列，块内按拓扑顺序，块按原顺序首次出现排列 */
export function orderSiblings<T extends GraphTicket>(siblings: T[]): T[] {
  const ids = new Set<string>();
  for (const s of siblings) { const sid = strId(s.id); if (sid) ids.add(sid); }
  const index = new Map<string, number>();
  siblings.forEach((s, i) => { const sid = strId(s.id); if (sid) index.set(sid, i); });
  const adj = new Map<string, Set<string>>();
  for (const s of siblings) {
    const sid = strId(s.id);
    if (!sid) continue;
    if (!adj.has(sid)) adj.set(sid, new Set());
    for (const p of depKeys(s)) {
      if (ids.has(p)) {
        adj.get(sid)!.add(p);
        if (!adj.has(p)) adj.set(p, new Set());
        adj.get(p)!.add(sid);
      }
    }
  }
  const seen = new Set<string>();
  const out: T[] = [];
  for (const s of siblings) {
    const sid = strId(s.id);
    if (!sid || seen.has(sid)) continue;
    const comp: T[] = [];
    const stack = [sid];
    seen.add(sid);
    while (stack.length) {
      const id = stack.pop()!;
      const origIdx = index.get(id)!;
      comp.push(siblings[origIdx]!);
      const neighbors = adj.get(id) ?? new Set<string>();
      for (const n of neighbors) {
        if (!seen.has(n)) { seen.add(n); stack.push(n); }
      }
    }
    comp.sort((a, b) => {
      const ai = index.get(strId(a.id)!) ?? 0;
      const bi = index.get(strId(b.id)!) ?? 0;
      return ai - bi;
    });
    // Kahn 拓扑排序（稳定）
    const compIds = new Set<string>();
    for (const c of comp) { const sid = strId(c.id); if (sid) compIds.add(sid); }
    const indeg = new Map<string, number>();
    for (const c of comp) {
      const sid = strId(c.id);
      if (!sid) continue;
      indeg.set(sid, depKeys(c).filter((p) => compIds.has(p)).length);
    }
    const done = new Set<string>();
    while (done.size < comp.length) {
      const next = comp.find((c) => {
        const sid = strId(c.id);
        return sid && !done.has(sid) && (indeg.get(sid) ?? 0) === 0;
      }) ?? comp.find((c) => { const sid = strId(c.id); return sid && !done.has(sid); })!;
      const nid = strId(next.id)!;
      done.add(nid);
      out.push(next);
      for (const c of comp) {
        if (depKeys(c).includes(nid)) {
          const sid = strId(c.id)!;
          indeg.set(sid, (indeg.get(sid) ?? 0) - 1);
        }
      }
    }
  }
  return out;
}

/** 关系列表：缩进表达父子，相邻同级的依赖用小箭头，其余用顶部引用 */
export function buildRelationRows<T extends GraphTicket>(
  all: T[],
  visibleList: T[],
  collapsed: Set<string>,
): RelationRow<T>[] {
  const map = new Map<string, T>();
  for (const t of all) { const sid = strId(t.id); if (sid) map.set(sid, t); }
  const visible = new Set<string>();
  for (const t of visibleList) { const sid = strId(t.id); if (sid) visible.add(sid); }
  const succ = successorMap(all);
  const children = new Map<string, T[]>();     // parent string → child tickets
  const roots: T[] = [];
  for (const t of visibleList) {
    const pKey = parentKey(t);
    if (pKey && visible.has(pKey)) {
      children.set(pKey, [...(children.get(pKey) ?? []), t]);
    } else {
      roots.push(t);
    }
  }

  const rows: Omit<RelationRow<T>, 'ref' | 'arrowFromPrev'>[] = [];
  const parentOf = new Map<string, string | null>();
  const walk = (list: T[], depth: number, parent: string | null) => {
    for (const t of orderSiblings(list)) {
      const sid = strId(t.id)!;
      const kids = children.get(sid) ?? [];
      const allKids = all.filter((k) => parentKey(k) === sid);
      parentOf.set(sid, parent);
      rows.push({
        ticket: t,
        depth,
        childCount: allKids.length,
        childDone: allKids.filter(isDone).length,
      });
      if (kids.length && !collapsed.has(sid)) walk(kids, depth + 1, sid);
    }
  };
  walk(roots, 0, null);

  // arrowed 仍用于计算 arrowFromPrev（相邻↓箭头），但不再传给 buildRef——
  // dep 行要完整显示所有 predecessor/successor 关系，不做 dedup
  const arrowed = new Set<string>();
  const arrows = rows.map((r, i) => {
    const prev = rows[i - 1];
    if (!prev) return false;
    const prevId = strId(prev.ticket.id)!;
    const curId = strId(r.ticket.id)!;
    if (parentOf.get(prevId) !== parentOf.get(curId)) return false;
    if (!depKeys(r.ticket).includes(prevId)) return false;
    arrowed.add(`${prevId}>${curId}`);
    return true;
  });
  return rows.map((r, i) => {
    const sid = strId(r.ticket.id)!;
    return {
      ...r,
      arrowFromPrev: arrows[i] ?? false,
      ref: buildRef(r.ticket, map, visible, succ),
    };
  });
}

/** 返回所有出现过子工单的父工单 id 集合（string 形式） */
export function parentIds<T extends GraphTicket>(all: T[]): Set<string> {
  return new Set(all.map((t) => parentKey(t)).filter((x): x is string => Boolean(x)));
}
