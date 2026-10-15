import { NavLink, Navigate, Outlet, useLocation, useNavigate, useParams } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me } from "../api";
import { Confirm } from "../dialog";
import { peerHome, peerPath, upHref } from "../boards";
import { BackIcon } from "../icons";
import { MigrationBanner } from "../migrate";

const STAFF = new Set(["admin", "engineer", "tester", "supervisor"]);

type InboxItem = { product: string; id: string; title: string; can_skip?: boolean };
type Inbox = { items: InboxItem[]; count: number };

function AccountIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5.5 19.5c1.8-3.2 4-4.8 6.5-4.8s4.7 1.6 6.5 4.8" />
    </svg>
  );
}

function AccountMenu({ product }: { product: string }) {
  const [open, setOpen] = useState(false);
  const [skipAsk, setSkipAsk] = useState<InboxItem | null>(null);
  const wrap = useRef<HTMLSpanElement>(null);
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me"), staleTime: 0 });
  const reviews = useQuery({
    queryKey: ["inbox-reviews"],
    queryFn: () => api<Inbox>("/api/v1/inbox/reviews"),
  });
  const links = useQuery({
    queryKey: ["inbox-pending-links"],
    queryFn: () => api<Inbox>("/api/v1/inbox/pending-links"),
  });
  const skip = useMutation({
    mutationFn: (it: InboxItem) =>
      api(`/api/v1/products/${it.product}/requirements/${it.id}/skip-link`, { method: "POST" }),
    onSuccess: () => {
      setSkipAsk(null);
      qc.invalidateQueries({ queryKey: ["inbox-pending-links"] });
      qc.invalidateQueries({ queryKey: ["reqs"] });
      qc.invalidateQueries({ queryKey: ["req"] });
    },
  });
  useEffect(() => {
    if (!open) return;
    function onPtr(e: PointerEvent) {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPtr);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPtr);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  const reviewItems = reviews.data?.items || [];
  const linkItems = links.data?.items || [];
  const n = reviewItems.length + linkItems.length;
  const admin = me.data?.user.role === "admin" || Boolean(me.data?.can_manage_users);
  return (
    <span className="account-wrap" ref={wrap}>
      <span className="account-name">{me.data?.user.name || me.data?.user.username}</span>
      <button
        type="button"
        className={`account-btn${open ? " is-open" : ""}`}
        aria-label="账号"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <AccountIcon />
        {n ? <span className="account-badge">{n > 9 ? "9+" : n}</span> : null}
      </button>
      {open ? (
        <div className="account-pop">
          {reviewItems.length ? <p className="account-pop-label">待评审</p> : null}
          {reviewItems.map((it) => (
            <NavLink
              key={`rev:${it.product}:${it.id}`}
              to={`/${it.product}/requirements/${it.id}`}
              className="account-pop-item"
              onClick={() => setOpen(false)}
            >
              {it.title}
            </NavLink>
          ))}
          {linkItems.length ? <p className="account-pop-label">建功能并关联</p> : null}
          {linkItems.map((it) => (
            <span key={`link:${it.product}:${it.id}`} className="account-pop-row">
              <NavLink
                to={`/${it.product}/requirements/${it.id}`}
                className="account-pop-item"
                onClick={() => setOpen(false)}
              >
                {it.title}
              </NavLink>
              {it.can_skip ? (
                <button
                  type="button"
                  className="account-pop-skip"
                  title="无相关功能开发"
                  aria-label={`确认无相关功能开发：${it.title}`}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setOpen(false);
                    setSkipAsk(it);
                  }}
                >
                  无功能
                </button>
              ) : null}
            </span>
          ))}
          {admin ? (
            <>
              <NavLink to={`/${product}/audit`} className="account-pop-item" onClick={() => setOpen(false)}>
                更新记录
              </NavLink>
              <NavLink to={`/${product}/visits`} className="account-pop-item" onClick={() => setOpen(false)}>
                浏览记录
              </NavLink>
            </>
          ) : null}
          <NavLink to={`/${product}/account`} className="account-pop-item" onClick={() => setOpen(false)}>
            账号设置
          </NavLink>
        </div>
      ) : null}
      {skipAsk ? (
        <Confirm
          title="确认无相关功能开发"
          body={`「${skipAsk.title}」评审已通过，将不再提醒建立或关联功能文档。`}
          confirm="确认无相关功能开发"
          pending={skip.isPending}
          error={skip.isError ? (skip.error instanceof ApiError ? skip.error.detail : "失败") : undefined}
          onCancel={() => {
            skip.reset();
            setSkipAsk(null);
          }}
          onConfirm={() => skip.mutate(skipAsk)}
        />
      ) : null}
    </span>
  );
}

function BackBtn({ product }: { product: string }) {
  const nav = useNavigate();
  const loc = useLocation();
  return (
    <button
      type="button"
      className="back-btn"
      aria-label="返回上级"
      onClick={() => nav(upHref(product, loc.pathname, loc.search))}
    >
      <BackIcon />
    </button>
  );
}

export function AppShell() {
  const loc = useLocation();
  const { product = "usp" } = useParams();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/api/v1/me"),
    retry: false,
    staleTime: 0,
  });
  if (q.isLoading) return <p className="wrap">…</p>;
  if (q.isError) return <Navigate to="/login" replace />;
  const products = q.data?.user.products || [];
  const role = q.data?.user.role || "";
  const home = /^\/(usp|ors)\/?$/.test(loc.pathname);
  const hoursPage = /\/hours(\/|$)/.test(loc.pathname);
  const editor = /\/editor\/?$/.test(loc.pathname);
  const modulePage = /\/m\//.test(loc.pathname);
  const reqDetail = /\/requirements\/[^/]+/.test(loc.pathname);
  const draftDetail = /\/drafts\/[^/]+/.test(loc.pathname);
  const wrapCls = home ? "wrap wide" : modulePage || reqDetail || draftDetail ? "wrap module-wrap" : "wrap";
  const staff = STAFF.has(role);
  const admin = role === "admin";
  function userLink({ isActive }: { isActive: boolean }) {
    return isActive ? "user-link active" : "user-link";
  }
  if (editor) {
    return (
      <div className="app is-editor">
        <Outlet />
      </div>
    );
  }
  return (
    <div className="app">
      <div className={wrapCls}>
        <div className="user-bar">
          {home ? null : <BackBtn product={product} />}
          {staff && home ? (
            <NavLink to={`/${product}/hours/history`} className={userLink}>
              工时统计
            </NavLink>
          ) : null}
          {admin && home ? (
            <NavLink to={`/${product}/users`} className={userLink}>
              用户
            </NavLink>
          ) : null}
          {home ? (
            <NavLink to={`/${product}/drafts`} className={userLink}>
              讨论区
            </NavLink>
          ) : null}
          <div className="user-bar-spacer" />
          <AccountMenu product={product} />
        </div>
        {home || hoursPage ? (
          <nav className="product-tabs" aria-label="产品">
            {products.includes("usp") ? (
              <NavLink
                to={hoursPage ? peerPath("usp", loc.pathname) : peerHome("usp", loc.search)}
                className={product === "usp" ? "active" : ""}
              >
                USP
              </NavLink>
            ) : null}
            {products.includes("ors") ? (
              <NavLink
                to={hoursPage ? peerPath("ors", loc.pathname) : peerHome("ors", loc.search)}
                className={product === "ors" ? "active" : ""}
              >
                ORS
              </NavLink>
            ) : null}
          </nav>
        ) : null}
        {home ? <MigrationBanner url={q.data?.migrated_url} /> : null}
        <Outlet />
      </div>
    </div>
  );
}
