import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me } from "../api";
import { Confirm } from "../dialog";
import { ChartStage, ExpandEditLink, TopologyChart, type ChartGraph } from "../chart";
import { DocHtml } from "../doc";
import { DocViews } from "../doc-views";
import { ModuleHoursPanel, moduleHoursPanelOpen, type ModuleHours } from "../hours-bar";
import { bandLabel, LC_BANDS, lcBandRank, REQ_STATUS } from "../labels";
import { cachedModuleTitle, prefetchModule } from "../prefetch";
import { fmtWhen } from "../req";
import { CheckIcon, CloseIcon, ShieldIcon } from "../icons";
import { ProductReleasePage, LcChip, LcProgress } from "./Product";
import { defaultBoard, editorPath, moduleHref } from "../boards";

type Hours = ModuleHours;

type Page = {
  title: string;
  html: string;
  markdown: string;
  version: string;
  versions?: string[];
  latest_version?: string;
  kind?: string;
  can_edit: boolean;
  can_edit_lifecycle?: boolean;
  can_protect?: boolean;
  can_delete_version?: boolean;
  admin_protected?: boolean;
  version_protected?: boolean;
  nested_board?: string | null;
  board_rel?: string | null;
  can_log_hours?: boolean;
  hours?: Hours | null;
  revision_count?: number;
  lifecycle: {
    band: string;
    progress: number | null;
    expected_hours: number | null;
    derived?: boolean;
  } | null;
};

type ReqItem = { id: string; title: string; status: string };
type Links = { items: ReqItem[] };
type ReqList = { items: ReqItem[] };
type Revs = { can_restore: boolean; items: { id: number; author: string; created_at: string | null; version?: string }[] };

function VerLatest() {
  return <span className="ver-latest">latest</span>;
}

function VerSwitch({
  value,
  versions,
  latest,
  onChange,
}: {
  value: string;
  versions: string[];
  latest: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onPtr(e: PointerEvent) {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPtr);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPtr);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className={`ver-switch${open ? " is-open" : ""}`} ref={wrap}>
      <button
        type="button"
        className="ver-switch-btn"
        aria-label="功能版本"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span>{value}</span>
        {value === latest ? <VerLatest /> : null}
      </button>
      {open ? (
        <ul className="ver-switch-list" role="listbox" aria-label="功能版本">
          {versions.map((v) => (
            <li key={v} role="option" aria-selected={v === value}>
              <button
                type="button"
                className={v === value ? "is-on" : ""}
                onClick={() => {
                  onChange(v);
                  setOpen(false);
                }}
              >
                <span>{v}</span>
                {v === latest ? <VerLatest /> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function LinkGlyph() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  );
}

function WriteMark() {
  return (
    <span className="write-mark" aria-label="Ai-辅助">
      Ai-辅助
    </span>
  );
}

type NestedGraph = ChartGraph & {
  skip?: boolean;
  empty?: boolean;
  source?: string;
  editor_board?: string | null;
  editor_group?: string | null;
  can_edit?: boolean;
};

type Snake = { features?: Record<string, { duration: number | null }> };

function NestedTopo({
  product,
  moduleId,
  kind,
  docLocked,
}: {
  product: string;
  moduleId: string;
  kind?: string;
  docLocked?: boolean;
}) {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const qc = useQueryClient();
  const skip = kind === "product_hub" || kind === "product_release";
  const q = useQuery({
    queryKey: ["nested-graph", product, moduleId],
    queryFn: () => api<NestedGraph>(`/api/v1/products/${product}/modules/${moduleId}/nested-graph`),
    enabled: !skip,
    staleTime: 20_000,
  });
  const snake = useQuery({
    queryKey: ["hours-snake", product],
    queryFn: () => api<Snake>(`/api/v1/products/${product}/hours/summary`),
    enabled: !skip,
  });
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  if (skip || q.data?.skip) return null;
  if (q.isLoading || q.isError || !q.data) return null;
  const g = q.data;
  const empty = !g.nodes.length;
  const metricsMap = kind === "metrics" && moduleId === `${product}_metrics`;
  if (kind === "metrics" && empty && !metricsMap) return null;
  const open = metricsMap ? true : (userOpen ?? !empty);
  const canBoard = Boolean(g.can_edit) && !docLocked;
  const board = g.editor_board || g.board;
  const editorHref = editorPath(product, board, g.editor_group);
  return (
    <section className={`nested-chart${open ? "" : " is-collapsed"}${empty ? " is-empty" : ""}${metricsMap ? " is-metrics" : ""}`}>
      {metricsMap ? null : (
        <div className="nest-head">
          <button
            type="button"
            className="nest-toggle"
            aria-expanded={open}
            onClick={() => setUserOpen(!open)}
          >
            <span className="mark">{open ? "−" : "+"}</span>
            子功能拓扑
          </button>
          {empty ? <span className="nest-badge">空</span> : null}
        </div>
      )}
      <div className="nest-body">
        {empty ? (
          <div className="chart-panel">
            <div className="chart-viewport nest-empty">
              {canBoard ? <ExpandEditLink to={editorHref} /> : null}
            </div>
          </div>
        ) : (
          <ChartStage
            compact
            fitKey={`${product}:${moduleId}:${g.board}`}
            extra={canBoard ? <ExpandEditLink to={editorHref} /> : null}
          >
            <TopologyChart
              graph={g}
              snakeDurations={Object.fromEntries(
                Object.entries(snake.data?.features || {}).map(([k, v]) => [k, v.duration]),
              )}
              onNodeClick={(n) => nav(moduleHref(product, n.id, sp.get("board") || g.board))}
              onGroupClick={(gid) => nav(moduleHref(product, gid, sp.get("board") || g.board))}
              onPrefetch={(id) => prefetchModule(qc, product, id)}
              onNested={(n) => n.nested_board && nav(`/${product}?board=${encodeURIComponent(n.nested_board)}`)}
            />
          </ChartStage>
        )}
      </div>
    </section>
  );
}

export function ModulePage() {
  const { product = "usp", id = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const verQ = sp.get("v") || undefined;
  const qc = useQueryClient();
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkAsk, setLinkAsk] = useState<{ id: string; title: string } | null>(null);
  const [reqQ, setReqQ] = useState("");
  const [revOpen, setRevOpen] = useState(false);
  const [lcBand, setLcBand] = useState("pre");
  const [lcProg, setLcProg] = useState(0);
  const [lcEdit, setLcEdit] = useState(false);
  const linkWrap = useRef<HTMLDivElement>(null);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const docLocked = Boolean(me.data?.migrated_url);
  const q = useQuery({
    queryKey: ["module", product, id, verQ || ""],
    queryFn: () => {
      const qs = verQ ? `?version=${encodeURIComponent(verQ)}` : "";
      return api<Page>(`/api/v1/products/${product}/modules/${id}${qs}`);
    },
    staleTime: 20_000,
  });
  useEffect(() => {
    const rel = q.data?.board_rel;
    if (!rel || rel === defaultBoard(product)) return;
    setSp(
      (prev) => {
        if (prev.get("board")) return prev;
        const n = new URLSearchParams(prev);
        n.set("board", rel);
        return n;
      },
      { replace: true },
    );
  }, [q.data?.board_rel, product, setSp]);
  const links = useQuery({
    queryKey: ["mod-links", product, id],
    queryFn: () => api<Links>(`/api/v1/products/${product}/features/${id}/requirements`),
    enabled: q.data != null && q.data.kind !== "product_release" && q.data.kind !== "product_hub" && q.data.kind !== "metrics",
    staleTime: 20_000,
  });
  const allReqs = useQuery({
    queryKey: ["reqs", product],
    queryFn: () => api<ReqList>(`/api/v1/products/${product}/requirements`),
    enabled: linkOpen,
  });
  const revs = useQuery({
    queryKey: ["revs", product, id],
    queryFn: () => api<Revs>(`/api/v1/products/${product}/modules/${id}/revisions`),
    enabled: q.data != null && q.data.kind !== "product_release",
    staleTime: 20_000,
  });
  const lc = useMutation({
    mutationFn: (body: { band: string; progress: number; version?: string }) =>
      api(`/api/v1/products/${product}/modules/${id}/lifecycle`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      setLcEdit(false);
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  const setLinks = useMutation({
    mutationFn: (ids: string[]) =>
      api(`/api/v1/products/${product}/features/${id}/requirements`, {
        method: "PUT",
        body: JSON.stringify({ requirement_ids: ids }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mod-links", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      qc.invalidateQueries({ queryKey: ["req"] });
      qc.invalidateQueries({ queryKey: ["inbox-pending-links"] });
    },
  });
  const restore = useMutation({
    mutationFn: (revision_id: number) =>
      api(`/api/v1/products/${product}/modules/${id}/revisions/restore`, {
        method: "POST",
        body: JSON.stringify({ revision_id }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["revs", product, id] });
    },
  });
  const saveFig = useMutation({
    mutationFn: (markdown: string) =>
      api(`/api/v1/products/${product}/modules/${id}/document`, {
        method: "PUT",
        body: JSON.stringify({ markdown, version: q.data?.version, snapshot: true }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["revs", product, id] });
    },
  });
  const protectVer = useMutation({
    mutationFn: (on: boolean) =>
      api(`/api/v1/products/${product}/modules/${id}/versions/${encodeURIComponent(q.data?.version || "")}/protect`, {
        method: "PUT",
        body: JSON.stringify({ protected: on }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["module", product, id] });
    },
  });
  useEffect(() => {
    setLinkOpen(false);
    setReqQ("");
    setLcEdit(false);
  }, [id, verQ]);
  useEffect(() => {
    const life = q.data?.lifecycle;
    if (!life) return;
    setLcBand(life.band || "pre");
    setLcProg(life.band === "done" ? 100 : Number(life.progress ?? 0));
  }, [q.data?.lifecycle]);
  useEffect(() => {
    if (!linkOpen) return;
    function onPtr(e: PointerEvent) {
      if (!linkWrap.current?.contains(e.target as Node)) setLinkOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setLinkOpen(false);
    }
    document.addEventListener("pointerdown", onPtr);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPtr);
      document.removeEventListener("keydown", onKey);
    };
  }, [linkOpen]);
  useEffect(() => {
    if (!lcEdit) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      const cur = q.data?.lifecycle;
      if (cur) {
        setLcBand(cur.band || "pre");
        setLcProg(cur.band === "done" ? 100 : Number(cur.progress ?? 0));
      }
      setLcEdit(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [lcEdit, q.data?.lifecycle]);
  if (q.isLoading) {
    const title = cachedModuleTitle(qc, product, id);
    return (
      <div className="mod-nav">
        <h1 className="page-title">{title || "…"}</h1>
      </div>
    );
  }
  if (q.isError) return <p className="err">无权访问或模块不存在</p>;
  if (q.data?.kind === "product_release") return <ProductReleasePage />;
  const p = q.data!;
  const isHub = p.kind === "product_hub";
  const isMetrics = p.kind === "metrics";
  const isMetricsHost = isMetrics && id === `${product}_metrics`;
  const life = p.lifecycle;
  function syncLcFrom(life: NonNullable<Page["lifecycle"]>) {
    setLcBand(life.band || "pre");
    setLcProg(life.band === "done" ? 100 : Number(life.progress ?? 0));
  }
  function onLc(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    lc.mutate({ band: lcBand, progress: lcBand === "done" ? 100 : lcProg, version: p.version });
  }
  function cancelLc() {
    if (life) syncLcFrom(life);
    setLcEdit(false);
  }
  const hrs = p.hours;
  const hoursSettled = Boolean(hrs?.settled) || (hrs?.band || "").toLowerCase() === "done";
  const showHours = moduleHoursPanelOpen(hrs);
  const canLink = ["admin", "engineer"].includes(me.data?.user.role || "");
  const linkedItems = links.data?.items || [];
  const linked = new Set(linkedItems.map((x) => x.id));
  const catalog = allReqs.data?.items || [];
  const needle = reqQ.trim().toLowerCase();
  const src = catalog.length ? catalog : linkedItems;
  const rows = (needle
    ? src.filter((c) => (c.title || "").toLowerCase().includes(needle) || c.id.toLowerCase().includes(needle))
    : src
  )
    .slice()
    .filter((c) => linked.has(c.id) || c.status === "pending_link" || c.status === "developing")
    .sort((a, b) => {
      const ao = linked.has(a.id) ? 0 : 1;
      const bo = linked.has(b.id) ? 0 : 1;
      if (ao !== bo) return ao - bo;
      return (a.title || a.id).localeCompare(b.title || b.id, "zh");
    });
  const vers = p.versions?.length ? p.versions : [p.version];
  const latest = p.latest_version || p.version;
  function onVer(next: string) {
    const n = new URLSearchParams(sp);
    if (next === latest) n.delete("v");
    else n.set("v", next);
    setSp(n, { replace: true });
  }
  const revItems = (revs.data?.items || []).filter((r) => !r.version || r.version === p.version);
  const revCount = p.revision_count ?? (revs.data ? revItems.length : 0);
  function linkReq(rid: string, title: string) {
    if (linked.has(rid)) return;
    setLinks.reset();
    setLinkAsk({ id: rid, title });
  }
  return (
    <>
      <div className="mod-nav">
        <h1 className="page-title">{p.title}</h1>
        {life && !lcEdit && !isMetrics ? (
          <LcChip
            band={life.band}
            progress={life.progress}
            label={
              life.derived && life.band !== "done"
                ? `子模块-${bandLabel(life.band, life.progress)}`
                : bandLabel(life.band, life.progress)
            }
            onClick={
              p.can_edit_lifecycle
                ? () => {
                    syncLcFrom(life);
                    setLcEdit(true);
                  }
                : undefined
            }
          />
        ) : null}
        {p.can_edit && !docLocked ? <WriteMark /> : null}
        {!isHub || p.version ? (
          <div className="mod-nav-end">
            {!isHub && !isMetrics ? (
              <div className="req-link-wrap" ref={linkWrap}>
                <button
                  type="button"
                  className={`req-link-btn${linkOpen ? " is-open" : ""}${linkedItems.length ? " has-links" : ""}`}
                  title="关联需求"
                  aria-label="关联需求"
                  aria-expanded={linkOpen}
                  onClick={() => setLinkOpen((v) => !v)}
                >
                  <LinkGlyph />
                  <span className="req-link-dot" />
                </button>
                {linkOpen ? (
                  <div className="req-link-pop" role="dialog" aria-label="关联需求">
                    <input
                      type="search"
                      value={reqQ}
                      onChange={(e) => setReqQ(e.target.value)}
                      placeholder="搜索需求"
                      autoComplete="off"
                      autoFocus
                    />
                    <div id="module-req-list">
                      {allReqs.isLoading && !catalog.length && !linkedItems.length ? (
                        <p className="req-link-empty">…</p>
                      ) : !rows.length ? (
                        <p className="req-link-empty">{catalog.length || linkedItems.length ? "无匹配" : "暂无需求"}</p>
                      ) : (
                        <ul className="req-link-list">
                          {rows.map((c) => {
                            const on = linked.has(c.id);
                            const st = REQ_STATUS[c.status] || c.status;
                            return (
                              <li key={c.id}>
                                <Link to={`/${product}/requirements/${c.id}`}>{c.title || c.id}</Link>
                                {st ? <span className="req-link-st"> · {st}</span> : null}
                                {canLink && !on ? (
                                  <button type="button" className="toggle" onClick={() => linkReq(c.id, c.title || c.id)} disabled={setLinks.isPending}>
                                    关联
                                  </button>
                                ) : on ? (
                                  <span className="toggle is-on is-static">已关联</span>
                                ) : null}
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                    {setLinks.isPending ? <span className="req-link-msg is-busy" /> : null}
                    {setLinks.isError ? (
                      <span className="req-link-msg err">{setLinks.error instanceof ApiError ? setLinks.error.detail : "失败"}</span>
                    ) : null}
                  </div>
                ) : null}
              </div>
            ) : null}
            {!isHub && !isMetrics ? (
              <VerSwitch value={p.version} versions={vers} latest={latest} onChange={onVer} />
            ) : p.version ? (
              <span className="chip ver-switch">{p.version}</span>
            ) : null}
            {!isHub && p.can_protect ? (
              <button
                type="button"
                className={`ver-act is-shield${p.version_protected ? " is-on" : ""}`}
                aria-label={p.version_protected ? "取消保护" : "保护"}
                title={p.version_protected ? "取消保护" : "保护"}
                onClick={() => protectVer.mutate(!p.version_protected)}
                disabled={protectVer.isPending}
              >
                <ShieldIcon />
              </button>
            ) : null}
          </div>
        ) : null}
        {p.can_edit_lifecycle && life && lcEdit ? (
          <form className="lc-bar" onSubmit={onLc}>
            <div className="lc-bands" role="group" aria-label="状态">
              {LC_BANDS.map((opt) => {
                const blocked =
                  me.data?.user.role !== "admin" && lcBandRank(opt.id) < lcBandRank(life.band);
                return (
                  <button
                    key={opt.id}
                    type="button"
                    className={lcBand === opt.id ? "is-on" : ""}
                    data-band={opt.id}
                    disabled={blocked}
                    onClick={() => {
                      setLcBand(opt.id);
                      if (opt.id === "done") setLcProg(100);
                      else if (life.band === "done") setLcProg(0);
                    }}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            {lcBand !== "done" ? <LcProgress value={lcProg} onChange={setLcProg} /> : null}
            <button
              type="submit"
              className="icon-act is-ok"
              aria-label="保存"
              disabled={
                lc.isPending ||
                (lcBand === (life.band || "pre") &&
                  (lcBand === "done" || lcProg === Number(life.progress ?? 0)))
              }
            >
              <CheckIcon />
            </button>
            <button type="button" className="icon-act is-off" aria-label="取消" onClick={cancelLc} disabled={lc.isPending}>
              <CloseIcon />
            </button>
            {lc.isError ? (
              <span className="err">{lc.error instanceof ApiError ? lc.error.detail : "失败"}</span>
            ) : null}
          </form>
        ) : null}
      </div>
      {isMetricsHost ? <NestedTopo product={product} moduleId={id} kind={p.kind} docLocked={docLocked} /> : null}
      {p.html || (showHours && hrs) ? (
        <div className={`detail-main${showHours ? "" : " no-hours"}`}>
          <div>
            {p.html ? (
              <div className="panel">
                <DocHtml
                  html={p.html}
                  canEdit={p.can_edit && !docLocked}
                  markdown={p.markdown}
                  onSave={(md) => saveFig.mutate(md)}
                  saving={saveFig.isPending}
                  saveError={saveFig.isError ? (saveFig.error instanceof ApiError ? saveFig.error.detail : "失败") : undefined}
                />
              </div>
            ) : null}
            <DocViews
              path={`/api/v1/products/${product}/modules/${id}/views${p.version ? `?version=${encodeURIComponent(p.version)}` : ""}`}
              enabled={Boolean(me.data?.can_protect)}
            />
            {!isMetricsHost ? <NestedTopo product={product} moduleId={id} kind={p.kind} docLocked={docLocked} /> : null}
          </div>
          {showHours && hrs ? (
            <ModuleHoursPanel
              product={product}
              moduleId={id}
              version={p.version}
              hours={hrs}
              username={me.data?.user.username}
              displayName={me.data?.user.name}
              totalOnly={hoursSettled}
            />
          ) : null}
        </div>
      ) : (
        <>
          <DocViews
            path={`/api/v1/products/${product}/modules/${id}/views${p.version ? `?version=${encodeURIComponent(p.version)}` : ""}`}
            enabled={Boolean(me.data?.can_protect)}
          />
          {!isMetricsHost ? <NestedTopo product={product} moduleId={id} kind={p.kind} docLocked={docLocked} /> : null}
        </>
      )}
      {(revCount || p.can_edit) ? (
        <div className={`rev-corner${revOpen ? " is-open" : ""}`}>
          <button type="button" className="rev-toggle" onClick={() => setRevOpen(!revOpen)}>
            修订
            <span className="rev-count">{revCount}</span>
          </button>
          {revOpen ? (
            <div className="rev-panel">
              <ul className="rev-list">
                {revItems.map((r) => (
                  <li key={r.id}>
                    <span className="rev-id">{r.author}</span>
                    <span className="rev-meta">
                      {fmtWhen(r.created_at)}
                      {revs.data?.can_restore && !docLocked ? (
                        <button type="button" className="btn" onClick={() => restore.mutate(r.id)} disabled={restore.isPending}>
                          回滚
                        </button>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
              {!revItems.length ? <p className="rev-empty">暂无修订</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {linkAsk ? (
        <Confirm
          title={`确认关联「${linkAsk.title}」需求`}
          note="该操作不可撤回"
          confirm="确定"
          pending={setLinks.isPending}
          error={setLinks.isError ? (setLinks.error instanceof ApiError ? setLinks.error.detail : "失败") : undefined}
          onCancel={() => setLinkAsk(null)}
          onConfirm={() => setLinks.mutate([...linked, linkAsk.id], { onSuccess: () => setLinkAsk(null) })}
        />
      ) : null}
    </>
  );
}
