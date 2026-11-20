import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, type Me } from "../api";
import { MigrationBanner } from "../migrate";
import {
  ChartStage,
  TopologyChart,
  type AdoptHub,
  type ChartEdge,
  type ChartGraph,
  type ChartGroup,
  type ChartNode,
} from "../chart";
import { defaultBoard, domainsFor, editorPath, homeHref, moduleHref } from "../boards";
import { ShieldIcon } from "../icons";

type Graph = ChartGraph & { can_protect?: boolean };
type Snake = { features?: Record<string, { duration: number | null }> };
type Tool = "select" | "pan" | "link";
type Sel = { kind: "node" | "edge" | "group"; id: string };

function keepSel(prev: Sel | null, nodes: ChartNode[], edges: ChartEdge[], groups: ChartGroup[]): Sel | null {
  if (!prev) return null;
  if (prev.kind === "node" && nodes.some((n) => n.id === prev.id)) return prev;
  if (prev.kind === "group" && groups.some((g) => g.id === prev.id)) return prev;
  if (prev.kind === "edge") {
    const [from, to] = prev.id.split("\t");
    if (from && to && edges.some((e) => e.from === from && e.to === to)) return prev;
  }
  return null;
}

function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

function newId(product: string, used: Set<string>): string {
  const p = (product || "m").replace(/[^a-z0-9]/gi, "").slice(0, 8).toLowerCase() || "m";
  let id = "";
  do {
    id = `${p}_${Math.random().toString(16).slice(2, 10)}`;
  } while (used.has(id));
  return id;
}

function nextTitle(nodes: ChartNode[]): string {
  const used = new Set(nodes.map((n) => n.title));
  if (!used.has("新功能")) return "新功能";
  let i = 2;
  while (used.has(`新功能 ${i}`)) i += 1;
  return `新功能 ${i}`;
}

function Ico({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function EditorPage() {
  const { product = "usp" } = useParams();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const domainQ = sp.get("domain");
  const groupScope = (sp.get("group") || "").trim();
  const board =
    sp.get("board") ||
    (domainQ && domainsFor(product).find((d) => d.id === domainQ)?.rel_path) ||
    defaultBoard(product);
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me"), staleTime: 0 });
  const docLocked = Boolean(me.data?.migrated_url);
  const q = useQuery({
    queryKey: ["graph", product, board],
    queryFn: () => api<Graph>(`/api/v1/products/${product}/graph?board=${encodeURIComponent(board)}`),
  });
  const snake = useQuery({
    queryKey: ["hours-snake", product],
    queryFn: () => api<Snake>(`/api/v1/products/${product}/hours/summary`),
  });
  const [nodes, setNodes] = useState<ChartNode[]>([]);
  const [edges, setEdges] = useState<ChartEdge[]>([]);
  const [groups, setGroups] = useState<ChartGroup[]>([]);
  const [tool, setTool] = useState<Tool>("select");
  const [sel, setSel] = useState<Sel | null>(null);
  const [help, setHelp] = useState(false);
  const [status, setStatus] = useState("");
  const undo = useRef<{ nodes: ChartNode[]; edges: ChartEdge[]; groups: ChartGroup[] }[]>([]);
  const draftRef = useRef({ nodes, edges, groups });
  draftRef.current = { nodes, edges, groups };
  const origProtect = useRef<Map<string, boolean>>(new Map());
  const groupDragSnap = useRef(false);
  const edgeAnchorSnap = useRef(false);
  useEffect(() => {
    if (!q.data) return;
    const nextNodes = q.data.nodes;
    const nextEdges = q.data.edges || [];
    const nextGroups = q.data.groups || [];
    const flags = new Map<string, boolean>();
    for (const n of nextNodes) flags.set(n.id, Boolean(n.admin_protected));
    for (const g of nextGroups) flags.set(g.id, Boolean(g.admin_protected));
    origProtect.current = flags;
    setNodes(nextNodes);
    setEdges(nextEdges);
    setGroups(nextGroups);
    setSel((prev) => keepSel(prev, nextNodes, nextEdges, nextGroups));
    undo.current = [];
  }, [q.data]);
  const save = useMutation({
    mutationFn: async () => {
      const { nodes: ns, edges: es, groups: gs } = draftRef.current;
      const rel = q.data?.board || board;
      await api(`/api/v1/products/${product}/boards/${rel}`, {
        method: "PUT",
        body: JSON.stringify({
          doc: {
            node_w: q.data?.node_w || 158,
            node_h: q.data?.node_h || 46,
            nodes: ns,
            edges: es,
            groups: gs,
          },
        }),
      });
      if (!q.data?.can_protect) return;
      const orig = origProtect.current;
      const seen = new Set<string>();
      const jobs: Promise<unknown>[] = [];
      const skip = (kind?: string) => kind === "product_hub" || kind === "product_release";
      const queue = (id: string, on: boolean, kind?: string, linked?: boolean) => {
        if (!id || skip(kind) || linked === false || seen.has(id)) return;
        seen.add(id);
        const was = orig.has(id) ? Boolean(orig.get(id)) : false;
        if (was === on) return;
        jobs.push(
          api(`/api/v1/products/${product}/modules/${id}/protect`, {
            method: "PUT",
            body: JSON.stringify({ protected: on }),
          }),
        );
      };
      for (const n of ns) queue(n.id, Boolean(n.admin_protected), n.kind);
      for (const g of gs) queue(g.id, Boolean(g.admin_protected), g.kind, g.linked);
      if (jobs.length) await Promise.all(jobs);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["graph", product] });
      qc.invalidateQueries({ queryKey: ["module", product] });
      setStatus("已保存");
    },
    onError: (e) => setStatus(e instanceof ApiError ? e.detail : "保存失败"),
  });

  function snapshot() {
    undo.current.push({ nodes: clone(nodes), edges: clone(edges), groups: clone(groups) });
    if (undo.current.length > 40) undo.current.shift();
  }
  function toggleProtect() {
    if (!sel || (sel.kind !== "node" && sel.kind !== "group")) return;
    const id = sel.id;
    const cur =
      sel.kind === "node" ? nodes.find((n) => n.id === id) : groups.find((g) => g.id === id);
    if (!cur) return;
    snapshot();
    const on = !cur.admin_protected;
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, admin_protected: on, can_delete: !on } : n)));
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, admin_protected: on, can_delete: !on } : g)));
    setStatus(on ? "已保护" : "已取消保护");
  }
  function doUndo() {
    const prev = undo.current.pop();
    if (!prev) return;
    setNodes(prev.nodes);
    setEdges(prev.edges);
    setGroups(prev.groups);
    setSel(null);
    setStatus("已撤回");
  }
  function addNode() {
    snapshot();
    const used = new Set([...nodes.map((n) => n.id), ...groups.map((g) => g.id)]);
    const id = newId(product, used);
    const nw = q.data?.node_w || 158;
    const nh = q.data?.node_h || 46;
    const host = groupScope ? groups.find((g) => g.id === groupScope) : null;
    const gsel = sel?.kind === "group" ? groups.find((g) => g.id === sel.id) : host;
    const pool = host ? nodes.filter((n) => (host.members || []).includes(n.id)) : nodes;
    let x = 40;
    let y = 40;
    if (pool.length) {
      x = Math.max(...pool.map((n) => n.x)) + nw + 24;
      y = Math.min(...pool.map((n) => n.y));
    }
    if (gsel && !pool.length) {
      x = (gsel.x || 0) + 24;
      y = (gsel.y || 0) + 36;
    }
    const n: ChartNode = { id, title: nextTitle(nodes), x, y, w: nw, h: nh, band: "pre", progress: 0 };
    setNodes((prev) => [...prev, n]);
    if (gsel) {
      setGroups((prev) => prev.map((g) => (g.id === gsel.id ? { ...g, members: [...(g.members || []), id] } : g)));
    }
    setSel({ kind: "node", id });
    setStatus("已添加");
  }
  function delSel() {
    if (!sel) return;
    snapshot();
    if (sel.kind === "edge") {
      const [from, to] = sel.id.split("\t");
      setEdges((prev) => prev.filter((e) => !(e.from === from && e.to === to)));
    } else if (sel.kind === "group") {
      if (groupScope && sel.id === groupScope) return;
      const g = groups.find((x) => x.id === sel.id);
      if (g && (g.can_delete === false || g.kind === "product_hub" || g.kind === "product_release")) {
        setStatus((g.admin_protected && q.data?.can_protect) ? "已保护" : "不可删除");
        undo.current.pop();
        return;
      }
      setGroups((prev) => prev.filter((x) => x.id !== sel.id));
      setEdges((prev) => prev.filter((e) => e.from !== sel.id && e.to !== sel.id));
    } else {
      const n = nodes.find((x) => x.id === sel.id);
      if (n && n.can_delete === false) {
        setStatus((n.admin_protected && q.data?.can_protect) ? "已保护" : "不可删除");
        undo.current.pop();
        return;
      }
      const id = sel.id;
      setNodes((prev) => prev.filter((x) => x.id !== id));
      setEdges((prev) => prev.filter((e) => e.from !== id && e.to !== id));
      setGroups((prev) => prev.map((g) => ({ ...g, members: (g.members || []).filter((m) => m !== id) })));
    }
    setSel(null);
    setStatus("已删除");
  }
  function stripHubMemberEdges(es: ChartEdge[], hubId: string, ids: string[]): ChartEdge[] {
    const mem = new Set(ids);
    return es.filter((e) => !((e.from === hubId && mem.has(e.to)) || (e.to === hubId && mem.has(e.from))));
  }
  function pruneGroups(gs: ChartGroup[]): ChartGroup[] {
    return gs.filter((g) => (g.members || []).length > 0 || (groupScope && g.id === groupScope) || g.kind === "product_hub");
  }
  async function moveIntoNested(hub: ChartNode, memberIds: string[]) {
    const rel = hub.nested_board;
    if (!rel) return;
    const { nodes: cur, edges: curE, groups: curG } = draftRef.current;
    const moving = cur.filter((n) => memberIds.includes(n.id));
    if (!moving.length) return;
    snapshot();
    try {
      const nested = await api<Graph>(`/api/v1/products/${product}/graph?board=${encodeURIComponent(rel)}`);
      const nw = nested.node_w || 158;
      const used = new Set(nested.nodes.map((n) => n.id));
      const toAdd = moving.filter((n) => !used.has(n.id));
      let x = 40;
      let y = 40;
      if (nested.nodes.length) {
        x = Math.max(...nested.nodes.map((n) => n.x)) + nw + 24;
        y = Math.min(...nested.nodes.map((n) => n.y));
      }
      const placed = toAdd.map((n, i) => ({ ...n, x, y: y + i * ((n.h || nested.node_h || 46) + 16) }));
      const nextNestedNodes = [...nested.nodes, ...placed];
      const persistGroups = (nested.groups || [])
        .filter((g) => g.id !== hub.id)
        .map((g) => ({ ...g, members: [...(g.members || [])] }));
      await api(`/api/v1/products/${product}/boards/${rel}`, {
        method: "PUT",
        body: JSON.stringify({
          doc: {
            node_w: nested.node_w || 158,
            node_h: nested.node_h || 46,
            nodes: nextNestedNodes,
            edges: nested.edges || [],
            groups: persistGroups,
          },
        }),
      });
      const idset = new Set(memberIds);
      const nextNodes = cur.filter((n) => !idset.has(n.id));
      const nextGroups = pruneGroups(curG.map((g) => ({ ...g, members: (g.members || []).filter((m) => !idset.has(m)) })));
      const nextEdges = curE.filter((e) => !idset.has(e.from) && !idset.has(e.to));
      await api(`/api/v1/products/${product}/boards/${q.data?.board || board}`, {
        method: "PUT",
        body: JSON.stringify({
          doc: {
            node_w: q.data?.node_w || 158,
            node_h: q.data?.node_h || 46,
            nodes: nextNodes,
            edges: nextEdges,
            groups: nextGroups,
          },
        }),
      });
      setNodes(nextNodes);
      setGroups(nextGroups);
      setEdges(nextEdges);
      setSel(null);
      qc.invalidateQueries({ queryKey: ["graph", product] });
      setStatus("已移入子拓扑");
    } catch (e) {
      setStatus(e instanceof ApiError ? e.detail : "移入失败");
    }
  }
  function adoptInto(ids: string[], hub: AdoptHub) {
    const { nodes: cur, groups: curG } = draftRef.current;
    const members = [...new Set(ids)].filter((id) => id && id !== hub.id && cur.some((n) => n.id === id));
    if (!members.length) return;
    if (hub.kind === "group") {
      const g = curG.find((x) => x.id === hub.id);
      if (!g) return;
      if (members.every((id) => (g.members || []).includes(id))) return;
      snapshot();
      setGroups((prev) =>
        pruneGroups(
          prev.map((x) => {
            const mems = (x.members || []).filter((m) => !members.includes(m));
            if (x.id === hub.id) return { ...x, members: [...new Set([...mems, ...members])], w: 0, h: 0 };
            return { ...x, members: mems };
          }),
        ),
      );
      setEdges((prev) => stripHubMemberEdges(prev, hub.id, members));
      setSel({ kind: "group", id: hub.id });
      setStatus("已纳入");
      return;
    }
    const target = cur.find((n) => n.id === hub.id);
    if (!target) return;
    if (target.kind === "product_hub" || target.kind === "product_release") return;
    if (target.nested_board) {
      void moveIntoNested(target, members);
      return;
    }
    snapshot();
    setNodes((prev) => prev.filter((n) => n.id !== hub.id));
    setGroups((prev) => {
      const cleaned = prev.map((x) => ({
        ...x,
        members: (x.members || []).filter((m) => m !== hub.id && !members.includes(m)),
      }));
      const existing = cleaned.find((x) => x.id === hub.id);
      if (existing) {
        return pruneGroups(
          cleaned.map((x) =>
            x.id === hub.id ? { ...x, members: [...new Set([...(x.members || []), ...members])], w: 0, h: 0 } : x,
          ),
        );
      }
        return pruneGroups([
        ...cleaned,
        {
          id: hub.id,
          title: target.title,
          x: target.x,
          y: target.y,
          w: 0,
          h: 0,
          members,
          admin_protected: target.admin_protected,
          can_delete: target.can_delete,
          linked: true,
        },
      ]);
    });
    setEdges((prev) => stripHubMemberEdges(prev, hub.id, members));
    setSel({ kind: "group", id: hub.id });
    setStatus("已建族");
  }
  function onLink(from: string, to: string) {
    if (from === to) return;
    if (edges.some((e) => e.from === from && e.to === to)) return;
    snapshot();
    setEdges((prev) => [...prev, { from, to }]);
    setStatus("已连线");
  }
  function onEdgeAnchor(from: string, to: string, patch: Pick<ChartEdge, "from_side" | "to_side" | "from_t" | "to_t">) {
    if (!q.data?.can_edit || docLocked) return;
    if (!edgeAnchorSnap.current) {
      snapshot();
      edgeAnchorSnap.current = true;
    }
    setEdges((prev) => prev.map((e) => (e.from === from && e.to === to ? { ...e, ...patch } : e)));
  }
  function patchNode(id: string, patch: Partial<ChartNode>) {
    setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, ...patch } : n)));
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT") return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (q.data?.can_edit && !docLocked) save.mutate();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (q.data?.can_edit && !docLocked) doUndo();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        if (q.data?.can_edit && !docLocked) delSel();
      } else if (e.key === "Escape") {
        setSel(null);
        setHelp(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, nodes, edges, groups, docLocked, q.data?.can_edit]);

  if (q.isLoading) return <div className="editor-page"><p style={{ padding: 16 }}>…</p></div>;
  if (q.isError) return <div className="editor-page"><p className="err" style={{ padding: 16 }}>无法打开画板</p></div>;
  const g = q.data!;
  const canBoard = Boolean(g.can_edit) && !docLocked;
  const scopeG = groupScope ? groups.find((x) => x.id === groupScope) : null;
  const focusIds = scopeG ? new Set([groupScope, ...(scopeG.members || [])]) : null;
  const viewNodes = focusIds ? nodes.filter((n) => focusIds.has(n.id)) : nodes;
  const viewGroups = groupScope ? groups.filter((g) => g.id === groupScope) : groups;
  const viewEdges = focusIds
    ? edges.filter((e) => focusIds.has(e.from) && focusIds.has(e.to))
    : edges;
  const draft: ChartGraph = { ...g, nodes: viewNodes, edges: viewEdges, groups: viewGroups };
  const selNode = sel?.kind === "node" ? nodes.find((n) => n.id === sel.id) : null;
  const selGroup = sel?.kind === "group" ? groups.find((x) => x.id === sel.id) : null;
  const selProtect =
    selNode && selNode.kind !== "product_hub" && selNode.kind !== "product_release"
      ? selNode
      : selGroup && selGroup.linked !== false && selGroup.kind !== "product_hub" && selGroup.kind !== "product_release"
        ? selGroup
        : null;
  const nodeBlocked = Boolean(sel?.kind === "node" && selNode && selNode.can_delete === false);
  const groupBlocked = Boolean(
    selGroup && (selGroup.can_delete === false || selGroup.kind === "product_hub" || selGroup.kind === "product_release"),
  );
  const tools: { id: Tool; label: string; d: string }[] = [
    { id: "select", label: "选择", d: "M4 4l7 16 2.5-7.5L21 10z" },
    { id: "pan", label: "平移", d: "M18 11V6a2 2 0 0 0-4 0v2M14 10V5a2 2 0 0 0-4 0v3M10 10.5V6a2 2 0 0 0-4 0v8M18 8a2 2 0 1 1 4 0v5a8 8 0 0 1-16 0v-1" },
    { id: "link", label: "连线", d: "M4 17C4 7 20 17 20 7" },
  ];
  return (
    <div className="editor-page">
      {docLocked ? (
        <div style={{ padding: "10px 12px 0" }}>
          <MigrationBanner url={me.data?.migrated_url} />
        </div>
      ) : null}
      <header className="editor-bar">
        {(canBoard ? tools : tools.filter((t) => t.id !== "link")).map((t) => (
          <button
            key={t.id}
            type="button"
            className={`btn-icon${tool === t.id ? " is-on" : ""}`}
            aria-label={t.label}
            aria-pressed={tool === t.id}
            title={t.label}
            onClick={() => setTool(t.id)}
          >
            <Ico d={t.d} />
          </button>
        ))}
        {canBoard ? (
          <>
            <span className="sep" />
            <button type="button" className="btn-icon" aria-label="添加" title="添加" onClick={addNode}>
              <Ico d="M12 5v14M5 12h14" />
            </button>
            <button type="button" className="btn-icon danger" aria-label="删除" title="删除" onClick={delSel} disabled={!sel || nodeBlocked || groupBlocked}>
              <Ico d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
            </button>
            {g.can_protect ? (
              <button
                type="button"
                className={`btn-icon is-shield${selProtect?.admin_protected ? " is-on" : ""}`}
                aria-label={selProtect?.admin_protected ? "取消保护" : "保护"}
                title={selProtect?.admin_protected ? "取消保护" : "保护"}
                disabled={!selProtect || save.isPending}
                onClick={toggleProtect}
              >
                <ShieldIcon />
              </button>
            ) : null}
            <button type="button" className="btn-icon" aria-label="撤回" title="撤回" onClick={doUndo} disabled={!undo.current.length}>
              <Ico d="M3 7v6h6M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13" />
            </button>
            <span className="sep" />
            <button type="button" className="btn-icon" aria-label="保存" title="保存" onClick={() => save.mutate()} disabled={save.isPending}>
              <Ico d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2zM17 21v-8H7v8M7 3v5h8" />
            </button>
          </>
        ) : null}
        <span className="sep" />
        <button type="button" className="btn-icon" aria-label="返回" title="返回" onClick={() => nav(groupScope ? moduleHref(product, groupScope, board) : homeHref(product, board))}>
          <Ico d="M19 12H5M12 19l-7-7 7-7" />
        </button>
        <div className="header-end">
          <span className="editor-status">{status}</span>
          <button type="button" className="btn-q" aria-label="帮助" aria-expanded={help} onClick={() => setHelp(!help)}>
            ?
          </button>
          {help ? (
            <div className="help-pop" role="dialog">
              <ul>
                <li>选择：点节点 / 边 / 包络标题栏</li>
                <li>选中边后拖两端圆点改吸附</li>
                <li>拖包络标题栏移动整族</li>
                <li>连线：选连线后从起点拖到终点</li>
                <li>拖到虚线包络或模块上纳入内部</li>
                <li>Ctrl/⌘+S 保存 · Ctrl/⌘+Z 撤回 · Delete 删除</li>
                {g.can_protect ? <li>保护模块</li> : null}
              </ul>
            </div>
          ) : null}
        </div>
      </header>
      <div className="editor-main">
        <ChartStage
          fitKey={`${product}:${g.board}:${groupScope}:${viewNodes.length}`}
          pan={tool !== "link"}
          linkCursor={tool === "link"}
        >
          <TopologyChart
            graph={draft}
            nodes={viewNodes}
            tool={tool}
            selected={sel}
            selectGroups
            snakeDurations={Object.fromEntries(Object.entries(snake.data?.features || {}).map(([k, v]) => [k, v.duration]))}
            onSelect={setSel}
            onGroupClick={(id) => setSel({ kind: "group", id })}
            onNested={(n) => n.nested_board && nav(editorPath(product, n.nested_board))}
            onTestClick={async () => {
              const w = window.open("about:blank", "_blank");
              if (w) w.opener = null;
              try {
                const j = await api<{ url: string }>(`/api/v1/products/${product}/test-sso`, { method: "POST" });
                if (w) w.location.replace(j.url);
                else window.location.assign(j.url);
              } catch (e) {
                w?.close();
                setStatus(e instanceof ApiError ? e.detail : "无法打开自测系统");
              }
            }}
            onNodeDrag={(id, x, y) => {
              if (!canBoard || tool !== "select") return;
              setNodes((prev) => prev.map((n) => (n.id === id ? { ...n, x, y } : n)));
            }}
            onGroupDrag={(nextNodes, nextGroups) => {
              if (!canBoard || tool !== "select") return;
              if (!groupDragSnap.current) {
                snapshot();
                groupDragSnap.current = true;
              }
              const np = Object.fromEntries(nextNodes.map((n) => [n.id, n]));
              const gp = Object.fromEntries(nextGroups.map((x) => [x.id, x]));
              setNodes((prev) => prev.map((n) => (np[n.id] ? { ...n, x: np[n.id].x, y: np[n.id].y } : n)));
              setGroups((prev) => prev.map((g) => (gp[g.id] ? { ...g, x: gp[g.id].x, y: gp[g.id].y } : g)));
            }}
            onGroupDragEnd={() => {
              if (groupDragSnap.current) setStatus("已移动整族");
              groupDragSnap.current = false;
            }}
            onAdopt={canBoard && tool === "select" ? adoptInto : undefined}
            onLink={canBoard ? onLink : undefined}
            onEdgeAnchor={canBoard && tool === "select" ? onEdgeAnchor : undefined}
            onEdgeAnchorEnd={() => {
              if (edgeAnchorSnap.current) setStatus("已调整边吸附点");
              edgeAnchorSnap.current = false;
            }}
          />
        </ChartStage>
        {canBoard && selNode ? (
          <aside className="editor-props">
            <h2>属性</h2>
            <label>
              标题
              <input value={selNode.title} onChange={(e) => patchNode(selNode.id, { title: e.target.value })} />
            </label>
            <label>
              说明
              <input value={selNode.sub || ""} onChange={(e) => patchNode(selNode.id, { sub: e.target.value })} />
            </label>
            {g.can_protect && selNode.admin_protected ? <p className="muted">已保护</p> : null}
          </aside>
        ) : null}
      </div>
    </div>
  );
}
