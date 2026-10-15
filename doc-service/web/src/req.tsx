import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./api";
import { Confirm, PopupMenu } from "./dialog";
import { fmtHours } from "./hours-bar";
import { REQ_STATUS } from "./labels";

export const REQ_PRIORITY = [0, 1, 2, 3, 4] as const;

export type ReqFeature = { id: string; title: string };

export type ReqListItem = {
  id: string;
  title: string;
  status: string;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
  priority: number;
  tag?: string | null;
  can_edit?: boolean;
  can_delete?: boolean;
  can_start_review?: boolean;
  features?: ReqFeature[];
  hours_ratio?: number | null;
  hours_week?: number | null;
};

export function matchReq(item: ReqListItem, q: string): boolean {
  const raw = q.trim().toLowerCase();
  if (!raw) return true;
  const hay = [
    item.title || "",
    item.created_by || "",
    item.id || "",
    REQ_STATUS[item.status] || item.status || "",
    ...(item.features || []).map((f) => f.title || f.id),
  ]
    .join("\n")
    .toLowerCase();
  return raw.split(/\s+/).every((tok) => hay.includes(tok));
}

export const REQ_PAGE = 30;

export type ReqSort = "created" | "updated" | "priority" | "hours" | "status";
export type ReqDir = "asc" | "desc";

export const REQ_SORTS: { id: ReqSort; label: string }[] = [
  { id: "created", label: "创建时间" },
  { id: "updated", label: "修改时间" },
  { id: "priority", label: "优先级" },
  { id: "hours", label: "公式速度" },
  { id: "status", label: "状态" },
];

const STATUS_RANK: Record<string, number> = {
  draft: 0,
  review: 1,
  pending_link: 2,
  developing: 3,
  done: 4,
};

function ts(iso?: string | null): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}

function sortRank(item: ReqListItem, sort: ReqSort): number {
  if (sort === "created") return ts(item.created_at) || ts(item.updated_at);
  if (sort === "updated") return ts(item.updated_at) || ts(item.created_at);
  if (sort === "priority") return -clampPriority(item.priority);
  if (sort === "hours") {
    const n = item.hours_ratio;
    return n == null || !Number.isFinite(Number(n)) ? Number.NEGATIVE_INFINITY : Number(n);
  }
  return STATUS_RANK[item.status] ?? Number.NEGATIVE_INFINITY;
}

export function sortReqs(items: ReqListItem[], sort: ReqSort, dir: ReqDir = "desc"): ReqListItem[] {
  const rows = items.slice();
  const sign = dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    const d = sortRank(a, sort) - sortRank(b, sort);
    if (d) return d * sign;
    return a.id.localeCompare(b.id) * sign;
  });
  return rows;
}

export function pageReqs<T>(items: T[], page: number, size = REQ_PAGE): { rows: T[]; page: number; pages: number; total: number } {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / size) || 1);
  const p = Math.min(Math.max(1, page), pages);
  const start = (p - 1) * size;
  return { rows: items.slice(start, start + size), page: p, pages, total };
}

export function useReqView(items: ReqListItem[]) {
  const [needle, setNeedle] = useState("");
  const [sort, setSort] = useState<ReqSort>("updated");
  const [dir, setDir] = useState<ReqDir>("desc");
  const [page, setPage] = useState(1);
  const filtered = useMemo(
    () => sortReqs(items.filter((it) => matchReq(it, needle)), sort, dir),
    [items, needle, sort, dir],
  );
  const paged = pageReqs(filtered, page);
  useEffect(() => {
    const pages = Math.max(1, Math.ceil(filtered.length / REQ_PAGE) || 1);
    setPage((p) => Math.min(p, pages));
  }, [filtered.length]);
  return {
    needle,
    sort,
    dir,
    setNeedle: (s: string) => {
      setNeedle(s);
      setPage(1);
    },
    setSort: (s: ReqSort) => {
      setSort(s);
      setDir("desc");
      setPage(1);
    },
    setDir: (d: ReqDir) => {
      setDir(d);
      setPage(1);
    },
    setPage,
    filtered,
    ...paged,
  };
}

export function ReqListTools({
  needle,
  sort,
  dir,
  onNeedle,
  onSort,
  onDir,
}: {
  needle: string;
  sort: ReqSort;
  dir: ReqDir;
  onNeedle: (s: string) => void;
  onSort: (s: ReqSort) => void;
  onDir: (d: ReqDir) => void;
}) {
  return (
    <div className="req-bar-end">
        <span className="req-sort-wrap">
          <select className="req-sort" aria-label="排序" value={sort} onChange={(e) => onSort(e.target.value as ReqSort)}>
            {REQ_SORTS.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="req-dir-btn"
            aria-label={dir === "asc" ? "从低到高" : "从高到低"}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => onDir(dir === "asc" ? "desc" : "asc")}
          >
            {dir === "asc" ? (
              <svg viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
                <path fill="currentColor" d="M5 0L0 6h10z" />
              </svg>
            ) : (
              <svg viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
                <path fill="currentColor" d="M0 0h10L5 6z" />
              </svg>
            )}
          </button>
        </span>
      <input
        className="req-search"
        type="search"
        value={needle}
        onChange={(e) => onNeedle(e.target.value)}
        placeholder="搜索"
        aria-label="搜索需求"
        autoComplete="off"
      />
    </div>
  );
}

export function ReqPager({
  page,
  pages,
  total,
  onPage,
  size = REQ_PAGE,
}: {
  page: number;
  pages: number;
  total: number;
  onPage: (n: number) => void;
  size?: number;
}) {
  if (total <= size) return null;
  return (
    <div className="req-pager">
      <span className="req-pager-n">
        {page}/{pages}
      </span>
      <button type="button" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label="上一页">
        ‹
      </button>
      <button type="button" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label="下一页">
        ›
      </button>
    </div>
  );
}

export function fmtWhen(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function clampPriority(n: number | null | undefined): number {
  const v = Number(n);
  if (!Number.isFinite(v)) return 2;
  return Math.min(4, Math.max(0, Math.round(v)));
}

export function PriorityBadge({ value }: { value: number }) {
  const v = clampPriority(value);
  return (
    <span className="prio" data-prio={v}>
      P{v}
    </span>
  );
}

export function PriorityField({
  value,
  canEdit,
  disabled,
  onChange,
}: {
  value: number;
  canEdit: boolean;
  disabled?: boolean;
  onChange?: (n: number) => void;
}) {
  const v = clampPriority(value);
  if (!canEdit || !onChange) return <PriorityBadge value={v} />;
  return (
    <select
      className="prio-select"
      data-prio={v}
      aria-label="优先级"
      value={v}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      {REQ_PRIORITY.map((n) => (
        <option key={n} value={n}>
          P{n}
        </option>
      ))}
    </select>
  );
}

export function ReqFeatTag({ product, feature }: { product: string; feature: ReqFeature }) {
  return (
    <Link className="req-feat" to={`/${product}/m/${encodeURIComponent(feature.id)}`}>
      {feature.title}
    </Link>
  );
}

export function ReqHoursBar({
  ratio,
  week,
}: {
  ratio: number | null | undefined;
  week?: number | null;
}) {
  if (ratio == null || !Number.isFinite(Number(ratio))) return <span className="req-hbar-slot" />;
  const v = Math.max(0, Math.min(1, Number(ratio)));
  const tip = week != null && Number.isFinite(Number(week)) ? fmtHours(Number(week)) : "";
  return (
    <span className="req-hbar-wrap">
      <span className="req-hbar" aria-label={tip ? `本周 ${tip}` : "当前工时"}>
        {[0, 1, 2, 3, 4].map((i) => {
          const fill = Math.max(0, Math.min(1, v * 5 - i));
          return (
            <span key={i} className="req-hbar-cell">
              {fill > 0 ? <span className="req-hbar-fill" style={{ width: `${fill * 100}%` }} /> : null}
            </span>
          );
        })}
      </span>
      {tip ? <span className="req-hbar-tip">{tip}</span> : null}
    </span>
  );
}

export function ReqListRow({ product, item }: { product: string; item: ReqListItem }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [delAsk, setDelAsk] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/products/${product}/requirements/${item.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reqs", product] }),
  });
  const feats = item.features || [];
  const loc = useLocation();
  const tag = new URLSearchParams(loc.search).get("tag");
  const href = `/${product}/requirements/${item.id}${tag === "project" ? "?tag=project" : ""}`;
  return (
    <li
      data-prio={clampPriority(item.priority)}
      className={menu ? "is-menu" : undefined}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("a, button")) return;
        nav(href);
      }}
      onContextMenu={(e) => {
        if (!item.can_delete) return;
        e.preventDefault();
        setMenu({ x: e.clientX, y: e.clientY });
      }}
    >
      <div className="req-main">
        <div className="req-title-row">
          <Link to={href} onClick={(e) => e.stopPropagation()}>
            {item.title}
          </Link>
          {feats.map((f) => (
            <span key={f.id} onClick={(e) => e.stopPropagation()}>
              <ReqFeatTag product={product} feature={f} />
            </span>
          ))}
        </div>
        <span className="meta">
          <span>{item.created_by}</span>
          <time dateTime={item.updated_at || undefined}>{fmtWhen(item.updated_at)}</time>
        </span>
      </div>
      <ReqHoursBar
        ratio={item.status === "developing" ? item.hours_ratio : null}
        week={item.status === "developing" ? item.hours_week : null}
      />
      <PriorityBadge value={item.priority} />
      <span className="st">{REQ_STATUS[item.status] || item.status}</span>
      {menu ? (
        <PopupMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)}>
          <button
            type="button"
            className="ctx-item danger"
            role="menuitem"
            onClick={() => {
              setMenu(null);
              setDelAsk(true);
            }}
          >
            删除
          </button>
        </PopupMenu>
      ) : null}
      {delAsk ? (
        <Confirm
          title="删除"
          body={`「${item.title}」`}
          confirm="删除"
          danger
          pending={remove.isPending}
          error={remove.isError ? (remove.error instanceof ApiError ? remove.error.detail : "失败") : undefined}
          onCancel={() => setDelAsk(false)}
          onConfirm={() => remove.mutate()}
        />
      ) : null}
    </li>
  );
}
