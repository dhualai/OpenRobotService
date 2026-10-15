import { FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api, type Me } from "../api";
import { ReqListRow, ReqListTools, ReqPager, useReqView, type ReqListItem } from "../req";

type List = { items: ReqListItem[] };

export function RequirementsPage() {
  const { product = "usp" } = useParams();
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const list = useQuery({
    queryKey: ["reqs", product],
    queryFn: () => api<List>(`/api/v1/products/${product}/requirements`),
  });
  const reqView = useReqView(list.data?.items || []);
  const create = useMutation({
    mutationFn: (title: string) =>
      api(`/api/v1/products/${product}/requirements`, {
        method: "POST",
        body: JSON.stringify({ title }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["reqs", product] }),
  });
  if (list.isLoading) return <p>…</p>;
  if (list.isError) return <p className="err">无法加载需求</p>;
  const canCreate = Boolean(me.data?.can_edit_requirements);
  function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    create.mutate(String(fd.get("title") || ""));
    e.currentTarget.reset();
  }
  return (
    <div className="panel">
      <div className="page-head">
        <h1>需求</h1>
        <ReqListTools
          needle={reqView.needle}
          sort={reqView.sort}
          dir={reqView.dir}
          onNeedle={reqView.setNeedle}
          onSort={reqView.setSort}
          onDir={reqView.setDir}
        />
      </div>
      {canCreate ? (
        <form onSubmit={onSubmit} className="row" style={{ marginBottom: 12 }}>
          <input name="title" placeholder="标题" required style={{ flex: 1, minWidth: 180 }} />
          <button type="submit" className="btn primary">
            新建
          </button>
        </form>
      ) : null}
      <ul className="req-list">
        {reqView.rows.map((r) => (
          <ReqListRow key={r.id} product={product} item={r} />
        ))}
      </ul>
      <ReqPager page={reqView.page} pages={reqView.pages} total={reqView.total} onPage={reqView.setPage} />
    </div>
  );
}
