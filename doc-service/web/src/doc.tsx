import { useLayoutEffect, useRef, type MouseEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import renderMathInElement from "katex/dist/contrib/auto-render.mjs";
import "katex/dist/katex.min.css";
import { FigEditor } from "./fig-editor";

const MATH_OPTS = {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "\\[", right: "\\]", display: true },
    { left: "$", right: "$", display: false },
    { left: "\\(", right: "\\)", display: false },
  ],
  throwOnError: false,
  ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "svg", "img"],
};

const MATH_SKIP_CHARS = 250_000;

const SKIP_PAGES = new Set([
  "account",
  "admin-users",
  "editor",
  "guide",
  "hours",
  "hours-history",
  "index",
  "login",
  "ors",
  "portal",
  "product-versions",
  "req",
  "updates",
  "usp",
  "why",
  "drafts",
]);

const MOD_FILE =
  /^(?:(\d+[a-z]?-(?:[A-Za-z][A-Za-z0-9_]*)(?:-[A-Za-z][A-Za-z0-9_]*)*)|([A-Za-z][A-Za-z0-9_]*))(?:-(v\d+(?:\.\d+)*))?(\.html|\.md)$/i;

const FILE_ALIAS: Record<string, string> = {
  sv: "sv_plan",
  mv: "mv_plan",
  mapf: "mv_cent",
  biz_assign: "biz",
  assign: "school_as",
  env: "env_ext",
  behavior: "beh_ext",
  assign_prio: "beh_assign_prio",
  path_prio: "beh_path_prio",
  task_dep: "beh_task_dep",
};

function resolveStem(stem: string): string {
  const raw = stem.trim();
  if (/^\d+[a-z]?-/i.test(raw)) {
    const slug = raw.replace(/^\d+[a-z]?-/i, "").replace(/-/g, "_");
    return FILE_ALIAS[slug] || slug;
  }
  return raw;
}

function spaDocHref(href: string, product: string): string | null {
  const raw = href.trim();
  if (!raw || raw.startsWith("#") || /^(mailto:|javascript:|data:|blob:)/i.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(raw, window.location.href);
  } catch {
    return null;
  }
  if (/^https?:$/i.test(url.protocol)) {
    if (url.host !== window.location.host && !/:8766$/i.test(url.host) && url.hostname !== "10.10.10.202") {
      return null;
    }
  }
  const file = decodeURIComponent(url.pathname.split("/").pop() || "");
  const reqId = url.searchParams.get("id");
  if (/^req\.(html|md)$/i.test(file) && reqId) {
    return `/${product}/requirements/${reqId}${url.hash}`;
  }
  const hit = file.match(MOD_FILE);
  if (hit && !SKIP_PAGES.has((hit[1] || hit[2] || "").toLowerCase())) {
    const stem = hit[1] ? resolveStem(hit[1]) : hit[2];
    const ver = hit[3];
    const destProd = /^ors/i.test(stem) ? "ors" : /^usp/i.test(stem) && stem.toLowerCase() !== "usp" ? "usp" : product;
    const q = ver ? `?v=${ver}` : "";
    return `/${destProd}/m/${stem}${q}${url.hash}`;
  }
  if (/^\/(usp|ors)(\/|$)/.test(url.pathname) && !/\.(html|md)$/i.test(file)) {
    return `${url.pathname}${url.search}${url.hash}`;
  }
  return null;
}

export function DocHtml({
  html,
  className = "doc",
  canEdit,
  markdown,
  onSave,
  saving,
  saveError,
}: {
  html: string;
  className?: string;
  canEdit?: boolean;
  markdown?: string;
  onSave?: (markdown: string) => void;
  saving?: boolean;
  saveError?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const nav = useNavigate();
  const { product = "usp" } = useParams();
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if ((html || "").length > MATH_SKIP_CHARS) return;
    try {
      renderMathInElement(el, MATH_OPTS);
    } catch {
      /* keep raw html */
    }
  }, [html]);
  function onClick(e: MouseEvent<HTMLDivElement>) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest("a");
    if (!a || (a.target && a.target !== "_self")) return;
    const href = a.getAttribute("href");
    if (!href) return;
    const dest = spaDocHref(href, product);
    if (!dest) return;
    e.preventDefault();
    nav(dest);
  }
  return (
    <>
      <div className={className} ref={ref} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
      {canEdit && markdown && onSave ? (
        <FigEditor
          hostRef={ref}
          html={html}
          markdown={markdown}
          onSave={onSave}
          saving={saving}
          error={saveError}
        />
      ) : null}
    </>
  );
}
