export type DomainBoard = { id: string; title: string; rel_path: string };

const USP: DomainBoard[] = [
  { id: "algo", title: "算法", rel_path: "board.json" },
  { id: "backend", title: "后端", rel_path: "boards/usp_backend.json" },
  { id: "frontend", title: "前端", rel_path: "boards/usp_frontend.json" },
];

const ORS: DomainBoard[] = [
  { id: "algo", title: "算法", rel_path: "boards/ors.json" },
  { id: "backend", title: "后端", rel_path: "boards/ors_backend.json" },
  { id: "frontend", title: "前端", rel_path: "boards/ors_frontend.json" },
];

export function defaultBoard(product: string): string {
  return product === "ors" ? "boards/ors.json" : "board.json";
}

export function domainsFor(product: string, fromApi?: DomainBoard[] | null): DomainBoard[] {
  if (fromApi && fromApi.length) return fromApi;
  return product === "ors" ? ORS : USP;
}

export function domainIdOf(product: string, board: string, fromApi?: DomainBoard[] | null): string {
  const hit = domainsFor(product, fromApi).find((d) => d.rel_path === board);
  return hit?.id || "";
}

export function editorPath(product: string, board?: string | null, group?: string | null): string {
  const qs = new URLSearchParams();
  if (board) qs.set("board", board);
  if (group) qs.set("group", group);
  const q = qs.toString();
  return `/${product}/editor${q ? `?${q}` : ""}`;
}

export function peerPath(target: string, pathname: string): string {
  const rest = pathname.replace(/^\/(usp|ors)/, "") || "";
  if (!rest || rest.startsWith("/m/")) return `/${target}`;
  if (rest.startsWith("/hours")) return `/${target}/hours/history`;
  const seg = rest.split("/").filter(Boolean)[0];
  const keep = new Set(["requirements", "releases", "editor", "audit", "account", "users", "drafts"]);
  if (keep.has(seg)) return `/${target}/${seg}`;
  return `/${target}`;
}

export function peerHome(target: string, search: string): string {
  return new URLSearchParams(search).get("tab") === "reqs" ? `/${target}?tab=reqs` : `/${target}`;
}

export function homeHref(product: string, board?: string | null): string {
  const rel = (board || "").trim();
  if (!rel || rel === defaultBoard(product)) return `/${product}`;
  return `/${product}?board=${encodeURIComponent(rel)}`;
}

export function moduleHref(product: string, id: string, board?: string | null): string {
  const path = `/${product}/m/${encodeURIComponent(id)}`;
  const home = homeHref(product, board);
  const q = home.includes("?") ? home.slice(home.indexOf("?")) : "";
  return `${path}${q}`;
}

export function upHref(product: string, pathname: string, search = ""): string {
  const parts = pathname.split("/").filter(Boolean);
  const kind = parts[1];
  const sp = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  if (kind === "requirements") {
    const tag = sp.get("tag");
    return tag === "project" ? `/${product}?tab=reqs&tag=project` : `/${product}?tab=reqs`;
  }
  if (kind === "drafts") {
    return parts.length >= 3 ? `/${product}/drafts` : `/${product}`;
  }
  if (kind === "m") return homeHref(product, sp.get("board"));
  return `/${product}`;
}
