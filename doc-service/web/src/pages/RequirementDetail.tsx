import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, type Me } from "../api";
import { Confirm, Modal } from "../dialog";
import { DocHtml } from "../doc";
import { DocViews } from "../doc-views";
import { CheckIcon, CloseIcon, PencilIcon } from "../icons";
import { REQ_STATUS, REQ_VERDICT } from "../labels";
import { fmtWhen, PriorityField, ReqFeatTag, type ReqFeature } from "../req";

function fmtRoundWhen(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

type Reviewer = { username: string; name: string; verdict?: string | null; reason?: string | null };
type ReviewRound = {
  id: number;
  started_by: string;
  started_at?: string | null;
  outcome?: string | null;
  reviewers?: Reviewer[];
};
type Person = { username: string; name: string; role: string };
type Req = {
  title: string;
  status: string;
  html: string;
  markdown: string;
  can_edit: boolean;
  can_delete?: boolean;
  can_start_review?: boolean;
  can_verdict?: boolean;
  can_skip_link?: boolean;
  my_verdict?: string | null;
  created_by: string;
  updated_at?: string | null;
  priority: number;
  comments: { id: number; author: string; body: string; quote?: string | null }[];
  reviewers?: Reviewer[];
  review_rounds?: ReviewRound[];
  features?: ReqFeature[];
  revision_count?: number;
};

export function RequirementDetailPage() {
  const { product = "usp", id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [edit, setEdit] = useState(false);
  const [pick, setPick] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [reasonFor, setReasonFor] = useState<"clarify" | "reject" | null>(null);
  const [reason, setReason] = useState("");
  const [delAsk, setDelAsk] = useState(false);
  const [skipAsk, setSkipAsk] = useState(false);
  const [revOpen, setRevOpen] = useState(false);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const q = useQuery({
    queryKey: ["req", product, id],
    queryFn: () => api<Req>(`/api/v1/products/${product}/requirements/${id}?include_markdown=false`),
  });
  const mdq = useQuery({
    queryKey: ["req-md", product, id],
    queryFn: () => api<Req>(`/api/v1/products/${product}/requirements/${id}`),
    enabled: edit || Boolean(q.data?.can_edit),
  });
  const revs = useQuery({
    queryKey: ["req-revs", product, id],
    queryFn: () =>
      api<{ items: { id: number; author: string; created_at?: string | null }[]; can_restore?: boolean }>(
        `/api/v1/products/${product}/requirements/${id}/revisions`
      ),
    enabled: q.data != null,
    staleTime: 20_000,
  });
  const people = useQuery({
    queryKey: ["review-people", product],
    queryFn: () => api<{ items: Person[] }>(`/api/v1/products/${product}/review-people`),
    enabled: pick,
  });
  const comment = useMutation({
    mutationFn: (body: string) =>
      api(`/api/v1/products/${product}/requirements/${id}/comments`, {
        method: "POST",
        body: JSON.stringify({ body }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["req", product, id] }),
  });
  const patch = useMutation({
    mutationFn: (body: { markdown?: string; priority?: number }) =>
      api(`/api/v1/products/${product}/requirements/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["req", product, id] });
      qc.invalidateQueries({ queryKey: ["req-md", product, id] });
      qc.invalidateQueries({ queryKey: ["req-revs", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      if (vars.markdown !== undefined) setEdit(false);
    },
  });
  const restore = useMutation({
    mutationFn: (revision_id: number) =>
      api(`/api/v1/products/${product}/requirements/${id}/revisions/restore`, {
        method: "POST",
        body: JSON.stringify({ revision_id }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["req", product, id] });
      qc.invalidateQueries({ queryKey: ["req-md", product, id] });
      qc.invalidateQueries({ queryKey: ["req-revs", product, id] });
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/v1/products/${product}/requirements/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      nav(`/${product}?tab=reqs`);
    },
  });
  const skipLink = useMutation({
    mutationFn: () => api(`/api/v1/products/${product}/requirements/${id}/skip-link`, { method: "POST" }),
    onSuccess: () => {
      setSkipAsk(false);
      qc.invalidateQueries({ queryKey: ["req", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      qc.invalidateQueries({ queryKey: ["inbox-pending-links"] });
    },
  });
  const startReview = useMutation({
    mutationFn: (usernames: string[]) =>
      api(`/api/v1/products/${product}/requirements/${id}/review`, {
        method: "POST",
        body: JSON.stringify({ usernames }),
      }),
    onSuccess: () => {
      setPick(false);
      qc.invalidateQueries({ queryKey: ["req", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      qc.invalidateQueries({ queryKey: ["inbox-reviews"] });
      qc.invalidateQueries({ queryKey: ["inbox-pending-links"] });
    },
  });
  const verdict = useMutation({
    mutationFn: (body: { verdict: string; reason?: string }) =>
      api(`/api/v1/products/${product}/requirements/${id}/verdict`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      setReasonFor(null);
      setReason("");
      qc.invalidateQueries({ queryKey: ["req", product, id] });
      qc.invalidateQueries({ queryKey: ["reqs", product] });
      qc.invalidateQueries({ queryKey: ["inbox-reviews"] });
      qc.invalidateQueries({ queryKey: ["inbox-pending-links"] });
    },
  });
  useEffect(() => {
    if (!pick) return;
    const rounds = q.data?.review_rounds || [];
    const last = rounds[rounds.length - 1];
    const src = last?.reviewers || q.data?.reviewers || [];
    setPicked(new Set(src.map((r) => r.username)));
  }, [pick, q.data?.review_rounds, q.data?.reviewers]);
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">{q.error instanceof ApiError ? q.error.detail : "无法打开需求"}</p>;
  const r = q.data!;
  function onComment(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    comment.mutate(String(fd.get("body") || ""));
    e.currentTarget.reset();
  }
  function onDoc(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    patch.mutate({ markdown: String(fd.get("markdown") || "") });
  }
  function toggleUser(u: string) {
    setPicked((prev) => {
      const n = new Set(prev);
      if (n.has(u)) n.delete(u);
      else n.add(u);
      return n;
    });
  }
  const feats = r.features || [];
  const revItems = revs.data?.items || [];
  const revCount = r.revision_count ?? revItems.length;
  return (
    <>
      <div className="page-head req-head">
        <div>
          <h1>{r.title}</h1>
          <p className="req-meta">
            <span>{r.created_by}</span>
            <time dateTime={r.updated_at || undefined}>{fmtWhen(r.updated_at)}</time>
          </p>
          {feats.length ? (
            <p className="req-feats">
              {feats.map((f) => (
                <ReqFeatTag key={f.id} product={product} feature={f} />
              ))}
            </p>
          ) : null}
          {r.status === "pending_link" ? (
            <p className="row req-link-hint">
              <Link className="btn primary" to={`/${product}`}>
                去结构图建功能并关联
              </Link>
              {r.can_skip_link ? (
                <button type="button" className="btn" onClick={() => setSkipAsk(true)} disabled={skipLink.isPending}>
                  无相关功能开发
                </button>
              ) : null}
            </p>
          ) : null}
        </div>
        <span className="chip">{REQ_STATUS[r.status] || r.status}</span>
        <PriorityField
          value={r.priority}
          canEdit={r.can_edit}
          disabled={patch.isPending}
          onChange={(n) => patch.mutate({ priority: n })}
        />
        {r.can_edit ? (
          edit ? (
            <span className="row">
              <button type="submit" form="req-doc" className="icon-act is-ok" aria-label="保存" disabled={patch.isPending || mdq.isLoading || !mdq.data}>
                <CheckIcon />
              </button>
              <button type="button" className="icon-act is-off" aria-label="取消" onClick={() => setEdit(false)} disabled={patch.isPending}>
                <CloseIcon />
              </button>
            </span>
          ) : (
            <button type="button" className="icon-act" aria-label="编辑" onClick={() => setEdit(true)}>
              <PencilIcon />
            </button>
          )
        ) : null}
      </div>
      <div className="panel">
            {edit ? (
              mdq.isError ? (
                <p className="err">{mdq.error instanceof ApiError ? mdq.error.detail : "无法打开需求"}</p>
              ) : mdq.isLoading || !mdq.data ? (
                <p>…</p>
              ) : (
                <form id="req-doc" onSubmit={onDoc}>
                  <textarea name="markdown" defaultValue={mdq.data.markdown} rows={14} />
                  {patch.isError ? <p className="err">{patch.error instanceof ApiError ? patch.error.detail : "失败"}</p> : null}
                </form>
              )
            ) : (
              <DocHtml
                html={r.html || ""}
                canEdit={r.can_edit}
                markdown={mdq.data?.markdown}
                onSave={(md) => patch.mutate({ markdown: md })}
                saving={patch.isPending}
                saveError={patch.isError ? (patch.error instanceof ApiError ? patch.error.detail : "失败") : undefined}
              />
            )}
          </div>
          <DocViews
            path={`/api/v1/products/${product}/requirements/${id}/views`}
            enabled={Boolean(me.data?.can_protect)}
          />
          <div className="panel" style={{ marginTop: 16 }}>
            <h2 style={{ fontSize: 15, margin: "0 0 8px" }}>评论</h2>
            <ul className="comments">
              {(r.comments || []).map((c) => (
                <li key={c.id}>
                  <span className="who">{c.author}</span>
                  {c.quote ? <span className="muted">「{c.quote}」 </span> : null}
                  {c.body}
                </li>
              ))}
            </ul>
            {["admin", "engineer", "tester", "pm", "pjm", "supervisor"].includes(me.data?.user.role || "") ? (
              <form onSubmit={onComment} className="row">
                <input name="body" placeholder="留言" required style={{ flex: 1 }} />
                <button type="submit" className="btn primary">
                  发送
                </button>
              </form>
            ) : null}
          </div>
          {r.can_start_review || r.can_verdict || r.can_delete ? (
            <p className="row req-review-actions">
              {r.can_start_review ? (
                <button type="button" className="btn primary" onClick={() => setPick(true)}>
                  评审
                </button>
              ) : null}
              {r.can_verdict ? (
                <>
                  <button
                    type="button"
                    className="btn primary"
                    disabled={verdict.isPending}
                    onClick={() => verdict.mutate({ verdict: "pass" })}
                  >
                    通过
                  </button>
                  <button type="button" className="btn" disabled={verdict.isPending} onClick={() => setReasonFor("clarify")}>
                    待明确
                  </button>
                  <button type="button" className="btn danger" disabled={verdict.isPending} onClick={() => setReasonFor("reject")}>
                    拒绝
                  </button>
                </>
              ) : null}
              {r.can_delete ? (
                <button type="button" className="btn danger" disabled={remove.isPending} onClick={() => setDelAsk(true)}>
                  删除
                </button>
              ) : null}
            </p>
          ) : null}
          {(r.review_rounds || []).length ? (
            <ol className="req-rounds">
              {(r.review_rounds || []).map((rnd, i) => (
                <li key={rnd.id}>
                  <div className="req-round-h">
                    <span className="n">{i + 1}.</span>
                    <span className="who">{rnd.started_by}</span>
                    <time dateTime={rnd.started_at || undefined}>{fmtRoundWhen(rnd.started_at)}</time>
                    <span className="act">发起评审</span>
                  </div>
                  <ul className="req-reviewers">
                    {(rnd.reviewers || []).map((rv) => {
                      const v = rv.verdict || "";
                      return (
                        <li key={rv.username} data-v={v || "wait"}>
                          <span className="req-rv-name">{rv.name}</span>
                          <span className="req-rv-v">{v ? REQ_VERDICT[v] || v : "待评"}</span>
                          {rv.reason ? <p className="req-rv-reason">{rv.reason}</p> : null}
                        </li>
                      );
                    })}
                  </ul>
                  {rnd.outcome === "pass" ? <p className="req-round-out is-pass">评审通过</p> : null}
                  {rnd.outcome === "fail" ? <p className="req-round-out is-fail">评审未通过</p> : null}
                </li>
              ))}
            </ol>
          ) : null}
      {pick ? (
        <Modal
          title="评审"
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
                disabled={!picked.size || startReview.isPending}
                onClick={() => startReview.mutate([...picked])}
              >
                确定
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
          {startReview.isError ? (
            <p className="err">{startReview.error instanceof ApiError ? startReview.error.detail : "失败"}</p>
          ) : null}
        </Modal>
      ) : null}
      {reasonFor ? (
        <Modal
          title={REQ_VERDICT[reasonFor]}
          onClose={() => setReasonFor(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setReasonFor(null)}>
                取消
              </button>
              <button
                type="button"
                className={reasonFor === "reject" ? "btn danger" : "btn primary"}
                disabled={!reason.trim() || verdict.isPending}
                onClick={() => verdict.mutate({ verdict: reasonFor, reason: reason.trim() })}
              >
                确定
              </button>
            </>
          }
        >
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={4} required autoFocus />
          {verdict.isError ? (
            <p className="err">{verdict.error instanceof ApiError ? verdict.error.detail : "失败"}</p>
          ) : null}
        </Modal>
      ) : null}
      {delAsk ? (
        <Confirm
          title="删除"
          body={`「${r.title}」`}
          confirm="删除"
          danger
          pending={remove.isPending}
          error={remove.isError ? (remove.error instanceof ApiError ? remove.error.detail : "失败") : undefined}
          onCancel={() => setDelAsk(false)}
          onConfirm={() => remove.mutate()}
        />
      ) : null}
      {skipAsk ? (
        <Confirm
          title="确认无相关功能开发"
          body={`「${r.title}」评审已通过，将不再提醒建立或关联功能文档。`}
          confirm="确认无相关功能开发"
          pending={skipLink.isPending}
          error={skipLink.isError ? (skipLink.error instanceof ApiError ? skipLink.error.detail : "失败") : undefined}
          onCancel={() => {
            skipLink.reset();
            setSkipAsk(false);
          }}
          onConfirm={() => skipLink.mutate()}
        />
      ) : null}
      {revCount || r.can_edit ? (
        <div className={`rev-corner${revOpen ? " is-open" : ""}`}>
          <button type="button" className="rev-toggle" onClick={() => setRevOpen(!revOpen)}>
            修订
            <span className="rev-count">{revCount}</span>
          </button>
          {revOpen ? (
            <div className="rev-panel">
              <ul className="rev-list">
                {revItems.map((it) => (
                  <li key={it.id}>
                    <span className="rev-id">{it.author}</span>
                    <span className="rev-meta">
                      {fmtWhen(it.created_at)}
                      {revs.data?.can_restore ? (
                        <button
                          type="button"
                          className="btn"
                          onClick={() => restore.mutate(it.id)}
                          disabled={restore.isPending}
                        >
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
    </>
  );
}
