import { Link } from "react-router-dom";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api";
import { CheckIcon, CloseIcon, PencilIcon } from "./icons";

export const EXPECTED_HOURS_MAX = 64;

export type HoursUser = { user: string; name?: string; hours: number; expected_hours?: number | null };

export function personName(u: { user: string; name?: string } | string, name?: string): string {
  if (typeof u === "string") return (name || "").trim() || u;
  return (u.name || "").trim() || u.user;
}

export type HoursChild = {
  id: string;
  title: string;
  hours: number;
  expected_hours?: number | null;
  invested?: number;
  band?: string | null;
};

export type ModuleHours = {
  invested: number;
  invested_total?: number;
  expected_hours: number | null;
  remaining_hours: number | null;
  settled: boolean;
  band: string | null;
  my_week_hours?: number;
  week_invested?: number;
  by_user?: HoursUser[];
  by_child?: HoursChild[];
  can_register?: boolean;
  hours_mode?: string;
  week?: string;
};

export function hoursChildListed(c: HoursChild): boolean {
  return Number(c.hours || 0) > 0;
}

export function moduleHoursPanelOpen(hrs: ModuleHours | null | undefined): boolean {
  if (!hrs) return false;
  const settled = Boolean(hrs.settled) || (hrs.band || "").toLowerCase() === "done";
  const own =
    Number(hrs.invested) > 0 ||
    Number(hrs.week_invested) > 0 ||
    hrs.expected_hours != null ||
    (hrs.by_user || []).length > 0 ||
    Boolean(hrs.can_register);
  const kids = (hrs.by_child || []).some(hoursChildListed);
  return !settled || own || kids;
}

function snap(n: number): number {
  return Math.round(Math.max(0, n) * 2) / 2;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

export function fmtHours(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(Number(n))) return "";
  const x = Math.round(Number(n) * 10) / 10;
  return Number.isInteger(x) ? String(x) : x.toFixed(1);
}

function dashHours(n: number | null | undefined): string {
  const x = Number(n);
  if (n == null || !Number.isFinite(x) || x === 0) return "–";
  return fmtHours(x);
}

type Thumb = "hours" | "expected";
type BarChange = { hours: boolean; expected: boolean };

export function HoursReadBar({ hours, max }: { hours: number; max: number }) {
  const m = Math.max(Number(max) || 0, 1e-6);
  const pct = Math.min(100, (Math.max(0, Number(hours) || 0) / m) * 100);
  return (
    <div className="hbar-track" aria-hidden="true">
      <span className="hbar-rail" />
      {pct > 0 ? <span className="hbar-fill ok" style={{ width: `${pct}%` }} /> : null}
    </div>
  );
}

export function HoursDualBar({
  hours,
  expected,
  editable,
  onChange,
}: {
  hours: number;
  expected: number | null;
  editable?: boolean;
  onChange?: (next: { hours: number; expected: number }, changed: BarChange) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<{ x: number; y: number; kind: Thumb; moved: boolean } | null>(null);
  const liveRef = useRef({ hours: 0, expected: 8 });
  const changedRef = useRef<BarChange>({ hours: false, expected: false });
  const editInpRef = useRef<HTMLInputElement>(null);
  const [live, setLive] = useState<{ hours: number; expected: number } | null>(null);
  const [dragKind, setDragKind] = useState<Thumb | null>(null);
    const [hoverExp, setHoverExp] = useState(false);
    const [hoverHours, setHoverHours] = useState(false);
    const [hoverBar, setHoverBar] = useState(false);
  const [editKind, setEditKind] = useState<Thumb | null>(null);
  const [editText, setEditText] = useState("");
  const exp0 = expected == null ? (editable ? 8 : 0) : clamp(expected, 0, EXPECTED_HOURS_MAX);
  const h0 = clamp(hours, 0, EXPECTED_HOURS_MAX);
  const h = live ? live.hours : h0;
  const exp = live ? live.expected : exp0;
  const hasExp = expected != null || editable || live != null;

  function posToHours(clientX: number): number {
    const el = trackRef.current;
    if (!el) return 0;
    const r = el.getBoundingClientRect();
    const t = r.width <= 0 ? 0 : (clientX - r.left) / r.width;
    return snap(clamp(t * EXPECTED_HOURS_MAX, 0, EXPECTED_HOURS_MAX));
  }

  function apply(thumb: Thumb, raw: number) {
    const curH = liveRef.current.hours;
    const curE = liveRef.current.expected;
    let nextH = curH;
    let nextE = curE;
    if (thumb === "hours") {
      nextH = clamp(raw, 0, curE);
      changedRef.current.hours = true;
    } else {
      nextE = clamp(raw, 0, EXPECTED_HOURS_MAX);
      nextH = Math.min(curH, nextE);
      changedRef.current.expected = true;
      if (nextH < curH - 1e-6) changedRef.current.hours = true;
    }
    liveRef.current = { hours: nextH, expected: nextE };
    setLive({ hours: nextH, expected: nextE });
    onChange?.({ hours: nextH, expected: nextE }, { ...changedRef.current });
  }

  function pickThumb(clientX: number): Thumb {
    const v = posToHours(clientX);
    if (!hasExp) return "hours";
    return Math.abs(v - h) <= Math.abs(v - exp) ? "hours" : "expected";
  }

  function onDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (!editable) return;
    if ((e.target as HTMLElement).closest(".hbar-tip-inp")) return;
    e.preventDefault();
    liveRef.current = { hours: h, expected: exp };
    changedRef.current = { hours: false, expected: false };
    const hit = (e.target as HTMLElement).closest("[data-thumb]")?.getAttribute("data-thumb") as Thumb | null;
    const kind = hit === "hours" || hit === "expected" ? hit : pickThumb(e.clientX);
    const waitClick = (kind === "hours" && hit === "hours") || (kind === "expected" && hit === "expected");
    startRef.current = { x: e.clientX, y: e.clientY, kind, moved: !waitClick };
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    if (!waitClick) {
      setDragKind(kind);
      setEditKind(null);
      apply(kind, posToHours(e.clientX));
    }
  }

  function onMove(e: ReactPointerEvent<HTMLDivElement>) {
    const start = startRef.current;
    if (!start) return;
    if (!start.moved) {
      const dx = e.clientX - start.x;
      const dy = e.clientY - start.y;
      if (dx * dx + dy * dy < 25) return;
      start.moved = true;
      setDragKind(start.kind);
      setEditKind(null);
    }
    apply(start.kind, posToHours(e.clientX));
  }

  function onUp(e: ReactPointerEvent<HTMLDivElement>) {
    const start = startRef.current;
    if (!start) return;
    startRef.current = null;
    setDragKind(null);
    setLive(null);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    if (!start.moved) {
      setEditText(fmtHours(start.kind === "hours" ? h : exp));
      setEditKind(start.kind);
    }
  }

  useEffect(() => {
    if (!editKind) return;
    editInpRef.current?.focus();
    editInpRef.current?.select();
  }, [editKind]);

  useEffect(() => {
    if (dragKind) return;
    setLive(null);
  }, [hours, expected, dragKind]);

  function onTipTyped(kind: Thumb, raw: string) {
    setEditText(raw);
    if (raw.trim() === "") return;
    const v = Number(raw);
    if (!Number.isFinite(v)) return;
    liveRef.current = { hours: h, expected: exp };
    apply(kind, snap(clamp(v, 0, EXPECTED_HOURS_MAX)));
  }

  function renderTip(kind: Thumb, value: number, show: boolean, label: string) {
    if (editKind === kind) {
      return (
        <input
          ref={editInpRef}
          className="hbar-tip-inp"
          type="text"
          inputMode="decimal"
          value={editText}
          onPointerDown={(ev) => ev.stopPropagation()}
          onChange={(ev) => onTipTyped(kind, ev.target.value)}
          onBlur={() => setEditKind(null)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" || ev.key === "Escape") (ev.target as HTMLInputElement).blur();
          }}
          aria-label={label}
        />
      );
    }
    if (show) return <span className="hbar-tip">{fmtHours(value)}</span>;
    return null;
  }

  const fillPct = (h / EXPECTED_HOURS_MAX) * 100;
  const expPct = (exp / EXPECTED_HOURS_MAX) * 100;
  const over = hasExp && h > exp + 1e-6;
  const showHoursTip = Boolean(editable && (dragKind === "hours" || hoverHours || editKind === "hours"));
  const showExpTip = hasExp && (dragKind === "expected" || hoverExp || hoverBar || editKind === "expected");

  return (
    <div
      className={`hbar-track${editable ? " is-edit" : ""}`}
      ref={trackRef}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onPointerEnter={() => setHoverBar(true)}
      onPointerLeave={() => setHoverBar(false)}
    >
      <span className="hbar-rail" />
      {hasExp ? <span className="hbar-span" style={{ width: `${expPct}%` }} /> : null}
      {fillPct > 0 ? <span className={`hbar-fill${over ? " over" : " ok"}`} style={{ width: `${Math.min(fillPct, 100)}%` }} /> : null}
      {editable ? (
        <span
          className="hbar-hit is-hours is-edit"
          data-thumb="hours"
          style={{ left: `${fillPct}%` }}
          onPointerEnter={() => setHoverHours(true)}
          onPointerLeave={() => setHoverHours(false)}
        >
          <span className="hbar-thumb is-hours" />
          {renderTip("hours", h, showHoursTip, "本周工时")}
        </span>
      ) : null}
      {hasExp ? (
        <span
          className={`hbar-hit is-expected${editable ? " is-edit" : ""}`}
          data-thumb={editable ? "expected" : undefined}
          style={{ left: `${expPct}%` }}
          onPointerEnter={() => setHoverExp(true)}
          onPointerLeave={() => setHoverExp(false)}
        >
          {editable ? <span className="hbar-thumb is-expected" /> : null}
          {renderTip("expected", exp, showExpTip, "预期工时")}
        </span>
      ) : null}
    </div>
  );
}

export function ModuleHoursPanel({
  product,
  moduleId,
  version,
  hours: hrs,
  username,
  displayName,
  totalOnly,
}: {
  product: string;
  moduleId: string;
  version: string;
  hours: ModuleHours;
  username?: string;
  displayName?: string;
  totalOnly?: boolean;
}) {
  const qc = useQueryClient();
  const [mineOpen, setMineOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<{ hours: number; expected: number } | null>(null);
  const [touched, setTouched] = useState<BarChange>({ hours: false, expected: false });
  const [omitMine, setOmitMine] = useState(false);
  const rows = hrs.by_user || [];
  const kids = (hrs.by_child || []).filter(hoursChildListed);
  const hasOwn =
    Number(hrs.invested) > 0 ||
    Number(hrs.week_invested) > 0 ||
    hrs.expected_hours != null ||
    rows.length > 0 ||
    Boolean(hrs.can_register);
  const showOwn = hasOwn;
  const canEdit = Boolean(showOwn && hrs.can_register && username);
  const mine = canEdit ? rows.find((r) => r.user === username) : undefined;
  const mineActive = Boolean(
    mine && !omitMine && (mine.hours > 0 || (mine.expected_hours ?? 0) > 0)
  );
  const showMine = Boolean(canEdit && (mineOpen || editing || mineActive));
  const canAdd = Boolean(canEdit && !showMine);
  const others = (canEdit && username ? rows.filter((r) => r.user !== username) : rows).filter(
    (u) => u.hours > 0 || (u.expected_hours ?? 0) > 0
  );
  const weekTotal = Number(hrs.week_invested ?? 0);
  const myHours = mine ? mine.hours : 0;
  const myExpected = mine?.expected_hours ?? null;
  const barHours = draft?.hours ?? myHours;
  const barExpected = draft ? draft.expected : myExpected;
  const dirty = touched.hours || touched.expected;

  useEffect(() => {
    setMineOpen(false);
    setEditing(false);
    setDraft(null);
    setTouched({ hours: false, expected: false });
    setOmitMine(false);
  }, [moduleId, version, hrs.week]);

  const save = useMutation({
    mutationFn: (body: { hours?: number; expected_hours?: number }) =>
      api(`/api/v1/products/${product}/hours`, {
        method: "PUT",
        body: JSON.stringify({ feature_id: moduleId, version, ...body }),
      }),
    onSuccess: () => {
      setDraft(null);
      setTouched({ hours: false, expected: false });
      setEditing(false);
      setMineOpen(false);
      setOmitMine(false);
      qc.invalidateQueries({ queryKey: ["module", product, moduleId] });
      qc.invalidateQueries({ queryKey: ["hours-snake", product] });
    },
  });

  function onBarChange(next: { hours: number; expected: number }, changed: BarChange) {
    setDraft(next);
    setTouched((t) => ({ hours: t.hours || changed.hours, expected: t.expected || changed.expected }));
  }

  function commitSave() {
    if (!draft || !dirty) {
      setEditing(false);
      setDraft(null);
      setTouched({ hours: false, expected: false });
      return;
    }
    save.mutate({ hours: draft.hours, expected_hours: draft.expected });
  }

  function cancelEdit() {
    const wasNew = !mine || (mine.hours <= 0 && (mine.expected_hours ?? 0) <= 0);
    setEditing(false);
    setDraft(null);
    setTouched({ hours: false, expected: false });
    if (wasNew) {
      setMineOpen(false);
      setOmitMine(true);
    }
  }

  const ownTotal = Number(hrs.invested || 0) + Number(hrs.week_invested || 0);
  const kidsTotal = kids.reduce((n, c) => n + Number(c.hours || 0), 0);
  const kidsBlock = kids.length ? (
    <div className={`hours-kids${showOwn ? "" : " is-only"}`}>
      <div className="hours-kids-head">
        <h2 className="hours-title">子模块工时</h2>
        <span className="hours-kids-sum">{fmtHours(kidsTotal) || "–"}</span>
      </div>
      <ul className="hbar-list">
        {kids.map((c) => (
          <li key={c.id} className="hbar-row">
            <Link className="hbar-name" to={`/${product}/m/${c.id}`} title={c.title}>
              {c.title}
            </Link>
            <HoursDualBar hours={c.hours} expected={c.expected_hours ?? null} />
            <span className={`hbar-val${c.expected_hours != null && c.hours > c.expected_hours ? " is-over" : ""}`}>
              {fmtHours(c.hours)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  ) : null;

  if (totalOnly) {
    return (
      <aside className="panel hours-side">
        {showOwn ? (
          <>
            <h2 className="hours-title">工时</h2>
            <div className="hours-stats is-total-only">
              <div className="hours-stat">
                <span className="v">{fmtHours(ownTotal) || "–"}</span>
                <span className="k">累计工时</span>
              </div>
            </div>
          </>
        ) : null}
        {kidsBlock}
      </aside>
    );
  }

  return (
    <aside className="panel hours-side">
      {showOwn ? (
        <>
          <h2 className="hours-title">工时</h2>
          <div className="hours-stats">
            <div className="hours-stat">
              <span className="v">{dashHours(hrs.invested)}</span>
              <span className="k">累计工时</span>
            </div>
            <div className="hours-stat">
              <span className={`v${hrs.expected_hours == null ? " is-empty" : ""}`}>
                {hrs.expected_hours == null ? "–" : fmtHours(hrs.expected_hours)}
              </span>
              <span className="k">预期工时</span>
            </div>
            <div className="hours-stat">
              <span className={`v${!weekTotal ? " is-empty" : ""}`}>{dashHours(weekTotal)}</span>
              <span className="k">本周工时</span>
            </div>
          </div>
          <div className="hours-sources">
            <div className="hours-src-head">
              <p className="hours-src-title">参与者本周工时</p>
              {canAdd ? (
                <button
                  type="button"
                  className="hours-add-btn"
                  onClick={() => {
                    setMineOpen(true);
                    setEditing(true);
                    setOmitMine(false);
                    setDraft({ hours: 0, expected: 8 });
                    setTouched({ hours: false, expected: true });
                  }}
                  aria-label="登记"
                >
                  +
                </button>
              ) : null}
            </div>
            {others.length || showMine ? (
              <ul className="hbar-list">
                {others.map((u) => (
                  <li key={u.user} className="hbar-row">
                    <span className="hbar-name" title={personName(u)}>
                      {personName(u)}
                    </span>
                    <HoursDualBar hours={u.hours} expected={u.expected_hours ?? null} />
                    <span className={`hbar-val${u.expected_hours != null && u.hours > u.expected_hours ? " is-over" : ""}`}>{fmtHours(u.hours)}</span>
                  </li>
                ))}
                {showMine && username ? (
                  <li className="hbar-row is-mine">
                    <span className="hbar-name" title={personName(username, displayName)}>
                      {personName(username, displayName)}
                    </span>
                    <HoursDualBar hours={barHours} expected={barExpected} editable={editing} onChange={onBarChange} />
                    <span className={`hbar-val${barExpected != null && barHours > barExpected ? " is-over" : ""}`}>{fmtHours(barHours)}</span>
                    <span className="hbar-acts">
                      {editing ? (
                        <>
                          <button
                            type="button"
                            className="hours-act-btn is-save"
                            disabled={save.isPending}
                            onClick={commitSave}
                            aria-label="保存"
                          >
                            <CheckIcon />
                          </button>
                          <button
                            type="button"
                            className="hours-act-btn"
                            disabled={save.isPending}
                            onClick={cancelEdit}
                            aria-label="取消"
                          >
                            <CloseIcon />
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          className="hours-act-btn"
                          disabled={save.isPending}
                          onClick={() => {
                            setEditing(true);
                            setDraft({ hours: barHours, expected: barExpected ?? 8 });
                          }}
                          aria-label="编辑"
                        >
                          <PencilIcon />
                        </button>
                      )}
                    </span>
                  </li>
                ) : null}
              </ul>
            ) : (
              <p className="empty">本周尚无登记</p>
            )}
          </div>
        </>
      ) : null}
      {kidsBlock}
      {hrs.week ? <span className="hours-week">{hrs.week}</span> : null}
      {save.isError ? <p className="err">{save.error instanceof ApiError ? save.error.detail : "失败"}</p> : null}
    </aside>
  );
}
