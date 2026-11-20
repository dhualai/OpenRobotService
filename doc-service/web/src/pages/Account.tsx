import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiUrl, type Me } from "../api";
import { orsLoginUrl, orsToken } from "../ors-session";
import { resetSession } from "../session";

const ROLE: Record<string, string> = {
  admin: "管理员",
  engineer: "开发工程师",
  tester: "测试工程师",
  pm: "产品经理",
  pjm: "项目经理",
  supervisor: "部门主管",
  guest: "访客",
};

const PRODUCT: Record<string, string> = { usp: "USP", ors: "ORS" };

export function AccountPage() {
  const { product = "usp" } = useParams();
  const qc = useQueryClient();
  const [help, setHelp] = useState(false);
  const [packMsg, setPackMsg] = useState("");
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const inbox = useQuery({
    queryKey: ["inbox-reviews"],
    queryFn: () => api<{ items: { product: string; id: string; title: string }[]; count: number }>("/api/v1/inbox/reviews"),
  });
  const pendingLinks = useQuery({
    queryKey: ["inbox-pending-links"],
    queryFn: () =>
      api<{ items: { product: string; id: string; title: string }[]; count: number }>("/api/v1/inbox/pending-links"),
  });
  const u = me.data?.user;
  const role = u?.role || "";
  const products = (u?.products || []).map((p) => PRODUCT[p] || p).join("、") || "—";
  async function downloadPack() {
    setPackMsg("");
    const r = await fetch(apiUrl("/api/v1/account/auth-pack"), {
      credentials: "include",
      headers: { "X-Topo-Origin": window.location.origin, Authorization: `Bearer ${orsToken()}` },
    });
    if (!r.ok) {
      setPackMsg("下载失败");
      return;
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `topo-agent-auth-${u?.username || "user"}.zip`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  return (
    <div className="account-page">
      <h1>账号设置</h1>
      {(inbox.data?.items || []).length ? (
        <section className="panel">
          <h2 className="card-h">待评审</h2>
          <ul className="account-inbox">
            {(inbox.data?.items || []).map((it) => (
              <li key={`${it.product}:${it.id}`}>
                <Link to={`/${it.product}/requirements/${it.id}`}>{it.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {(pendingLinks.data?.items || []).length ? (
        <section className="panel">
          <h2 className="card-h">建功能并关联</h2>
          <ul className="account-inbox">
            {(pendingLinks.data?.items || []).map((it) => (
              <li key={`${it.product}:${it.id}`}>
                <Link to={`/${it.product}/requirements/${it.id}`}>{it.title}</Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <section className="panel">
        <h2 className="card-h">基本信息</h2>
        <div className="kv">
          <span className="k">姓名</span>
          <span className="v">{u?.name || u?.username || "…"}</span>
        </div>
        <div className="kv">
          <span className="k">角色</span>
          <span className="v">{ROLE[role] || role || "…"}</span>
        </div>
        <div className="kv">
          <span className="k">可访问产品</span>
          <span className="v">{products}</span>
        </div>
        <div className="kv">
          <span className="k">当前自然周</span>
          <span className="v">{me.data?.week || "—"}</span>
        </div>
      </section>
      {me.data?.can_edit_requirements ? (
        <section className="panel">
          <h2 className="card-h">需求</h2>
          <div className="actions">
            <Link className="btn" to={`/${product}/requirements`}>
              {PRODUCT[product] || product.toUpperCase()} · 需求视图
            </Link>
          </div>
        </section>
      ) : null}
      {me.data?.can_manage_users ? (
        <section className="panel">
          <h2 className="card-h">管理</h2>
          <div className="actions">
            <Link className="btn" to={`/${product}/users`}>
              用户权限
            </Link>
            <Link className="btn" to={`/${product}/releases`}>
              产品版本
            </Link>
            <Link className="btn" to={`/${product}/audit`}>
              更新记录
            </Link>
            <Link className="btn" to={`/${product}/visits`}>
              浏览记录
            </Link>
          </div>
        </section>
      ) : me.data?.can_product_versions ? (
        <section className="panel">
          <h2 className="card-h">产品版本</h2>
          <div className="actions">
            <Link className="btn" to={`/${product}/releases`}>
              版本与标签
            </Link>
          </div>
        </section>
      ) : null}
      {me.data?.can_auth_pack ? (
        <section className="panel">
          <div className="card-head">
            <h2 className="card-h">Agent 授权包</h2>
            <button type="button" className="btn-info" onClick={() => setHelp(true)} aria-label="授权包说明">
              i
            </button>
          </div>
          <div className="actions">
            <button type="button" className="btn primary" onClick={downloadPack}>
              下载授权包
            </button>
            {packMsg ? <span className="err">{packMsg}</span> : null}
          </div>
        </section>
      ) : null}
      <section className="panel">
        <h2 className="card-h">账号</h2>
        <p className="muted">
          账号与密码统一由摇人吧管理，本页不再提供改密；如需修改请到摇人吧账号设置操作。
        </p>
      </section>
      <section className="panel">
        <h2 className="card-h">会话</h2>
        <div className="actions">
          <button
            type="button"
            className="btn danger"
            onClick={() => {
              resetSession(qc);
              // 登出交回摇人吧：清本地缓存后回摇人吧登录页
              window.location.assign(orsLoginUrl());
            }}
          >
            退出登录
          </button>
        </div>
      </section>
      {help ? (
        <>
          <div className="drawer-backdrop is-open" onClick={() => setHelp(false)} />
          <aside className="drawer is-open" role="dialog" aria-labelledby="auth-pack-title">
            <div className="drawer-head">
              <h3 id="auth-pack-title">授权包</h3>
              <button type="button" className="drawer-close" onClick={() => setHelp(false)} aria-label="关闭">
                ×
              </button>
            </div>
            <div className="drawer-body">
              <p>授权包给 Agent 用，内含登录令牌、可写模块列表，以及一份 AGENTS.md 副本。</p>
              <p>行文规则以工程包里的 AGENTS.md 为准，最后一行是校验码。规则更新后旧包 import 会失败，返回会要求全文重读 AGENTS.md，尤其行文。须重新 export。不必重下授权包。</p>
              <p>重新下载授权包会作废旧令牌，只在令牌丢失或换账号时使用。</p>
            </div>
          </aside>
        </>
      ) : null}
    </div>
  );
}
