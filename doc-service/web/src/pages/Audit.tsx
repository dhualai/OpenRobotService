import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Me } from "../api";
import { fmtWhen, pageReqs, ReqPager } from "../req";

const PAGE = 20;

type Item = {
  id: number;
  at: string | null;
  actor: string;
  action: string;
  action_label: string;
  title: string;
  summary: string;
  href?: string | null;
  product?: string | null;
};

type Audit = { items: Item[]; days?: number };

export function AuditPage() {
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const allowed = Boolean(me.data?.can_manage_users);
  const q = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<Audit>("/api/v1/admin/audit"),
    enabled: allowed,
  });
  const [page, setPage] = useState(1);
  const listRef = useRef<HTMLDivElement>(null);
  const items = q.data?.items || [];
  const view = pageReqs(items, page, PAGE);
  useEffect(() => {
    setPage((p) => Math.min(p, view.pages));
  }, [view.pages]);
  useEffect(() => {
    listRef.current?.scrollTo(0, 0);
  }, [view.page]);
  if (me.isLoading) return <p>…</p>;
  if (!allowed) return <p className="err">无权查看</p>;
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">无权查看</p>;
  return (
    <div className="panel">
      <div className="page-head">
        <h1>更新记录</h1>
        <ReqPager page={view.page} pages={view.pages} total={view.total} onPage={setPage} size={PAGE} />
      </div>
      {items.length ? (
        <div className="upd-list-wrap" ref={listRef}>
          <ul className="upd-list">
            {view.rows.map((r) => (
              <li key={r.id}>
                <div className="upd-main">
                  {r.href ? (
                    <Link to={r.href}>{r.title || r.action_label}</Link>
                  ) : (
                    <span className="upd-title">{r.title || r.action_label}</span>
                  )}
                  <span className="meta">
                    <time dateTime={r.at || undefined}>{fmtWhen(r.at)}</time>
                    <span>{r.actor}</span>
                    <span className="upd-tag">{r.action_label}</span>
                    {r.summary && r.summary !== r.action_label ? <span>{r.summary}</span> : null}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="empty">无更新</p>
      )}
    </div>
  );
}
