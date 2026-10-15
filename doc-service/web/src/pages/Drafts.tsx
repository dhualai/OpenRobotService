import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import { fmtWhen, ReqPager } from "../req";

const PAGE = 20;

type Tag = "public" | "draft";
type Item = { id: string; title: string; owner?: string; updated_at?: string | null };
type List = { items: Item[]; total: number; page: number; pages: number; tag?: string };

export function DraftsPage() {
  const { product = "usp" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [tag, setTag] = useState<Tag>("public");
  const [page, setPage] = useState(1);
  const q = useQuery({
    queryKey: ["drafts", tag, page],
    queryFn: () => api<List>(`/api/v1/drafts?tag=${tag}&page=${page}&size=${PAGE}`),
  });
  const create = useMutation({
    mutationFn: (title: string) =>
      api<{ id: string }>("/api/v1/drafts", { method: "POST", body: JSON.stringify({ title }) }),
    onSuccess: (out) => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      nav(`/${product}/drafts/${out.id}`);
    },
  });
  useEffect(() => {
    if (q.data && q.data.page !== page) setPage(q.data.page);
  }, [q.data, page]);
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">{q.error instanceof ApiError ? q.error.detail : "无法打开"}</p>;
  const items = q.data?.items || [];
  const total = q.data?.total || 0;
  const pages = q.data?.pages || 1;
  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    create.mutate(String(fd.get("title") || ""));
    e.currentTarget.reset();
  }
  function onTag(next: Tag) {
    setTag(next);
    setPage(1);
  }
  return (
    <div className="panel">
      <div className="page-head">
        <h1>讨论区</h1>
        <div className="disc-tags" role="tablist">
          <button type="button" className={tag === "public" ? "is-active" : ""} onClick={() => onTag("public")}>
            公共
          </button>
          <button type="button" className={tag === "draft" ? "is-active" : ""} onClick={() => onTag("draft")}>
            草稿
          </button>
        </div>
        <ReqPager page={page} pages={pages} total={total} onPage={setPage} size={PAGE} />
      </div>
      {tag === "draft" ? (
        <form onSubmit={onSubmit} className="row" style={{ marginBottom: 12 }}>
          <input name="title" placeholder="标题" required style={{ flex: 1, minWidth: 180 }} />
          <button type="submit" className="btn primary" disabled={create.isPending}>
            新建
          </button>
        </form>
      ) : null}
      {create.isError ? (
        <p className="err">{create.error instanceof ApiError ? create.error.detail : "失败"}</p>
      ) : null}
      {items.length ? (
        <ul className="draft-list">
          {items.map((r) => (
            <li key={r.id}>
              <Link to={`/${product}/drafts/${r.id}`}>{r.title}</Link>
              <span className="meta">
                {tag === "public" && r.owner ? <span>{r.owner}</span> : null}
                <time dateTime={r.updated_at || undefined}>{fmtWhen(r.updated_at)}</time>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty">暂无</p>
      )}
    </div>
  );
}
