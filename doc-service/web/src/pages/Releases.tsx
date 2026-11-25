import { FormEvent, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import { Confirm } from "../dialog";

type Tag = { id: string; semver: string; published?: boolean };

type RelList = {
  versions: {
    id: string;
    semver: string;
    tag: string | null;
    tags?: string[];
    published: boolean;
    cover_count?: number;
    covers: { module_id: string; version?: string }[];
  }[];
  tags: Record<string, Tag | string>;
  can_create?: boolean;
  can_publish?: boolean;
  can_assign_tags?: boolean;
  can_delete?: boolean;
};

function tagId(tags: RelList["tags"], key: string): string {
  const t = tags[key];
  if (!t) return "";
  if (typeof t === "string") return t;
  return t.id || "";
}

const TAGS = [
  { id: "stable", label: "STABLE" },
  { id: "latest", label: "LATEST" },
  { id: "dev", label: "DEV" },
] as const;

function tagClass(tag: string) {
  if (tag === "stable") return "rel-tag stable";
  if (tag === "latest") return "rel-tag latest";
  if (tag === "dev") return "rel-tag dev";
  return "rel-tag";
}

export function ReleasesPage() {
  const { product = "usp" } = useParams();
  const qc = useQueryClient();
  const [tagSel, setTagSel] = useState<Record<string, string>>({});
  const [delAsk, setDelAsk] = useState<{ id: string; semver: string } | null>(null);
  const q = useQuery({
    queryKey: ["releases", product],
    queryFn: () => api<RelList>(`/api/v1/products/${product}/releases`),
  });
  const saveTags = useMutation({
    mutationFn: (body: Record<string, string>) =>
      api(`/api/v1/products/${product}/tags`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  const publish = useMutation({
    mutationFn: (rid: string) => api(`/api/v1/products/${product}/releases/${rid}/publish`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  const create = useMutation({
    mutationFn: (body: { semver: string; copy_from: string | null }) =>
      api<{ id?: string }>(`/api/v1/products/${product}/releases`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["releases", product] }),
  });
  const remove = useMutation({
    mutationFn: (rid: string) =>
      api(`/api/v1/products/${product}/releases/${rid}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["releases", product] });
      qc.invalidateQueries({ queryKey: ["graph", product] });
    },
  });
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">无法加载发布</p>;
  const d = q.data!;
  const current = Object.fromEntries(TAGS.map((t) => [t.id, tagSel[t.id] ?? tagId(d.tags, t.id)]));
  function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    create.mutate({
      semver: String(fd.get("semver") || ""),
      copy_from: String(fd.get("copy_from") || "") || null,
    });
    e.currentTarget.reset();
  }
  function verOpts() {
    return d.versions.map((v) => (
      <option key={v.id} value={v.id}>
        {v.semver}
      </option>
    ));
  }
  return (
    <>
      <div className="page-head">
        <h1>产品版本</h1>
      </div>
      {d.can_create ? (
        <form className="panel row" style={{ marginBottom: 16 }} onSubmit={onCreate}>
          <input name="semver" placeholder="v1.2.0" required style={{ width: 120 }} />
          <select name="copy_from" defaultValue={tagId(d.tags, "dev")}>
            <option value="">不复制涵盖</option>
            {verOpts()}
          </select>
          <button type="submit" className="btn" disabled={create.isPending}>
            新建版本
          </button>
          {create.isError ? <span className="err">{create.error instanceof ApiError ? create.error.detail : "失败"}</span> : null}
        </form>
      ) : null}
      <section className="panel" style={{ marginBottom: 16 }}>
        <table>
          <thead>
            <tr>
              <th>版本</th>
              <th>标签</th>
              <th>发布</th>
              <th>涵盖</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {d.versions.map((v) => (
              <tr key={v.id}>
                <td>
                  <Link to={`/${product}/m/${v.id}`}>{v.semver}</Link>
                </td>
                <td>
                  {(v.tags || (v.tag ? [v.tag] : [])).length
                    ? (v.tags || [v.tag!]).map((t) => (
                        <span key={t} className={tagClass(t)}>
                          {t.toUpperCase()}
                        </span>
                      ))
                    : "—"}
                </td>
                <td>{v.published ? "已发布" : "未发布"}</td>
                <td>{v.cover_count ?? v.covers.length}</td>
                <td>
                  <span className="row">
                    {d.can_publish && !v.published ? (
                      <button type="button" className="btn" onClick={() => publish.mutate(v.id)} disabled={publish.isPending}>
                        发布
                      </button>
                    ) : null}
                    {d.can_delete && !v.published ? (
                      <button
                        type="button"
                        className="btn danger"
                        onClick={() => setDelAsk({ id: v.id, semver: v.semver })}
                        disabled={remove.isPending}
                      >
                        删除
                      </button>
                    ) : null}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!d.versions.length ? <p className="empty">无版本</p> : null}
        {remove.isError ? <p className="err">{remove.error instanceof ApiError ? remove.error.detail : "失败"}</p> : null}
      </section>
      {d.can_assign_tags ? (
        <section className="panel">
          <h2 className="card-h">标签分配</h2>
          <div className="tag-assign">
            {TAGS.map((t) => (
              <label key={t.id}>
                {t.label}
                <select value={current[t.id] || ""} onChange={(e) => setTagSel((s) => ({ ...s, [t.id]: e.target.value }))}>
                  <option value="">—</option>
                  {verOpts()}
                </select>
              </label>
            ))}
          </div>
          <p className="row" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="btn primary"
              onClick={() => saveTags.mutate(current)}
              disabled={saveTags.isPending}
            >
              保存标签
            </button>
            {saveTags.isError ? <span className="err">{saveTags.error instanceof ApiError ? saveTags.error.detail : "失败"}</span> : null}
            {saveTags.isSuccess ? <span className="muted">已保存</span> : null}
          </p>
        </section>
      ) : (
        <p className="meta">
          STABLE {typeof d.tags.stable === "object" ? d.tags.stable.semver : d.tags.stable || "—"} · LATEST{" "}
          {typeof d.tags.latest === "object" ? d.tags.latest.semver : d.tags.latest || "—"} · DEV{" "}
          {typeof d.tags.dev === "object" ? d.tags.dev.semver : d.tags.dev || "—"}
        </p>
      )}
      {delAsk ? (
        <Confirm
          title="删除"
          body={delAsk.semver}
          confirm="删除"
          danger
          pending={remove.isPending}
          error={remove.isError ? (remove.error instanceof ApiError ? remove.error.detail : "失败") : undefined}
          onCancel={() => setDelAsk(null)}
          onConfirm={() => remove.mutate(delAsk.id, { onSuccess: () => setDelAsk(null) })}
        />
      ) : null}
    </>
  );
}
