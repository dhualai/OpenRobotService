import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { fmtWhen } from "./req";

type Views = {
  total_views: number;
  items: { username: string; name: string; count: number; last_seen_at: string | null }[];
};

export function DocViews({ path, enabled }: { path: string; enabled: boolean }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!path) return;
    api(path, { method: "POST" }).catch(() => {});
  }, [path]);
  const q = useQuery({
    queryKey: ["doc-views", path],
    queryFn: () => api<Views>(path),
    enabled: enabled && open,
    staleTime: 15_000,
  });
  if (!enabled) return null;
  const items = [...(q.data?.items || [])].sort(
    (a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh")
  );
  const total = q.data?.total_views ?? 0;
  return (
    <div className={`doc-views${open ? " is-open" : ""}`}>
      <button type="button" className="doc-views-btn" onClick={() => setOpen((v) => !v)}>
        浏览记录
      </button>
      {open ? (
        <div className="doc-views-panel">
          {q.isLoading ? (
            <p className="doc-views-empty">…</p>
          ) : q.isError ? (
            <p className="doc-views-empty">无法读取</p>
          ) : !items.length ? (
            <p className="doc-views-empty">暂无</p>
          ) : (
            <>
              <p className="doc-views-sum">
                {items.length} 人 · {total} 次
              </p>
              <ul className="hbar-list doc-views-list">
                {items.map((it) => {
                  const pct = Math.max(0, Math.min(100, it.count));
                  const when = it.last_seen_at ? fmtWhen(it.last_seen_at) : "";
                  return (
                    <li key={it.username} className="hbar-row" title={when || undefined}>
                      <span className="hbar-name" title={it.name}>
                        {it.name}
                      </span>
                      {pct > 0 ? (
                        <span className="doc-views-fill" style={{ width: `${(pct / 100) * 78}em` }} aria-hidden="true" />
                      ) : null}
                      <span className="hbar-val">{it.count}</span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
