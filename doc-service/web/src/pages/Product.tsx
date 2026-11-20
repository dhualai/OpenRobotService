import { FormEvent, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import { Confirm } from "../dialog";
import { HoursDualBar, fmtHours, personName } from "../hours-bar";
import { bandLabel } from "../labels";
import { CheckIcon, CloseIcon } from "../icons";

type CoverItem = {
  module_id: string;
  title: string;
  version: string;
  band?: string | null;
  progress?: number | null;
  week_invested?: number;
};
type TreeNode = {
  id: string;
  title: string;
  kind: string;
  versions: string[];
  coverable?: boolean;
  children: TreeNode[];
};
type ChangelogRow = {
  module_id: string;
  title: string;
  version: string;
  kind: string;
  prev_version?: string;
  note: string;
  label: string;
};
type Hours = {
  invested?: number;
  invested_total?: number;
  expected_hours: number | null;
  remaining_hours: number | null;
  hours_mode?: string;
  hours_view?: string;
  week?: string;
  week_invested?: number;
  by_user?: { user: string; name?: string; hours: number; expected_hours?: number | null }[];
};
type Page = {
  kind: string;
  title: string;
  tag: string | null;
  tag_unassigned: boolean;
  release_id: string | null;
  semver: string;
  published: boolean;
  is_dev: boolean;
  added: string;
  fixed: string;
  remark: string;
  changelog: ChangelogRow[];
  cover_items: CoverItem[];
  cover_tree: TreeNode[];
  can_edit_covers: boolean;
  can_publish: boolean;
  can_edit_notes: boolean;
  can_edit_lifecycle?: boolean;
  lifecycle?: {
    band: string;
    progress: number | null;
    label?: string;
    derived?: boolean;
    published?: boolean;
    can_publish?: boolean;
  } | null;
  hours?: Hours | null;
};

export function LcProgress({
  value,
  onChange,
}: {
  value: number;
  onChange: (n: number) => void;
}) {
  const v = Math.max(0, Math.min(100, Math.round(Number.isFinite(value) ? value : 0)));
  function set(raw: number) {
    onChange(Math.max(0, Math.min(100, Math.round(Number(raw) || 0))));
  }
  return (
    <span className="lc-progress">
      <input type="range" min={0} max={100} step={1} value={v} onChange={(e) => set(Number(e.target.value))} />
      <input className="lc-progress-num" type="number" min={0} max={100} step={1} value={v} onChange={(e) => set(Number(e.target.value))} />
      <span className="lc-progress-unit">%</span>
    </span>
  );
}

function lcTone(band?: string | null): "pre" | "dev" | "test" | "done" {
  const b = (band || "pre").toLowerCase();
  if (b === "done") return "done";
  if (b.includes("test") || b === "joint_test") return "test";
  if (b === "pre" || b === "req" || b === "design" || b === "review") return "pre";
  return "dev";
}

export function LcChip({
  band,
  progress,
  label,
  onClick,
}: {
  band?: string | null;
  progress?: number | null;
  label: string;
  onClick?: () => void;
}) {
  const tone = lcTone(band);
  const p = tone === "done" ? 100 : Math.max(0, Math.min(100, Number(progress) || 0));
  const cls = `chip lc-chip lc-${tone}${onClick ? " is-lc" : ""}`;
  const style = { ["--lc-p" as string]: `${p}%` };
  if (onClick) {
    return (
      <button type="button" className={cls} style={style} onClick={onClick}>
        {label}
      </button>
    );
  }
  return (
    <span className={cls} style={style}>
      {label}
    </span>
  );
}

function nodeHasCover(n: TreeNode, selected: Map<string, string>): boolean {
  if (selected.has(n.id)) return true;
  return (n.children || []).some((c) => nodeHasCover(c, selected));
}

function hoursPanelHasContent(j: Hours | null | undefined): boolean {
  if (!j || j.hours_view === "hidden") return false;
  const total = Number(j.invested_total ?? j.invested) || 0;
  if (j.hours_view === "total_only") return total > 0;
  const week = Number(j.week_invested) || 0;
  const rem = j.remaining_hours;
  const hasRem = rem != null && Number(rem) > 0;
  return total > 0 || week > 0 || hasRem || (j.by_user || []).length > 0;
}

function hoursTotal(j: Hours): number {
  return Number(j.invested_total ?? j.invested) || 0;
}

function weekHoursLabel(hours?: number | null): string {
  const w = Number(hours) || 0;
  if (w <= 0) return "";
  return `${Math.round(w * 100) / 100}h`;
}

function ProductNote({ text }: { text?: string | null }) {
  if (!text) return null;
  return (
    <div className="doc">
      <pre className="plain">{text}</pre>
    </div>
  );
}

function coverBadge(c: CoverItem, tab: "all" | "active"): string {
  const wh = weekHoursLabel(c.week_invested);
  if (tab === "active") return wh;
  return wh || bandLabel(c.band, c.progress);
}

function CoverTreeView({
  nodes,
  selected,
  statuses,
  product,
  depth = 0,
  defaultOpen = false,
}: {
  nodes: TreeNode[];
  selected: Map<string, string>;
  statuses: Record<string, string>;
  product: string;
  depth?: number;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <ul className="usp-covers-tree">
      {nodes.map((n) => {
        if (!nodeHasCover(n, selected)) return null;
        const kids = (n.children || []).filter((c) => nodeHasCover(c, selected));
        const isOpen = open[n.id] ?? defaultOpen;
        const ver = selected.get(n.id);
        return (
          <li key={n.id} className={`tree-item${isOpen && kids.length ? " is-open" : ""}`}>
            <div
              className={`tree-row${n.kind === "group" ? " is-group" : ""}`}
              style={{ paddingLeft: 6 + depth * 18 }}
            >
              {kids.length ? (
                <button type="button" className="twist" onClick={() => setOpen((s) => ({ ...s, [n.id]: !isOpen }))}>
                  {isOpen ? "▼" : "▶"}
                </button>
              ) : (
                <button type="button" className="twist" disabled>
                  ·
                </button>
              )}
              <div className="tree-main">
                {ver ? (
                  <span className="covers-hot">
                    <Link to={`/${product}/m/${n.id}`}>{n.title}</Link>
                    <span className="ver">{ver}</span>
                    {statuses[n.id] ? <span className="status">{statuses[n.id]}</span> : null}
                  </span>
                ) : (
                  <span className="t">{n.title}</span>
                )}
              </div>
            </div>
            {kids.length && isOpen ? (
              <div className="tree-branch">
                <CoverTreeView
                  nodes={kids}
                  selected={selected}
                  statuses={statuses}
                  product={product}
                  depth={depth + 1}
                  defaultOpen={defaultOpen}
                />
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function CoverPicker({
  nodes,
  selected,
  onToggle,
  onVer,
}: {
  nodes: TreeNode[];
  selected: Map<string, string>;
  onToggle: (id: string, versions: string[], on: boolean) => void;
  onVer: (id: string, ver: string) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  return (
    <div className="usp-covers-picker">
      {nodes.map((n) => {
        const kids = n.children || [];
        const isGroup = n.kind === "group";
        const canPin = n.coverable ?? !isGroup;
        const isOpen = open[n.id] ?? nodeHasCover(n, selected);
        const on = canPin && selected.has(n.id);
        const vers = n.versions?.length ? n.versions : ["v1.0"];
        const cur = selected.get(n.id) || vers[vers.length - 1];
        return (
          <div key={n.id} className={`item${isOpen && kids.length ? " is-open" : ""}`}>
            <div className={`row${isGroup ? " is-group" : ""}`}>
              {kids.length ? (
                <button type="button" className="twist" onClick={() => setOpen((s) => ({ ...s, [n.id]: !isOpen }))}>
                  {isOpen ? "▼" : "▶"}
                </button>
              ) : (
                <button type="button" className="twist" disabled>
                  ·
                </button>
              )}
              {canPin ? (
                <>
                  <input type="checkbox" checked={on} onChange={(e) => onToggle(n.id, vers, e.target.checked)} />
                  <span className="t">{n.title}</span>
                  <select value={cur} disabled={!on} onChange={(e) => onVer(n.id, e.target.value)} aria-label="功能版本">
                    {vers.map((v) => (
                      <option key={v} value={v}>
                        {v}
                      </option>
                    ))}
                  </select>
                </>
              ) : (
                <span className="t">{n.title}</span>
              )}
            </div>
            {kids.length && isOpen ? <CoverPicker nodes={kids} selected={selected} onToggle={onToggle} onVer={onVer} /> : null}
          </div>
        );
      })}
    </div>
  );
}

export function ProductReleasePage() {
  const { product = "usp", id = "" } = useParams();
  const qc = useQueryClient();
  const [tab, setTab] = useState<"all" | "active">("all");
  const [editing, setEditing] = useState(false);
  const [coverMap, setCoverMap] = useState<Map<string, string>>(new Map());
  const [lcBand, setLcBand] = useState("joint_test");
  const [lcProg, setLcProg] = useState(0);
  const [lcEdit, setLcEdit] = useState(false);
  const [pubAsk, setPubAsk] = useState(false);
  const q = useQuery({
    queryKey: ["module", product, id],
    queryFn: () => api<Page>(`/api/v1/products/${product}/modules/${id}`),
  });
  const p = q.data;
  useEffect(() => {
    if (!p) return;
    setCoverMap(new Map((p.cover_items || []).map((c) => [c.module_id, c.version])));
    setEditing(false);
    setLcEdit(false);
    setLcBand(p.lifecycle?.band || "joint_test");
    setLcProg(Number(p.lifecycle?.progress || 0));
  }, [p]);
  const saveCovers = useMutation({
    mutationFn: () =>
      api(`/api/v1/products/${product}/releases/${p!.release_id}/covers`, {
        method: "PUT",
        body: JSON.stringify({
          covers: [...coverMap.entries()].map(([module_id, version]) => ({ module_id, version })),
        }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["releases", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
      setEditing(false);
    },
  });
  const publish = useMutation({
    mutationFn: () => api(`/api/v1/products/${product}/releases/${p!.release_id}/publish`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["releases", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  const saveLc = useMutation({
    mutationFn: () =>
      api(`/api/v1/products/${product}/modules/${id}/lifecycle`, {
        method: "PUT",
        body: JSON.stringify({ band: lcBand, progress: lcBand === "done" ? 100 : lcProg }),
      }),
    onSuccess: () => {
      setLcEdit(false);
      qc.invalidateQueries({ queryKey: ["module", product, id] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">无权访问或不存在</p>;
  if (!p) return null;
  const items = p.cover_items || [];
  const changelogIds = new Set((p.changelog || []).map((r) => r.module_id));
  const visible = tab === "active"
    ? items.filter((c) => {
        if (changelogIds.size && !changelogIds.has(c.module_id)) return false;
        return (c.week_invested || 0) > 0;
      })
    : items;
  const viewSelected = new Map(visible.map((c) => [c.module_id, c.version]));
  const statuses: Record<string, string> = {};
  for (const c of visible) {
    const b = coverBadge(c, tab);
    if (b) statuses[c.module_id] = b;
  }
  const hrs = p.hours;
  const life = p.lifecycle;
  const lifeLabel = life?.label || (life ? bandLabel(life.band, life.progress) : "");
  function onLc(e: FormEvent) {
    e.preventDefault();
    saveLc.mutate();
  }
  function cancelLc() {
    setLcBand(life?.band || "joint_test");
    setLcProg(Number(life?.progress || 0));
    setLcEdit(false);
  }
  const lcDirty =
    lcBand !== (life?.band || "joint_test") ||
    (lcBand !== "done" && lcProg !== Number(life?.progress || 0));
  return (
    <>
      <div className="page-head">
        <h1>{p.title}</h1>
        <span className="row">
          {p.semver ? <span className="chip">{p.semver}</span> : null}
          {!lcEdit && lifeLabel ? (
            <LcChip
              band={life?.band}
              progress={life?.progress}
              label={lifeLabel}
              onClick={
                p.can_edit_lifecycle && life
                  ? () => {
                      setLcBand(life.band || "joint_test");
                      setLcProg(Number(life.progress || 0));
                      setLcEdit(true);
                    }
                  : undefined
              }
            />
          ) : null}
          {p.release_id ? (
            <span className={`usp-covers-publish-badge ${p.published ? "is-yes" : "is-no"}`}>
              {p.published ? "已发布" : "未发布"}
            </span>
          ) : null}
        </span>
      </div>
      {p.can_edit_lifecycle && life && lcEdit ? (
        <form className="lc-bar" onSubmit={onLc}>
          <div className="lc-bands" role="group" aria-label="状态">
            <button
              type="button"
              className={lcBand === "joint_test" ? "is-on" : ""}
              disabled={life.band === "done"}
              onClick={() => setLcBand("joint_test")}
            >
              联合测试中
            </button>
            <button
              type="button"
              className={lcBand === "done" ? "is-on" : ""}
              data-band="done"
              onClick={() => {
                setLcBand("done");
                setLcProg(100);
              }}
            >
              已完成
            </button>
          </div>
          {lcBand !== "done" ? <LcProgress value={lcProg} onChange={setLcProg} /> : null}
          <button type="submit" className="icon-act is-ok" aria-label="保存" disabled={saveLc.isPending || !lcDirty}>
            <CheckIcon />
          </button>
          <button type="button" className="icon-act is-off" aria-label="取消" onClick={cancelLc} disabled={saveLc.isPending}>
            <CloseIcon />
          </button>
          {saveLc.isError ? (
            <span className="err">{saveLc.error instanceof ApiError ? saveLc.error.detail : "失败"}</span>
          ) : null}
        </form>
      ) : null}
      {p.tag_unassigned || !p.release_id ? (
        <div className="panel">
          <p className="empty">未分配版本</p>
        </div>
      ) : (
        <div className="detail-main">
          <div>
            <section className="panel usp-covers">
              <h2>
                涵盖功能
                <span className={`usp-covers-publish-badge ${p.published ? "is-yes" : "is-no"}`}>
                  {p.published ? "已发布" : "未发布"}
                </span>
              </h2>
              {p.is_dev && !editing ? (
                <div className="usp-covers-tabs" role="tablist">
                  <button type="button" className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>
                    全部
                  </button>
                  <button type="button" className={tab === "active" ? "active" : ""} onClick={() => setTab("active")}>
                    本周有登记
                  </button>
                </div>
              ) : null}
              {editing ? (
                <>
                  <CoverPicker
                    nodes={p.cover_tree || []}
                    selected={coverMap}
                    onToggle={(mid, vers, on) => {
                      setCoverMap((prev) => {
                        const next = new Map(prev);
                        if (on) next.set(mid, next.get(mid) || vers[vers.length - 1] || "v1.0");
                        else next.delete(mid);
                        return next;
                      });
                    }}
                    onVer={(mid, ver) => {
                      setCoverMap((prev) => {
                        const next = new Map(prev);
                        next.set(mid, ver);
                        return next;
                      });
                    }}
                  />
                  <p className="row" style={{ marginTop: 12 }}>
                    <button type="button" className="btn primary" onClick={() => saveCovers.mutate()} disabled={saveCovers.isPending}>
                      保存
                    </button>
                    <button type="button" className="btn" onClick={() => setEditing(false)}>
                      取消
                    </button>
                    {saveCovers.isError ? (
                      <span className="err">{saveCovers.error instanceof ApiError ? saveCovers.error.detail : "失败"}</span>
                    ) : null}
                  </p>
                </>
              ) : (
                <>
                  {!items.length ? (
                    <p className="empty">尚未关联功能</p>
                  ) : !visible.length ? (
                    <p className="empty">
                      {(p.changelog || []).length ? "本周尚无新增内容的工时登记" : "相对上一版无新增内容"}
                    </p>
                  ) : (p.cover_tree || []).length ? (
                    <CoverTreeView
                      key={tab}
                      nodes={p.cover_tree || []}
                      selected={viewSelected}
                      statuses={statuses}
                      product={product}
                      defaultOpen={tab === "active"}
                    />
                  ) : (
                    <ul className="usp-covers-list">
                      {visible.map((c) => (
                        <li key={c.module_id}>
                          <span className="covers-hot">
                            <Link to={`/${product}/m/${c.module_id}`}>{c.title}</Link>
                            <span className="ver">{c.version}</span>
                            {statuses[c.module_id] ? <span className="status">{statuses[c.module_id]}</span> : null}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="row" style={{ marginTop: 12 }}>
                    {p.can_edit_covers ? (
                      <button type="button" className="btn" onClick={() => setEditing(true)}>
                        编辑涵盖
                      </button>
                    ) : null}
                    {p.can_publish ? (
                      <button
                        type="button"
                        className="btn primary"
                        onClick={() => setPubAsk(true)}
                        disabled={publish.isPending}
                      >
                        发布
                      </button>
                    ) : null}
                    {publish.isError ? (
                      <span className="err">{publish.error instanceof ApiError ? publish.error.detail : "失败"}</span>
                    ) : null}
                  </p>
                </>
              )}
            </section>
            <section className="panel" style={{ marginTop: 16 }}>
              <div className="field">
                <p>新增内容</p>
                {(p.changelog || []).length ? (
                  <div className="changelog-wrap">
                    <table className="topo-changelog-auto">
                      <thead>
                        <tr>
                          <th>功能</th>
                          <th>相对上一版</th>
                          <th>说明</th>
                        </tr>
                      </thead>
                      <tbody>
                        {p.changelog.map((r) => (
                          <tr key={`${r.module_id}:${r.version}`}>
                            <td>
                              <Link to={`/${product}/m/${r.module_id}`}>{r.title}</Link>
                            </td>
                            <td>{r.label}</td>
                            <td>{r.note || ""}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <ProductNote text={p.added} />
              </div>
              <div className="field">
                <p>修复内容</p>
                <ProductNote text={p.fixed} />
              </div>
              <div className="field">
                <p>备注</p>
                <ProductNote text={p.remark} />
              </div>
            </section>
          </div>
          {hrs && hoursPanelHasContent(hrs) ? (
            <aside className="panel hours-side">
              <h2 style={{ fontSize: 15, margin: "0 0 10px" }}>工时</h2>
              {hrs.hours_view === "total_only" ? (
                <div className="hours-stats is-total-only">
                  <div className="hours-stat">
                    <span className="v">{fmtHours(hoursTotal(hrs))}</span>
                    <span className="k">累计工时</span>
                  </div>
                </div>
              ) : (
                <>
                  <div className="hours-stats">
                    <div className="hours-stat">
                      <span className="v">{fmtHours(hoursTotal(hrs))}</span>
                      <span className="k">累计（有登记涵盖）</span>
                    </div>
                    <div className="hours-stat">
                      <span className={`v${hrs.remaining_hours == null ? " is-empty" : ""}`}>
                        {hrs.remaining_hours == null || Number(hrs.remaining_hours) === 0
                          ? "–"
                          : fmtHours(Number(hrs.remaining_hours))}
                      </span>
                      <span className="k">预期剩余（有登记涵盖）</span>
                    </div>
                    <div className="hours-stat">
                      <span className={`v${!hrs.week_invested ? " is-empty" : ""}`}>
                        {!hrs.week_invested ? "–" : fmtHours(Number(hrs.week_invested))}
                      </span>
                      <span className="k">本周（有登记涵盖）</span>
                    </div>
                  </div>
                  <div className="hours-sources">
                    {(hrs.by_user || []).length ? (
                      <>
                        <p className="hours-src-title">参与者本周工时</p>
                        <ul className="hbar-list">
                          {(hrs.by_user || []).map((u) => (
                            <li key={u.user} className="hbar-row">
                              <span className="hbar-name" title={personName(u)}>
                                {personName(u)}
                              </span>
                              <HoursDualBar hours={u.hours} expected={u.expected_hours ?? null} />
                              <span className="hbar-val">{fmtHours(u.hours)}</span>
                            </li>
                          ))}
                        </ul>
                      </>
                    ) : (
                      <p className="empty">本周尚无登记</p>
                    )}
                  </div>
                  {hrs.week ? <span className="hours-week">{hrs.week}</span> : null}
                </>
              )}
            </aside>
          ) : null}
        </div>
      )}
      {pubAsk ? (
        <Confirm
          title="发布"
          body="发版后将锁定涵盖功能关联，版本进入联合测试中。"
          confirm="发布"
          pending={publish.isPending}
          error={publish.isError ? (publish.error instanceof ApiError ? publish.error.detail : "失败") : undefined}
          onCancel={() => setPubAsk(false)}
          onConfirm={() => publish.mutate(undefined, { onSuccess: () => setPubAsk(false) })}
        />
      ) : null}
    </>
  );
}
