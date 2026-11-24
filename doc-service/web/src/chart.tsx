import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ExpandIcon } from "./icons";

export type ChartNode = {
  id: string;
  x: number;
  y: number;
  w?: number;
  h?: number;
  title: string;
  sub?: string;
  version?: string;
  band?: string;
  progress?: number | null;
  nested_board?: string | null;
  encrypted?: boolean;
  kind?: string;
  tag?: string | null;
  tag_unassigned?: boolean;
  admin_protected?: boolean;
  can_delete?: boolean;
};

export type ChartGroup = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  title?: string;
  members?: string[];
  kind?: string;
  linked?: boolean;
  admin_protected?: boolean;
  can_delete?: boolean;
  metrics_id?: string | null;
  test_sso?: boolean;
};

export type ChartEdge = {
  from: string;
  to: string;
  from_side?: string;
  to_side?: string;
  from_t?: number;
  to_t?: number;
};

export type ChartGraph = {
  board: string;
  node_w: number;
  node_h: number;
  nodes: ChartNode[];
  groups: ChartGroup[];
  edges: ChartEdge[];
  can_edit?: boolean;
};

export function ExpandEditLink({ to }: { to: string }) {
  return (
    <Link className="chart-expand" to={to} aria-label="展开编辑">
      <ExpandIcon />
    </Link>
  );
}

function lcClass(band?: string): string {
  const b = (band || "pre").toLowerCase();
  if (b === "done") return "done";
  if (b.includes("test") || b === "joint_test") return "test";
  if (b === "pre" || b === "req" || b === "design" || b === "review") return "pre";
  return "dev";
}

function ringPath(x: number, y: number, w: number, h: number, r = 3): string {
  const x1 = x + r;
  const y1 = y + 1.5;
  const x2 = x + w - r;
  const y2 = y + h - 1.5;
  const xr = x + w - 1.5;
  const xl = x + 1.5;
  const cx = x + w / 2;
  return [
    `M${cx.toFixed(1)},${y1.toFixed(1)}`,
    `L${x2.toFixed(1)},${y1.toFixed(1)}`,
    `A${r},${r} 0 0 1 ${xr.toFixed(1)},${(y + r + 0.5).toFixed(1)}`,
    `L${xr.toFixed(1)},${(y + h - r - 0.5).toFixed(1)}`,
    `A${r},${r} 0 0 1 ${x2.toFixed(1)},${y2.toFixed(1)}`,
    `L${x1.toFixed(1)},${y2.toFixed(1)}`,
    `A${r},${r} 0 0 1 ${xl.toFixed(1)},${(y + h - r - 0.5).toFixed(1)}`,
    `L${xl.toFixed(1)},${(y + r + 0.5).toFixed(1)}`,
    `A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)}`,
    `L${cx.toFixed(1)},${y1.toFixed(1)}`,
  ].join(" ");
}

function boxOf(n: ChartNode, nw: number, nh: number): [number, number, number, number] {
  return [n.x, n.y, n.w || nw, n.h || nh];
}

function sidePoint(box: [number, number, number, number], side: string, t = 0.5): [number, number] {
  const [x, y, w, h] = box;
  const tt = Math.max(0.08, Math.min(0.92, t));
  if (side === "top") return [x + w * tt, y];
  if (side === "bottom") return [x + w * tt, y + h];
  if (side === "left") return [x, y + h * tt];
  return [x + w, y + h * tt];
}

function pickSides(a: [number, number, number, number], b: [number, number, number, number]): [string, string] {
  const acx = a[0] + a[2] / 2;
  const acy = a[1] + a[3] / 2;
  const bcx = b[0] + b[2] / 2;
  const bcy = b[1] + b[3] / 2;
  const dx = bcx - acx;
  const dy = bcy - acy;
  if (Math.abs(dx) * 1.15 >= Math.abs(dy)) {
    return dx >= 0 ? ["right", "left"] : ["left", "right"];
  }
  return dy >= 0 ? ["bottom", "top"] : ["top", "bottom"];
}

function resolveSides(
  a: [number, number, number, number],
  b: [number, number, number, number],
  opts?: { from_side?: string; to_side?: string },
): [string, string] {
  const sides = new Set(["top", "bottom", "left", "right"]);
  let fs = opts?.from_side && sides.has(opts.from_side) ? opts.from_side : "";
  let ts = opts?.to_side && sides.has(opts.to_side) ? opts.to_side : "";
  if (!fs || !ts) {
    const [afs, ats] = pickSides(a, b);
    fs = fs || afs;
    ts = ts || ats;
  }
  return [fs, ts];
}

function projectToBorder(
  box: [number, number, number, number],
  bx: number,
  by: number,
): { side: string; t: number } {
  const [x, y, w, h] = box;
  const candidates = [
    { side: "top", x: Math.max(x, Math.min(x + w, bx)), y },
    { side: "bottom", x: Math.max(x, Math.min(x + w, bx)), y: y + h },
    { side: "left", x, y: Math.max(y, Math.min(y + h, by)) },
    { side: "right", x: x + w, y: Math.max(y, Math.min(y + h, by)) },
  ];
  let best = candidates[0];
  let bestD = Infinity;
  for (const c of candidates) {
    const d = Math.hypot(c.x - bx, c.y - by);
    if (d < bestD) {
      bestD = d;
      best = c;
    }
  }
  let t = 0.5;
  if (best.side === "top" || best.side === "bottom") t = w ? (best.x - x) / w : 0.5;
  else t = h ? (best.y - y) / h : 0.5;
  return { side: best.side, t: Math.max(0.08, Math.min(0.92, t)) };
}

function edgePath(
  a: [number, number, number, number],
  b: [number, number, number, number],
  opts?: { from_side?: string; to_side?: string; from_t?: number; to_t?: number },
): string {
  const [fs, ts] = resolveSides(a, b, opts);
  const ft = opts?.from_t == null ? 0.5 : opts.from_t;
  const tt = opts?.to_t == null ? 0.5 : opts.to_t;
  const [x1, y1] = sidePoint(a, fs, ft);
  const [x2, y2] = sidePoint(b, ts, tt);
  const n1 = fs === "right" ? [1, 0] : fs === "left" ? [-1, 0] : fs === "top" ? [0, -1] : [0, 1];
  const n2 = ts === "right" ? [1, 0] : ts === "left" ? [-1, 0] : ts === "top" ? [0, -1] : [0, 1];
  const dist = Math.max(36, Math.min(120, Math.hypot(x2 - x1, y2 - y1) * 0.35));
  const c1x = x1 + n1[0] * dist;
  const c1y = y1 + n1[1] * dist;
  const c2x = x2 + n2[0] * dist;
  const c2y = y2 + n2[1] * dist;
  const tip = 9;
  return `M${x1.toFixed(1)},${y1.toFixed(1)} C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${(x2 + n2[0] * tip).toFixed(1)},${(y2 + n2[1] * tip).toFixed(1)} L${x2.toFixed(1)},${y2.toFixed(1)}`;
}

function endpointBox(
  id: string,
  pos: Record<string, ChartNode>,
  groups: ChartGroup[],
  collapsed: Set<string>,
  nw: number,
  nh: number,
): [number, number, number, number] | null {
  const n = pos[id];
  if (n) return boxOf(n, nw, nh);
  const g = groups.find((x) => x.id === id);
  if (!g) return null;
  const members = (g.members || []).map((mid) => pos[mid]).filter(Boolean);
  return groupHull(g, members, collapsed.has(g.id), nw, nh);
}

function svgId(id: string): string {
  return id.replace(/[^A-Za-z0-9_-]/g, "_");
}

function approxTextWidth(s: string): number {
  let w = 0;
  for (const ch of s) w += /[\u4e00-\u9fff]/.test(ch) ? 12 : 8.2;
  return w;
}

const METRICS_GAP = 8;
const METRICS_PAD_X = 7.8;
const METRICS_LABEL_W = 18;
const METRICS_TAG_H = 14.4;
const METRICS_TAG_W = METRICS_LABEL_W + METRICS_PAD_X * 2;
const TEST_LABEL_W = 24;
const TEST_TAG_W = TEST_LABEL_W + METRICS_PAD_X * 2;

function titleTagsWidth(g: ChartGroup): number {
  let w = 0;
  if (g.metrics_id) w += METRICS_GAP + METRICS_TAG_W;
  if (g.test_sso) w += METRICS_GAP + TEST_TAG_W;
  return w;
}

function GroupTitleRow({
  title,
  x,
  y,
  titleClass,
  metricsId,
  testSso,
  onPrefetch,
  onMetricsClick,
  onTestClick,
  clickable,
  titleHitEvents,
  onTitlePointerEnter,
  onTitlePointerDown,
  onTitlePointerUp,
}: {
  title: string;
  x: number;
  y: number;
  titleClass: string;
  metricsId?: string | null;
  testSso?: boolean;
  onPrefetch?: (id: string) => void;
  onMetricsClick?: (id: string) => void;
  onTestClick?: () => void;
  clickable?: boolean;
  titleHitEvents: "all" | "none";
  onTitlePointerEnter?: () => void;
  onTitlePointerDown?: (ev: ReactPointerEvent<SVGRectElement>) => void;
  onTitlePointerUp?: (ev: ReactPointerEvent<SVGRectElement>) => void;
}) {
  const ref = useRef<SVGTextElement>(null);
  const [box, setBox] = useState(() => ({ w: approxTextWidth(title), y: y - 9, h: 11 }));
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const b = el.getBBox();
    setBox({ w: b.width, y: b.y, h: b.height });
  }, [title, y]);
  const my = box.y + box.h / 2 - METRICS_TAG_H / 2;
  const mx = x + box.w + METRICS_GAP;
  const tx = mx + (metricsId ? METRICS_TAG_W + METRICS_GAP : 0);
  return (
    <>
      <rect
        className="group-title-hit"
        x={x}
        y={box.y}
        width={box.w}
        height={box.h}
        pointerEvents={titleHitEvents}
        onPointerEnter={onTitlePointerEnter}
        onPointerDown={onTitlePointerDown}
        onPointerUp={onTitlePointerUp}
      />
      <text ref={ref} className={titleClass} x={x} y={y}>
        {title}
      </text>
      {metricsId ? (
        <g
          className="group-metrics"
          pointerEvents="all"
          onPointerDown={(ev) => {
            ev.stopPropagation();
            ev.preventDefault();
            if (ev.button !== 0 || !clickable) return;
            onPrefetch?.(metricsId);
            onMetricsClick?.(metricsId);
          }}
          onPointerEnter={() => onPrefetch?.(metricsId)}
        >
          <rect className="group-metrics-tag" x={mx} y={my} width={METRICS_TAG_W} height={METRICS_TAG_H} rx={METRICS_TAG_H / 2} />
          <text
            className="group-metrics-label"
            x={mx + METRICS_TAG_W / 2}
            y={my + METRICS_TAG_H / 2}
            textAnchor="middle"
            dominantBaseline="central"
          >
            指标
          </text>
        </g>
      ) : null}
      {testSso ? (
        <g
          className="group-test"
          pointerEvents="all"
          onPointerDown={(ev) => {
            ev.stopPropagation();
            ev.preventDefault();
            if (ev.button !== 0 || !clickable) return;
            onTestClick?.();
          }}
        >
          <rect className="group-test-tag" x={tx} y={my} width={TEST_TAG_W} height={METRICS_TAG_H} rx={METRICS_TAG_H / 2} />
          <text
            className="group-test-label"
            x={tx + TEST_TAG_W / 2}
            y={my + METRICS_TAG_H / 2}
            textAnchor="middle"
            dominantBaseline="central"
          >
            TEST
          </text>
        </g>
      ) : null}
    </>
  );
}

function groupHull(
  g: ChartGroup,
  members: ChartNode[],
  collapsed: boolean,
  nw: number,
  nh: number,
): [number, number, number, number] {
  let hx: number;
  let hy: number;
  let hw: number;
  let hh: number;
  if (collapsed) {
    hx = g.x;
    hy = g.y;
    hw = Math.max(g.w || 120, 120);
    hh = 28;
  } else if ((!g.w || !g.h) && members.length) {
    const pad = 18;
    const labelH = 22;
    hx = Math.min(...members.map((n) => n.x)) - pad;
    hy = Math.min(...members.map((n) => n.y)) - pad - labelH;
    hw = Math.max(...members.map((n) => n.x + (n.w || nw))) + pad - hx;
    hh = Math.max(...members.map((n) => n.y + (n.h || nh))) + pad - hy;
  } else {
    hx = g.x;
    hy = g.y;
    hw = g.w;
    hh = g.h;
  }
  const extra = titleTagsWidth(g);
  if (extra) {
    hw = Math.max(hw, 10 + approxTextWidth(g.title || g.id) + extra + 28);
  }
  return [hx, hy, hw, hh];
}

function chartViewBox(
  vis: ChartNode[],
  groups: ChartGroup[],
  pos: Record<string, ChartNode>,
  collapsed: Set<string>,
  nw: number,
  nh: number,
): { ox: number; oy: number; w: number; h: number } {
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const n of vis) {
    const ww = n.w || nw;
    const hh = n.h || nh;
    minX = Math.min(minX, n.x);
    minY = Math.min(minY, n.y);
    maxX = Math.max(maxX, n.x + ww + 8);
    maxY = Math.max(maxY, n.y + hh + 20);
  }
  for (const g of groups) {
    const members = (g.members || []).map((id) => pos[id]).filter(Boolean);
    const [hx, hy, hw, hh] = groupHull(g, members, collapsed.has(g.id), nw, nh);
    minX = Math.min(minX, hx);
    minY = Math.min(minY, hy);
    maxX = Math.max(maxX, hx + hw);
    maxY = Math.max(maxY, hy + hh);
  }
  if (!Number.isFinite(minX)) {
    return { ox: 0, oy: 0, w: 120, h: 120 };
  }
  const pad = 32;
  const ox = minX - pad;
  const oy = minY - pad;
  return { ox, oy, w: Math.max(maxX + pad - ox, 120), h: Math.max(maxY + pad - oy, 120) };
}

function HoursBead({ id, d, prog, duration }: { id: string; d: string; prog: number; duration: number | null | undefined }) {
  const gap = Math.max(0, 100 - prog);
  const beadGap = gap > 1 ? gap : 100;
  const beadProg = gap > 1 ? prog : 0;
  let bead = 0.9;
  let gapB = 3.0;
  let slot = bead + gapB;
  if (slot > beadGap) {
    bead = Math.min(0.9, beadGap * 0.35);
    gapB = Math.max(0.4, beadGap - bead);
    slot = bead + gapB;
  }
  const sid = svgId(id);
  const maskId = `lc-m-${sid}`;
  const active = duration != null && duration > 0;
  const dur = active ? duration : 8;
  return (
    <g key={`${sid}-${dur}`}>
      <mask id={maskId} maskUnits="userSpaceOnUse">
        <path
          d={d}
          pathLength={100}
          fill="none"
          stroke="#fff"
          strokeWidth={4}
          strokeLinecap="butt"
          strokeDasharray={`${beadGap.toFixed(2)} ${beadProg.toFixed(2)}`}
          strokeDashoffset={-beadProg}
        />
      </mask>
      <path
        className={`lc-bead${active ? " is-active" : ""}`}
        d={d}
        pathLength={100}
        fill="none"
        strokeDasharray={`${bead.toFixed(2)} ${gapB.toFixed(2)}`}
        strokeDashoffset={0}
        mask={`url(#${maskId})`}
      >
        <animate attributeName="stroke-dashoffset" from="0" to={(-slot).toFixed(2)} dur={`${dur}s`} repeatCount="indefinite" />
      </path>
    </g>
  );
}

type Sel = { kind: "node" | "edge" | "group"; id: string };

export type AdoptHub = { kind: "node" | "group"; id: string };

export function hitAdoptTarget(
  bx: number,
  by: number,
  nodes: ChartNode[],
  groups: ChartGroup[],
  exclude: Set<string>,
  nw: number,
  nh: number,
): AdoptHub | null {
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (exclude.has(n.id)) continue;
    const ww = n.w || nw;
    const hh = n.h || nh;
    if (bx >= n.x && bx <= n.x + ww && by >= n.y && by <= n.y + hh) {
      return { kind: "node", id: n.id };
    }
  }
  const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
  for (const g of groups) {
    if (exclude.has(g.id)) continue;
    const members = (g.members || []).map((id) => pos[id]).filter(Boolean);
    const [hx, hy, hw, hh] = groupHull(g, members, false, nw, nh);
    if (bx >= hx && bx <= hx + hw && by >= hy && by <= hy + hh) {
      return { kind: "group", id: g.id };
    }
  }
  return null;
}

function familyOf(gid: string, groups: ChartGroup[], nodeIds: Set<string>): { groupIds: string[]; nodeIds: string[] } {
  const byId = Object.fromEntries(groups.map((g) => [g.id, g]));
  const outG: string[] = [];
  const outN: string[] = [];
  const seen = new Set<string>();
  const walk = (id: string) => {
    if (seen.has(id)) return;
    seen.add(id);
    const g = byId[id];
    if (g) {
      outG.push(id);
      for (const m of g.members || []) walk(m);
    } else if (nodeIds.has(id)) {
      outN.push(id);
    }
  };
  walk(gid);
  return { groupIds: outG, nodeIds: outN };
}

type Props = {
  graph: ChartGraph;
  nodes?: ChartNode[];
  snakeDurations?: Record<string, number | null>;
  tool?: "select" | "pan" | "link";
  selected?: Sel | null;
  selectGroups?: boolean;
  onNodeClick?: (n: ChartNode) => void;
  onGroupClick?: (id: string) => void;
  onPrefetch?: (id: string) => void;
  onNested?: (n: ChartNode) => void;
  onMetricsClick?: (id: string) => void;
  onTestClick?: () => void;
  onNodeDrag?: (id: string, x: number, y: number) => void;
  onGroupDrag?: (
    nodes: { id: string; x: number; y: number }[],
    groups: { id: string; x: number; y: number }[],
  ) => void;
  onGroupDragEnd?: () => void;
  onAdopt?: (ids: string[], hub: AdoptHub) => void;
  onSelect?: (s: Sel | null) => void;
  onLink?: (from: string, to: string) => void;
  onEdgeAnchor?: (from: string, to: string, patch: Pick<ChartEdge, "from_side" | "to_side" | "from_t" | "to_t">) => void;
  onEdgeAnchorEnd?: () => void;
};

export function TopologyChart({
  graph,
  nodes,
  snakeDurations,
  tool = "select",
  selected,
  selectGroups,
  onNodeClick,
  onGroupClick,
  onPrefetch,
  onNested,
  onMetricsClick,
  onTestClick,
  onNodeDrag,
  onGroupDrag,
  onGroupDragEnd,
  onAdopt,
  onSelect,
  onLink,
  onEdgeAnchor,
  onEdgeAnchorEnd,
}: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [linkFrom, setLinkFrom] = useState<string | null>(null);
  const [drop, setDrop] = useState<AdoptHub | null>(null);
  const live = useRef({
    list: [] as ChartNode[],
    groups: [] as ChartGroup[],
    nw: 158,
    nh: 46,
    collapsed: new Set<string>(),
  });
  const onAdoptRef = useRef(onAdopt);
  onAdoptRef.current = onAdopt;
  const onGroupDragRef = useRef(onGroupDrag);
  onGroupDragRef.current = onGroupDrag;
  const onGroupDragEndRef = useRef(onGroupDragEnd);
  onGroupDragEndRef.current = onGroupDragEnd;
  const onEdgeAnchorRef = useRef(onEdgeAnchor);
  onEdgeAnchorRef.current = onEdgeAnchor;
  const onEdgeAnchorEndRef = useRef(onEdgeAnchorEnd);
  onEdgeAnchorEndRef.current = onEdgeAnchorEnd;
  useEffect(() => {
    if (tool !== "link") setLinkFrom(null);
  }, [tool]);
  const list = nodes || graph.nodes;
  live.current = { list, groups: graph.groups, nw: graph.node_w || 158, nh: graph.node_h || 46, collapsed };
  const hidden = new Set<string>();
  for (const g of graph.groups) {
    if (collapsed.has(g.id)) {
      for (const id of g.members || []) hidden.add(id);
    }
  }
  const vis = onNodeDrag ? list : list.filter((n) => !hidden.has(n.id));
  const nw = graph.node_w || 158;
  const nh = graph.node_h || 46;
  const pos = Object.fromEntries(vis.map((n) => [n.id, n]));
  const { ox, oy, w, h } = chartViewBox(vis, graph.groups, pos, collapsed, nw, nh);
  const canAnchor = Boolean(onEdgeAnchor) && tool === "select";
  function startAnchorDrag(ev: ReactPointerEvent<SVGCircleElement>, edge: ChartEdge, which: "from" | "to") {
    if (ev.button !== 0) return;
    ev.stopPropagation();
    ev.preventDefault();
    const svg = (ev.currentTarget as SVGCircleElement).ownerSVGElement;
    if (!svg) return;
    const toBoard = (e: PointerEvent) => {
      const p = svg.createSVGPoint();
      p.x = e.clientX;
      p.y = e.clientY;
      const c = svg.getScreenCTM();
      if (!c) return null;
      return p.matrixTransform(c.inverse());
    };
    const move = (e: PointerEvent) => {
      const loc = toBoard(e);
      if (!loc) return;
      const cur = live.current;
      const pmap = Object.fromEntries(cur.list.map((n) => [n.id, n]));
      const box = endpointBox(which === "from" ? edge.from : edge.to, pmap, cur.groups, cur.collapsed, cur.nw, cur.nh);
      if (!box) return;
      const hit = projectToBorder(box, loc.x, loc.y);
      onEdgeAnchorRef.current?.(
        edge.from,
        edge.to,
        which === "from" ? { from_side: hit.side, from_t: hit.t } : { to_side: hit.side, to_t: hit.t },
      );
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      onEdgeAnchorEndRef.current?.();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }
  let anchorPts: { edge: ChartEdge; from: [number, number]; to: [number, number] } | null = null;
  if (canAnchor && selected?.kind === "edge") {
    const edge = graph.edges.find((e) => `${e.from}\t${e.to}` === selected.id);
    if (edge) {
      const ea = endpointBox(edge.from, pos, graph.groups, collapsed, nw, nh);
      const eb = endpointBox(edge.to, pos, graph.groups, collapsed, nw, nh);
      if (ea && eb) {
        const [fs, ts] = resolveSides(ea, eb, edge);
        anchorPts = { edge, from: sidePoint(ea, fs, edge.from_t ?? 0.5), to: sidePoint(eb, ts, edge.to_t ?? 0.5) };
      }
    }
  }
  return (
    <svg className="chart-svg" viewBox={`${ox} ${oy} ${w} ${h}`} width={w} height={h}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="10" markerHeight="7" orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0.5 L9,3.5 L0,6.5 Z" fill="currentColor" />
        </marker>
      </defs>
      {graph.groups.map((g) => {
        const members = (g.members || []).map((id) => pos[id]).filter(Boolean);
        const [hx, hy, hw, hh] = groupHull(g, members, collapsed.has(g.id), nw, nh);
        return (
          <g
            key={`hull-${g.id}`}
            className={`chart-group${g.kind === "product_hub" || g.id.endsWith("_algo") ? " is-product" : ""}${selected?.kind === "group" && selected.id === g.id ? " is-selected" : ""}${drop?.kind === "group" && drop.id === g.id ? " is-drop" : ""}`}
            pointerEvents="none"
          >
            <rect className="group-hull" x={hx} y={hy} width={hw} height={hh} rx={4} />
          </g>
        );
      })}
      {graph.edges.map((e) => {
        const a = endpointBox(e.from, pos, graph.groups, collapsed, nw, nh);
        const b = endpointBox(e.to, pos, graph.groups, collapsed, nw, nh);
        if (!a || !b) return null;
        const d = edgePath(a, b, e);
        const eid = `${e.from}\t${e.to}`;
        const on = selected?.kind === "edge" && selected.id === eid;
        return (
          <g
            key={eid}
            className={`chart-edge-wrap${on ? " is-selected" : ""}`}
            onPointerDown={(ev) => {
              if (ev.button !== 0) return;
              if (tool === "pan" || tool === "link") return;
              ev.stopPropagation();
              onSelect?.({ kind: "edge", id: eid });
            }}
          >
            <path className="chart-edge-glow" d={d} fill="none" />
            <path className="chart-edge-hit" d={d} fill="none" />
            <path className="chart-edge" d={d} fill="none" stroke="currentColor" strokeWidth={on ? 2.8 : 1.7} markerEnd="url(#arrow)" />
          </g>
        );
      })}
      {vis.map((n) => {
        const band = lcClass(n.band);
        const busy = band !== "done";
        const prog = n.progress == null ? (busy ? 0 : 100) : Math.max(0, Math.min(100, n.progress));
        const ww = n.w || nw;
        const hh = n.h || nh;
        const d = ringPath(n.x, n.y, ww, hh);
        const hasSub = Boolean((n.sub || "").trim()) && n.kind !== "product_release";
        const release = n.kind === "product_release";
        const showVer = Boolean(n.version);
        const picked = selected?.kind === "node" && selected.id === n.id;
        const linking = tool === "link" && linkFrom === n.id;
        return (
          <g
            key={n.id}
            className={`chart-node lc-${band}${busy ? " lc-busy" : ""}${n.nested_board ? " nested" : ""}${release ? " usp-release" : ""}${n.tag_unassigned ? " tag-unassigned" : ""}${picked || linking ? " is-selected" : ""}${drop?.kind === "node" && drop.id === n.id ? " is-drop" : ""}`}
            style={{ cursor: tool === "link" ? "crosshair" : tool === "pan" ? "grab" : onNodeDrag ? "grab" : "pointer" }}
            onPointerEnter={() => onPrefetch?.(n.id)}
            onPointerDown={(ev) => {
              if (tool === "link") {
                ev.stopPropagation();
                setLinkFrom(n.id);
                return;
              }
              if (onNodeDrag) {
                if (tool === "pan") return;
                ev.preventDefault();
                const svg = (ev.currentTarget as SVGGElement).ownerSVGElement;
                if (!svg) return;
                const ctm = svg.getScreenCTM();
                if (!ctm) return;
                const pt = svg.createSVGPoint();
                pt.x = ev.clientX;
                pt.y = ev.clientY;
                const loc = pt.matrixTransform(ctm.inverse());
                const dx = loc.x - n.x;
                const dy = loc.y - n.y;
                let moved = false;
                const toBoard = (e: PointerEvent) => {
                  const p = svg.createSVGPoint();
                  p.x = e.clientX;
                  p.y = e.clientY;
                  const c = svg.getScreenCTM();
                  if (!c) return null;
                  return p.matrixTransform(c.inverse());
                };
                const move = (e: PointerEvent) => {
                  const l = toBoard(e);
                  if (!l) return;
                  if (Math.abs(l.x - dx - n.x) + Math.abs(l.y - dy - n.y) > 4) moved = true;
                  onNodeDrag(n.id, l.x - dx, l.y - dy);
                  if (!onAdoptRef.current) return;
                  if (!moved) return;
                  const cur = live.current;
                  setDrop(hitAdoptTarget(l.x, l.y, cur.list, cur.groups, new Set([n.id]), cur.nw, cur.nh));
                };
                const up = (e: PointerEvent) => {
                  window.removeEventListener("pointermove", move);
                  window.removeEventListener("pointerup", up);
                  const adopt = onAdoptRef.current;
                  if (!adopt || !moved) {
                    setDrop(null);
                    return;
                  }
                  const l = toBoard(e);
                  const cur = live.current;
                  const hit = l ? hitAdoptTarget(l.x, l.y, cur.list, cur.groups, new Set([n.id]), cur.nw, cur.nh) : null;
                  setDrop(null);
                  if (hit) adopt([n.id], hit);
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", up);
                return;
              }
              if (tool === "pan") return;
              if (ev.button !== 0) return;
              onPrefetch?.(n.id);
              if (onNodeClick) {
                ev.stopPropagation();
                onSelect?.({ kind: "node", id: n.id });
                onNodeClick(n);
              }
            }}
            onClick={(ev) => {
              ev.stopPropagation();
              if (tool === "link") return;
              onSelect?.({ kind: "node", id: n.id });
              if (onNodeDrag || tool === "pan") onNodeClick?.(n);
            }}
            onPointerUp={(ev) => {
              if (tool !== "link" || !linkFrom) return;
              ev.stopPropagation();
              if (linkFrom !== n.id) onLink?.(linkFrom, n.id);
              setLinkFrom(null);
            }}
          >
            <clipPath id={`clip-${n.id}`}>
              <rect x={n.x + 6} y={n.y + 2} width={Math.max(8, ww - (n.encrypted || n.nested_board ? 28 : 12))} height={hh - 4} />
            </clipPath>
            <rect className="node-body" x={n.x} y={n.y} width={ww} height={hh} rx={3} />
            {picked || linking || (drop?.kind === "node" && drop.id === n.id) ? (
              <rect className={`sel-frame${drop?.kind === "node" && drop.id === n.id ? " is-drop" : ""}`} x={n.x - 3} y={n.y - 3} width={ww + 6} height={hh + 6} rx={5} />
            ) : null}
            <path className="lc-track" d={d} pathLength={100} />
            <path className="lc-prog" d={d} pathLength={100} strokeDasharray={`${prog} ${100 - prog}`} />
            {busy ? <HoursBead id={n.id} d={d} prog={prog} duration={snakeDurations?.[n.id]} /> : null}
            <text className="node-title" x={n.x + 8} y={hasSub ? n.y + 18 : n.y + hh / 2 + 4} clipPath={`url(#clip-${n.id})`}>
              {n.title}
            </text>
            {hasSub ? (
              <text className="node-sub" x={n.x + 8} y={n.y + 33} clipPath={`url(#clip-${n.id})`}>
                {(n.sub || "").slice(0, 16)}
              </text>
            ) : null}
            {showVer ? (
              <text className={`node-ver ${release || busy ? "is-busy" : "is-idle"}`} x={n.x + ww + 1} y={n.y + hh + 10} textAnchor="end">
                {n.version}
              </text>
            ) : null}
            {n.encrypted ? (
              <g className="node-lock" transform={`translate(${n.x + ww - 16},${n.y + 3})`}>
                <rect x="1.5" y="6" width="9" height="7" rx="1.2" fill="none" stroke="#656d76" strokeWidth="1.3" />
                <path d="M3.5 6 V4.6 a2.5 2.5 0 0 1 5 0 V6" fill="none" stroke="#656d76" strokeWidth="1.3" />
              </g>
            ) : null}
            {n.nested_board ? (
              <text
                className="node-nest"
                x={n.x + ww - 8}
                y={n.y + hh - 8}
                textAnchor="end"
                onClick={(ev) => {
                  ev.stopPropagation();
                  onNested?.(n);
                }}
              >
                ▸
              </text>
            ) : null}
          </g>
        );
      })}
      {graph.groups.map((g) => {
        const members = (g.members || []).map((id) => pos[id]).filter(Boolean);
        const [hx, hy, hw] = groupHull(g, members, collapsed.has(g.id), nw, nh);
        const title = g.title || g.id;
        const canOpen = Boolean(onGroupClick) && (selectGroups || g.linked !== false);
        const gOn = selected?.kind === "group" && selected.id === g.id;
        const canDrag = Boolean(onGroupDrag) && tool === "select";
        return (
          <g
            key={`chrome-${g.id}`}
            className={`chart-group-chrome${canOpen ? " is-link" : ""}${canDrag ? " is-drag" : ""}${gOn ? " is-selected" : ""}`}
          >
            <GroupTitleRow
              title={title}
              x={hx + 10}
              y={hy + 16}
              titleClass={`group-title${canOpen ? " is-link" : ""}${gOn ? " is-selected" : ""}`}
              metricsId={g.metrics_id}
              testSso={Boolean(g.test_sso)}
              onPrefetch={onPrefetch}
              onMetricsClick={onMetricsClick}
              onTestClick={onTestClick}
              clickable={tool !== "pan" && tool !== "link"}
              titleHitEvents={canOpen || canDrag || tool === "link" ? "all" : "none"}
              onTitlePointerEnter={() => onPrefetch?.(g.id)}
              onTitlePointerDown={(ev) => {
                if (ev.button !== 0) return;
                if (tool === "pan") return;
                ev.stopPropagation();
                if (tool === "link") {
                  setLinkFrom(g.id);
                  return;
                }
                onPrefetch?.(g.id);
                onSelect?.({ kind: "group", id: g.id });
                if (canOpen) onGroupClick?.(g.id);
                if (!canDrag) return;
                ev.preventDefault();
                const svg = (ev.currentTarget as SVGRectElement).ownerSVGElement;
                if (!svg) return;
                const ctm = svg.getScreenCTM();
                if (!ctm) return;
                const pt = svg.createSVGPoint();
                pt.x = ev.clientX;
                pt.y = ev.clientY;
                const start = pt.matrixTransform(ctm.inverse());
                const cur = live.current;
                const fam = familyOf(g.id, cur.groups, new Set(cur.list.map((n) => n.id)));
                const nodeOrg = Object.fromEntries(
                  fam.nodeIds.map((id) => {
                    const n = cur.list.find((x) => x.id === id);
                    return [id, { x: n?.x || 0, y: n?.y || 0 }];
                  }),
                );
                const groupOrg = Object.fromEntries(
                  fam.groupIds.map((id) => {
                    const gg = cur.groups.find((x) => x.id === id);
                    return [id, { x: gg?.x || 0, y: gg?.y || 0 }];
                  }),
                );
                let moved = false;
                const toBoard = (e: PointerEvent) => {
                  const p = svg.createSVGPoint();
                  p.x = e.clientX;
                  p.y = e.clientY;
                  const c = svg.getScreenCTM();
                  if (!c) return null;
                  return p.matrixTransform(c.inverse());
                };
                const move = (e: PointerEvent) => {
                  const l = toBoard(e);
                  if (!l) return;
                  const dx = l.x - start.x;
                  const dy = l.y - start.y;
                  if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
                  if (!moved) return;
                  onGroupDragRef.current?.(
                    fam.nodeIds.map((id) => ({ id, x: Math.round(nodeOrg[id].x + dx), y: Math.round(nodeOrg[id].y + dy) })),
                    fam.groupIds.map((id) => ({ id, x: Math.round(groupOrg[id].x + dx), y: Math.round(groupOrg[id].y + dy) })),
                  );
                };
                const up = () => {
                  window.removeEventListener("pointermove", move);
                  window.removeEventListener("pointerup", up);
                  if (moved) onGroupDragEndRef.current?.();
                };
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", up);
              }}
              onTitlePointerUp={(ev) => {
                if (tool !== "link" || !linkFrom) return;
                ev.stopPropagation();
                if (linkFrom !== g.id) onLink?.(linkFrom, g.id);
                setLinkFrom(null);
              }}
            />
            <text
              className="group-toggle"
              x={hx + hw - 12}
              y={hy + 16}
              textAnchor="middle"
              onClick={(ev) => {
                ev.stopPropagation();
                setCollapsed((prev) => {
                  const next = new Set(prev);
                  if (next.has(g.id)) next.delete(g.id);
                  else next.add(g.id);
                  return next;
                });
              }}
            >
              {collapsed.has(g.id) ? "+" : "−"}
            </text>
          </g>
        );
      })}
      {anchorPts ? (
        <g className="edge-anchors">
          <circle
            className="anchor-handle"
            cx={anchorPts.from[0]}
            cy={anchorPts.from[1]}
            r={6}
            onPointerDown={(ev) => {
              if (!anchorPts) return;
              startAnchorDrag(ev, anchorPts.edge, "from");
            }}
          />
          <circle
            className="anchor-handle"
            cx={anchorPts.to[0]}
            cy={anchorPts.to[1]}
            r={6}
            onPointerDown={(ev) => {
              if (!anchorPts) return;
              startAnchorDrag(ev, anchorPts.edge, "to");
            }}
          />
        </g>
      ) : null}
    </svg>
  );
}

export function ChartStage({
  children,
  fitKey,
  extra,
  compact,
  pan = true,
  linkCursor,
}: {
  children: ReactNode;
  fitKey?: string;
  extra?: ReactNode;
  compact?: boolean;
  pan?: boolean;
  linkCursor?: boolean;
}) {
  const vp = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const z = useRef(1);
  const t = useRef({ x: 0, y: 0 });
  const [, bump] = useState(0);
  function apply() {
    const el = inner.current;
    if (el) el.style.transform = `translate(${t.current.x}px, ${t.current.y}px) scale(${z.current})`;
    bump((n) => n + 1);
  }
  function fit() {
    const box = vp.current;
    const node = inner.current;
    if (!box || !node) return;
    const svg = node.querySelector("svg");
    const sw = svg ? Number(svg.getAttribute("width")) || svg.getBoundingClientRect().width : node.scrollWidth;
    const sh = svg ? Number(svg.getAttribute("height")) || svg.getBoundingClientRect().height : node.scrollHeight;
    const pad = 16;
    const nw = Math.max(1, box.clientWidth - pad * 2);
    const nh = Math.max(1, box.clientHeight - pad * 2);
    z.current = Math.max(0.2, Math.min(1.4, Math.min(nw / Math.max(sw, 1), nh / Math.max(sh, 1))));
    t.current = {
      x: (box.clientWidth - sw * z.current) / 2,
      y: (box.clientHeight - sh * z.current) / 2,
    };
    apply();
  }
  useLayoutEffect(() => {
    const id = requestAnimationFrame(() => fit());
    return () => cancelAnimationFrame(id);
  }, [fitKey]);
  function zoomBy(delta: number, cx?: number, cy?: number) {
    const box = vp.current;
    if (!box) return;
    const prev = z.current;
    const next = Math.max(0.2, Math.min(2.2, prev + delta));
    const px = cx ?? box.clientWidth / 2;
    const py = cy ?? box.clientHeight / 2;
    const wx = (px - t.current.x) / prev;
    const wy = (py - t.current.y) / prev;
    z.current = next;
    t.current = { x: px - wx * next, y: py - wy * next };
    apply();
  }
  return (
    <div className={`chart-stage${compact ? " is-compact" : ""}`}>
      <div
        className={`chart-viewport${linkCursor ? " is-link" : ""}`}
        ref={vp}
        onWheel={(e) => {
          if ((e.target as HTMLElement).closest(".chart-zoom, .chart-expand, .editor-save")) return;
          e.preventDefault();
          const box = vp.current;
          if (!box) return;
          const r = box.getBoundingClientRect();
          zoomBy(e.deltaY > 0 ? -0.1 : 0.1, e.clientX - r.left, e.clientY - r.top);
        }}
        onPointerDown={(e) => {
          if ((e.target as HTMLElement).closest(".chart-node, .group-toggle, .group-title, .group-title-hit, .group-metrics, .group-test, .node-nest, .chart-zoom, .chart-expand, .editor-save, .chart-edge-wrap, .anchor-handle")) return;
          if (!pan) return;
          const ox = e.clientX - t.current.x;
          const oy = e.clientY - t.current.y;
          const move = (ev: PointerEvent) => {
            t.current = { x: ev.clientX - ox, y: ev.clientY - oy };
            apply();
          };
          const up = () => {
            window.removeEventListener("pointermove", move);
            window.removeEventListener("pointerup", up);
          };
          window.addEventListener("pointermove", move);
          window.addEventListener("pointerup", up);
        }}
      >
        {extra}
        <div className="chart-zoom">
          <button type="button" className="btn" onClick={() => zoomBy(-0.12)}>
            −
          </button>
          <span className="zoom-label">{Math.round(z.current * 100)}%</span>
          <button type="button" className="btn" onClick={() => zoomBy(0.12)}>
            +
          </button>
          <button type="button" className="btn" onClick={() => fit()}>
            复位
          </button>
        </div>
        <div className="chart-scaler" ref={inner}>
          {children}
        </div>
      </div>
    </div>
  );
}
