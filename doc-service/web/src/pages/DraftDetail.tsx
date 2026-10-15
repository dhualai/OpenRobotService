import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, api, type Me } from "../api";
import { Confirm, Modal } from "../dialog";
import { DocHtml } from "../doc";
import { DocViews } from "../doc-views";
import { CheckIcon, CloseIcon, PencilIcon } from "../icons";
import { fmtWhen } from "../req";

type Person = { username: string; name: string };
type Draft = {
  id: string;
  title: string;
  html: string;
  markdown: string;
  status?: string;
  owner?: string;
  updated_at?: string | null;
  can_edit?: boolean;
  can_delete?: boolean;
  can_publish?: boolean;
  can_comment?: boolean;
  comments?: { id: number; author: string; body: string; quote?: string | null }[];
};

export function DraftDetailPage() {
  const { product = "usp", id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [edit, setEdit] = useState(false);
  const [delAsk, setDelAsk] = useState(false);
  const [pick, setPick] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const q = useQuery({
    queryKey: ["draft", id],
    queryFn: () => api<Draft>(`/api/v1/drafts/${id}?include_markdown=false`),
  });
  const mdq = useQuery({
    queryKey: ["draft-md", id],
    queryFn: () => api<Draft>(`/api/v1/drafts/${id}`),
    enabled: edit || Boolean(q.data?.can_edit),
  });
  const people = useQuery({
    queryKey: ["draft-people"],
    queryFn: () => api<{ items: Person[] }>("/api/v1/drafts/people"),
    enabled: pick,
  });
  const patch = useMutation({
    mutationFn: (body: { title?: string; markdown?: string }) =>
      api(`/api/v1/drafts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["draft", id] });
      qc.invalidateQueries({ queryKey: ["draft-md", id] });
      qc.invalidateQueries({ queryKey: ["drafts"] });
      if (vars.markdown !== undefined || vars.title !== undefined) setEdit(false);
    },
  });
  const publish = useMutation({
    mutationFn: (usernames: string[]) =>
      api(`/api/v1/drafts/${id}/publish`, { method: "POST", body: JSON.stringify({ usernames }) }),
    onSuccess: () => {
      setPick(false);
      qc.invalidateQueries({ queryKey: ["draft", id] });
      qc.invalidateQueries({ queryKey: ["drafts"] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/drafts/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["drafts"] });
      nav(`/${product}/drafts`);
    },
  });
  const comment = useMutation({
    mutationFn: (body: string) =>
      api(`/api/v1/drafts/${id}/comments`, { method: "POST", body: JSON.stringify({ body }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["draft", id] }),
  });
  useEffect(() => {
    if (!pick || !people.data) return;
    setPicked(new Set((people.data.items || []).map((u) => u.username)));
  }, [pick, people.data]);
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">{q.error instanceof ApiError ? q.error.detail : "无法打开"}</p>;
  const d = q.data!;
  function onDoc(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    patch.mutate({
      title: String(fd.get("title") || ""),
      markdown: String(fd.get("markdown") || ""),
    });
  }
  function onComment(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    comment.mutate(String(fd.get("body") || ""));
    e.currentTarget.reset();
  }
  function toggleUser(u: string) {
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(u)) n.delete(u);
      else n.add(u);
      return n;
    });
  }
  return (
    <>
      <div className="page-head req-head">
        <div>
          <h1>{d.title}</h1>
          <p className="req-meta">
            {d.owner ? <span>{d.owner}</span> : null}
            <time dateTime={d.updated_at || undefined}>{fmtWhen(d.updated_at)}</time>
          </p>
        </div>
        {edit ? (
          <span className="row">
            <button
              type="submit"
              form="draft-doc"
              className="icon-act is-ok"
              aria-label="保存"
              disabled={patch.isPending || mdq.isLoading || !mdq.data}
            >
              <CheckIcon />
            </button>
            <button
              type="button"
              className="icon-act is-off"
              aria-label="取消"
              onClick={() => setEdit(false)}
              disabled={patch.isPending}
            >
              <CloseIcon />
            </button>
          </span>
        ) : (
          <span className="row">
            {d.can_publish ? (
              <button type="button" className="btn primary" onClick={() => setPick(true)}>
                发布
              </button>
            ) : null}
            {d.can_edit ? (
              <button type="button" className="icon-act" aria-label="编辑" onClick={() => setEdit(true)}>
                <PencilIcon />
              </button>
            ) : null}
            {d.can_delete ? (
              <button type="button" className="btn danger" disabled={remove.isPending} onClick={() => setDelAsk(true)}>
                删除
              </button>
            ) : null}
          </span>
        )}
      </div>
      <div className="panel">
        {edit ? (
          mdq.isError ? (
            <p className="err">{mdq.error instanceof ApiError ? mdq.error.detail : "无法打开"}</p>
          ) : mdq.isLoading || !mdq.data ? (
            <p>…</p>
          ) : (
            <form id="draft-doc" onSubmit={onDoc}>
              <input name="title" defaultValue={mdq.data.title} required style={{ width: "100%", marginBottom: 10 }} />
              <textarea name="markdown" defaultValue={mdq.data.markdown} rows={16} />
              {patch.isError ? (
                <p className="err">{patch.error instanceof ApiError ? patch.error.detail : "失败"}</p>
              ) : null}
            </form>
          )
        ) : (
          <DocHtml
            html={d.html || ""}
            canEdit={d.can_edit}
            markdown={mdq.data?.markdown}
            onSave={(md) => patch.mutate({ markdown: md })}
            saving={patch.isPending}
            saveError={patch.isError ? (patch.error instanceof ApiError ? patch.error.detail : "失败") : undefined}
          />
        )}
      </div>
      <DocViews path={`/api/v1/drafts/${id}/views`} enabled={Boolean(me.data?.can_protect)} />
      <div className="panel" style={{ marginTop: 16 }}>
        <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>评论</h2>
        <ul className="comments">
          {(d.comments || []).map((c) => (
            <li key={c.id}>
              <span className="who">{c.author}</span>
              {c.quote ? <span className="muted">「{c.quote}」 </span> : null}
              {c.body}
            </li>
          ))}
        </ul>
        {d.can_comment ? (
          <form onSubmit={onComment} className="row">
            <input name="body" placeholder="留言" required style={{ flex: 1 }} />
            <button type="submit" className="btn primary" disabled={comment.isPending}>
              发送
            </button>
          </form>
        ) : null}
        {comment.isError ? (
          <p className="err">{comment.error instanceof ApiError ? comment.error.detail : "失败"}</p>
        ) : null}
      </div>
      {pick ? (
        <Modal
          title="谁可见"
          wide
          onClose={() => setPick(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setPick(false)}>
                取消
              </button>
              <button
                type="button"
                className="btn primary"
                disabled={publish.isPending}
                onClick={() => publish.mutate([...picked])}
              >
                发布
              </button>
            </>
          }
        >
          <ul className="req-people">
            {(people.data?.items || []).map((u) => (
              <li key={u.username}>
                <label>
                  <input type="checkbox" checked={picked.has(u.username)} onChange={() => toggleUser(u.username)} />
                  {u.name}
                </label>
              </li>
            ))}
          </ul>
          {publish.isError ? (
            <p className="err">{publish.error instanceof ApiError ? publish.error.detail : "失败"}</p>
          ) : null}
        </Modal>
      ) : null}
      {delAsk ? (
        <Confirm
          title="删除"
          body={`「${d.title}」`}
          confirm="删除"
          danger
          pending={remove.isPending}
          error={remove.isError ? (remove.error instanceof ApiError ? remove.error.detail : "失败") : undefined}
          onCancel={() => setDelAsk(false)}
          onConfirm={() => remove.mutate()}
        />
      ) : null}
    </>
  );
}
