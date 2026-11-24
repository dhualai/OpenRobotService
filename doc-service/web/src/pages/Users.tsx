import { FormEvent, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me } from "../api";

type Perms = Record<string, string>;
type User = {
  username: string;
  name?: string;
  role: string;
  role_label: string;
  products: string[];
  module_perms: Perms;
};
type CatNode = { id: string; title: string; kind: string; encrypted: boolean; children?: CatNode[] };
type CatBranch = { id: string; tag: string; nodes: CatNode[] };
type CatMod = { id: string; title: string; kind: string; domain: string; encrypted: boolean };
type Catalog = { domains: { id: string; tag: string }[]; modules: CatMod[]; tree?: CatBranch[] };

const ROLES: { id: string; label: string }[] = [
  { id: "admin", label: "管理员" },
  { id: "engineer", label: "开发工程师" },
  { id: "tester", label: "测试工程师" },
  { id: "pm", label: "产品经理" },
  { id: "pjm", label: "项目经理" },
  { id: "supervisor", label: "部门主管" },
  { id: "guest", label: "访客" },
];

function productAll(product: string) {
  return `${product}:*`;
}
function domainAllKey(product: string, domain: string) {
  return `${product}:${domain}:*`;
}
function isDomainAll(k: string, product: string) {
  const p = k.split(":");
  return p.length === 3 && p[0] === product && p[2] === "*";
}

function walkIds(nodes: CatNode[]): string[] {
  return nodes.flatMap((n) => [n.id, ...walkIds(n.children || [])]);
}

function descendants(n: CatNode): string[] {
  return walkIds(n.children || []);
}

function nodeMatch(n: CatNode, q: string): boolean {
  if (!q) return true;
  const s = q.toLowerCase();
  if ((n.title || "").toLowerCase().includes(s) || n.id.toLowerCase().includes(s)) return true;
  return (n.children || []).some((c) => nodeMatch(c, s));
}

function titleMap(branches: CatBranch[]): Map<string, string> {
  const m = new Map<string, string>();
  function w(list: CatNode[]) {
    for (const n of list) {
      m.set(n.id, n.title);
      w(n.children || []);
    }
  }
  branches.forEach((b) => w(b.nodes || []));
  return m;
}

function pruneImplied(ids: Set<string>, branches: CatBranch[]): Set<string> {
  const implied = new Set<string>();
  function w(n: CatNode, under: boolean) {
    const grant = under || ids.has(n.id);
    for (const c of n.children || []) {
      if (grant) implied.add(c.id);
      w(c, grant);
    }
  }
  branches.forEach((b) => (b.nodes || []).forEach((n) => w(n, false)));
  const out = new Set<string>();
  ids.forEach((id) => {
    if (!implied.has(id)) out.add(id);
  });
  return out;
}

function LockMark() {
  return (
    <svg className="perm-lock" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="5" y="11" width="14" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 0 1 8 0v3" />
    </svg>
  );
}

function PermNode({
  n,
  q,
  depth,
  ancestors,
  all,
  domainOn,
  checked,
  open,
  setOpen,
  onToggle,
}: {
  n: CatNode;
  q: string;
  depth: number;
  ancestors: string[];
  all: boolean;
  domainOn: boolean;
  checked: Set<string>;
  open: Record<string, boolean>;
  setOpen: (id: string, v: boolean) => void;
  onToggle: (n: CatNode, on: boolean) => void;
}) {
  if (!nodeMatch(n, q)) return null;
  const kids = (n.children || []).filter((c) => nodeMatch(c, q));
  const kidIds = descendants(n);
  const inherited = all || domainOn || ancestors.some((a) => checked.has(a));
  const on = inherited || checked.has(n.id);
  const some = !on && kidIds.some((id) => checked.has(id));
  const hasKids = (n.children || []).length > 0;
  const autoOpen = Boolean(q) || kidIds.some((id) => checked.has(id));
  const isOpen = hasKids && (open[n.id] ?? autoOpen);
  const locked = all || domainOn || inherited;
  return (
    <div className={`perm-node${n.kind === "group" ? " is-group" : ""}`}>
      <div className="perm-row" style={{ paddingLeft: 8 + depth * 16 }}>
        {hasKids ? (
          <button type="button" className="twist" onClick={() => setOpen(n.id, !isOpen)}>
            {isOpen ? "▼" : "▶"}
          </button>
        ) : (
          <span className="twist is-leaf" />
        )}
        <label>
          <input
            type="checkbox"
            disabled={locked}
            checked={on}
            ref={(el) => {
              if (el) el.indeterminate = some;
            }}
            onChange={(e) => onToggle(n, e.target.checked)}
          />
          <span className="t">{n.title}</span>
          {n.encrypted ? <LockMark /> : null}
          {hasKids && !q ? <span className="n">{kidIds.length}</span> : null}
        </label>
      </div>
      {hasKids && isOpen
        ? kids.map((c) => (
            <PermNode
              key={c.id}
              n={c}
              q={q}
              depth={depth + 1}
              ancestors={[...ancestors, n.id]}
              all={all}
              domainOn={domainOn}
              checked={checked}
              open={open}
              setOpen={setOpen}
              onToggle={onToggle}
            />
          ))
        : null}
    </div>
  );
}

export function UsersPage() {
  const qc = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me") });
  const allowed = Boolean(me.data?.can_manage_users);
  const [sel, setSel] = useState("");
  const [catProduct, setCatProduct] = useState("usp");
  const [role, setRole] = useState("engineer");
  const [disp, setDisp] = useState("");
  const [prods, setProds] = useState<string[]>(["usp"]);
  const [all, setAll] = useState(false);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [domainAlls, setDomainAlls] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const users = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api<{ users: User[] }>("/api/v1/admin/users"),
    enabled: allowed,
  });
  const cat = useQuery({
    queryKey: ["admin-cat", catProduct],
    queryFn: () => api<Catalog>(`/api/v1/admin/catalog?product=${catProduct}`),
    enabled: allowed,
  });
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api("/api/v1/admin/users", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  const list = users.data?.users || [];
  const selected = list.find((u) => u.username === sel);
  const modules = cat.data?.modules || [];
  const domains = cat.data?.domains || [];
  const branches: CatBranch[] = useMemo(() => {
    if (cat.data?.tree?.length) return cat.data.tree;
    const m: Record<string, CatMod[]> = {};
    modules.forEach((x) => {
      (m[x.domain] ||= []).push(x);
    });
    return domains.map((d) => ({
      id: d.id,
      tag: d.tag,
      nodes: (m[d.id] || []).map((x) => ({
        id: x.id,
        title: x.title,
        kind: x.kind,
        encrypted: x.encrypted,
        children: [],
      })),
    }));
  }, [cat.data, modules, domains]);
  const titles = useMemo(() => titleMap(branches), [branches]);

  function hydrate(u: User, product: string, tree: CatBranch[]) {
    const perms = u.module_perms || {};
    setAll(perms[productAll(product)] === "write" || perms["*"] === "write" || perms.all === "write");
    const d = new Set<string>();
    const c = new Set<string>();
    tree.forEach((b) => {
      if (perms[domainAllKey(product, b.id)] === "write") d.add(b.id);
    });
    walkIds(tree.flatMap((b) => b.nodes)).forEach((id) => {
      if (perms[id] === "write") c.add(id);
    });
    setDomainAlls(d);
    setChecked(c);
    setQ("");
    setOpen({});
  }

  useEffect(() => {
    if (!selected) return;
    hydrate(selected, catProduct, branches);
  }, [catProduct, cat.data, selected?.username, selected?.module_perms]);

  function pick(u: User) {
    setSel(u.username);
    setRole(u.role);
    setDisp(u.name || "");
    setProds(u.products || []);
    hydrate(u, catProduct, branches);
  }

  function onCatChange(p: string) {
    setCatProduct(p);
  }

  function toggleNode(n: CatNode, on: boolean) {
    const next = new Set(checked);
    const ids = [n.id, ...descendants(n)];
    if (on) {
      next.add(n.id);
      ids.slice(1).forEach((id) => next.delete(id));
    } else {
      ids.forEach((id) => next.delete(id));
    }
    setChecked(next);
  }

  function buildPerms(): Perms {
    const keep: Perms = {};
    const shown = new Set(walkIds(branches.flatMap((b) => b.nodes)));
    Object.entries(selected?.module_perms || {}).forEach(([k, v]) => {
      if (k === "*" || k === "all" || k === "ALL") return;
      if (k === productAll(catProduct)) return;
      if (isDomainAll(k, catProduct)) return;
      if (k.endsWith(":*")) {
        keep[k] = v;
        return;
      }
      if (!shown.has(k)) keep[k] = v;
    });
    if (all) keep[productAll(catProduct)] = "write";
    else {
      domainAlls.forEach((d) => {
        keep[domainAllKey(catProduct, d)] = "write";
      });
      const explicit = pruneImplied(checked, branches);
      const domainOfId = new Map<string, string>();
      branches.forEach((b) => walkIds(b.nodes).forEach((id) => domainOfId.set(id, b.id)));
      explicit.forEach((id) => {
        const dom = domainOfId.get(id);
        if (dom && domainAlls.has(dom)) return;
        keep[id] = "write";
      });
    }
    return keep;
  }

  function onSave() {
    if (!selected) return;
    save.mutate({
      username: selected.username,
      display_name: disp,
      role,
      products: prods,
      module_perms: buildPerms(),
    });
  }

  function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const username = String(fd.get("username") || "").trim();
    const password = String(fd.get("password") || "");
    save.mutate({
      username,
      password,
      display_name: String(fd.get("display_name") || "").trim() || undefined,
      role: String(fd.get("role") || "engineer"),
      products: ["usp"],
      module_perms: {},
    });
    e.currentTarget.reset();
  }

  const picked = [...pruneImplied(checked, branches)].filter((id) => titles.has(id));
  const needle = q.trim();

  if (me.isLoading) return <p>…</p>;
  if (!allowed) return <p className="err">无权管理用户</p>;
  if (users.isLoading) return <p>…</p>;
  if (users.isError) return <p className="err">无权管理用户</p>;
  return (
    <>
      <div className="page-head">
        <h1>用户权限</h1>
      </div>
      <form className="panel row" style={{ marginBottom: 16 }} onSubmit={onCreate}>
        <input name="username" placeholder="用户名" required style={{ width: 140 }} />
        <input name="display_name" placeholder="姓名" style={{ width: 100 }} />
        <input name="password" type="password" placeholder="密码 ≥8" minLength={8} required style={{ width: 140 }} />
        <select name="role" defaultValue="engineer">
          {ROLES.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
        <button type="submit" className="btn primary">
          新建
        </button>
      </form>
      <div className="admin-layout">
        <section className="panel">
          <ul className="user-list">
            {list.map((u) => (
              <li key={u.username} className={u.username === sel ? "active" : ""}>
                <button type="button" onClick={() => pick(u)}>
                  <span className="name">{u.name || u.username}</span>
                  <span className="meta">
                    {u.username} · {u.role_label} · {(u.products || []).join(", ") || "—"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          {!selected ? (
            <p className="empty">选择左侧用户</p>
          ) : (
            <>
              <p className="muted" style={{ marginTop: 0 }}>
                {selected.username}
              </p>
              <label className="field">
                <p>姓名</p>
                <input value={disp} onChange={(e) => setDisp(e.target.value)} />
              </label>
              <label className="field">
                <p>角色</p>
                <select value={role} onChange={(e) => setRole(e.target.value)}>
                  {ROLES.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.label}
                    </option>
                  ))}
                </select>
              </label>
              <p className="field">可访问产品</p>
              <div className="chips">
                {["usp", "ors"].map((p) => (
                  <label key={p} className="chip">
                    <input
                      type="checkbox"
                      checked={prods.includes(p)}
                      onChange={(e) => setProds(e.target.checked ? [...prods, p] : prods.filter((x) => x !== p))}
                    />
                    {p.toUpperCase()}
                  </label>
                ))}
              </div>
              <div className="perm-bar">
                <div className="chips">
                  {["usp", "ors"].map((p) => (
                    <button
                      key={p}
                      type="button"
                      className={`chip-btn${catProduct === p ? " is-on" : ""}`}
                      onClick={() => onCatChange(p)}
                    >
                      {p.toUpperCase()}
                    </button>
                  ))}
                </div>
                <label className="chip">
                  <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
                  全部
                </label>
              </div>
              {!all && picked.length ? (
                <div className="perm-picked">
                  {picked.map((id) => (
                    <button
                      key={id}
                      type="button"
                      className="perm-chip"
                      onClick={() => {
                        const next = new Set(checked);
                        next.delete(id);
                        setChecked(next);
                      }}
                    >
                      {titles.get(id) || id}
                      <span aria-hidden="true">×</span>
                    </button>
                  ))}
                </div>
              ) : null}
              {!all ? (
                <input
                  className="perm-search"
                  type="search"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索"
                  autoComplete="off"
                />
              ) : null}
              <div className="mod-scroll">
                {all ? (
                  <p className="mod-empty">该产品全部可写</p>
                ) : (
                  branches.map((d) => {
                    const nodes = (d.nodes || []).filter((n) => nodeMatch(n, needle));
                    if (needle && !nodes.length && !domainAlls.has(d.id)) return null;
                    return (
                      <div key={d.id} className="mod-domain">
                        <div className="mod-domain-h">
                          {d.tag}
                          <label className="chip">
                            <input
                              type="checkbox"
                              checked={domainAlls.has(d.id)}
                              onChange={(e) => {
                                const next = new Set(domainAlls);
                                if (e.target.checked) next.add(d.id);
                                else next.delete(d.id);
                                setDomainAlls(next);
                              }}
                            />
                            全部
                          </label>
                        </div>
                        {domainAlls.has(d.id) ? (
                          <p className="mod-empty">该领域全部可写</p>
                        ) : (
                          nodes.map((n) => (
                            <PermNode
                              key={n.id}
                              n={n}
                              q={needle}
                              depth={0}
                              ancestors={[]}
                              all={all}
                              domainOn={false}
                              checked={checked}
                              open={open}
                              setOpen={(id, v) => setOpen((s) => ({ ...s, [id]: v }))}
                              onToggle={toggleNode}
                            />
                          ))
                        )}
                      </div>
                    );
                  })
                )}
              </div>
              <p className="row" style={{ marginTop: 12 }}>
                <button type="button" className="btn primary" onClick={onSave} disabled={save.isPending}>
                  保存
                </button>
                {save.isError ? <span className="err">{save.error instanceof ApiError ? save.error.detail : "失败"}</span> : null}
                {save.isSuccess ? <span className="muted">已保存</span> : null}
              </p>
            </>
          )}
        </section>
      </div>
    </>
  );
}
