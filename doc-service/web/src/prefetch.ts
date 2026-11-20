import type { QueryClient } from "@tanstack/react-query";
import { api } from "./api";

const STALE = 20_000;

export function prefetchModule(qc: QueryClient, product: string, id: string) {
  if (!product || !id) return;
  const mid = encodeURIComponent(id);
  void qc.prefetchQuery({
    queryKey: ["module", product, id, ""],
    queryFn: () => api(`/api/v1/products/${product}/modules/${mid}`),
    staleTime: STALE,
  });
  void qc.prefetchQuery({
    queryKey: ["nested-graph", product, id],
    queryFn: () => api(`/api/v1/products/${product}/modules/${mid}/nested-graph`),
    staleTime: STALE,
  });
  void qc.prefetchQuery({
    queryKey: ["mod-links", product, id],
    queryFn: () => api(`/api/v1/products/${product}/features/${mid}/requirements`),
    staleTime: STALE,
  });
}

export function cachedModuleTitle(qc: QueryClient, product: string, id: string): string | undefined {
  const page = qc.getQueryData<{ title?: string }>(["module", product, id, ""]);
  if (page?.title) return page.title;
  for (const key of ["graph", "nested-graph"] as const) {
    const hits = qc.getQueriesData<{ nodes?: { id: string; title?: string }[] }>({ queryKey: [key, product] });
    for (const [, g] of hits) {
      const n = g?.nodes?.find((x) => x.id === id);
      if (n?.title) return n.title;
    }
  }
  return undefined;
}
