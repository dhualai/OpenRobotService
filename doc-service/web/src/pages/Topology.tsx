import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, type Me } from "../api";
import { prefetchModule } from "../prefetch";
import { ChartStage, ExpandEditLink, TopologyChart, type ChartGraph } from "../chart";
import { defaultBoard, domainIdOf, domainsFor, editorPath, moduleHref, type DomainBoard } from "../boards";
import { ReqListRow, ReqListTools, ReqPager, useReqView, type ReqListItem } from "../req";

type Graph = ChartGraph & { domains?: DomainBoard[]; elided?: number };
type ReqList = { items: ReqListItem[] };
type Snake = { features?: Record<string, { duration: number | null }> };

export function TopologyPage() {
  const { product = "usp" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const board = sp.get("board") || defaultBoard(product);
  const [help, setHelp] = useState(false);
  const [testErr, setTestErr] = useState("");
  const view = sp.get("tab") === "reqs" ? "reqs" : "features";
  const tag = sp.get("tag") === "project" ? "project" : "product";
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/me"), staleTime: 0 });
  const graph = useQuery({
    queryKey: ["graph", product, board],
    queryFn: () => api<Graph>(`/api/v1/products/${product}/graph?board=${encodeURIComponent(board)}`),
  });
  const reqs = useQuery({
    queryKey: ["reqs", product],
    queryFn: () => api<ReqList>(`/api/v1/products/${product}/requirements`),
    enabled: view === "reqs",
  });
  const snake = useQuery({
    queryKey: ["hours-snake", product],
    queryFn: () => api<Snake>(`/api/v1/products/${product}/hours/summary`),
    enabled: view === "features",
  });
  useEffect(() => {
    api(`/api/v1/products/${product}/visits`, { method: "POST" }).catch(() => {});
  }, [product]);
  const tagged = (reqs.data?.items || []).filter((it) => (it.tag || "product") === tag);
  const reqView = useReqView(tagged);
  if (graph.isLoading) return <p>…</p>;
  if (graph.isError) return <p className="err">无法加载拓扑</p>;
  const g = graph.data!;
  const domains = domainsFor(product, g.domains);
  const domain = domainIdOf(product, g.board || board, domains);
  const isDomain = domains.some((d) => d.rel_path === (g.board || board));
  const empty = !g.nodes.length;
  const canBoard = Boolean(g.can_edit) && !me.data?.migrated_url;
  const editorHref = editorPath(product, board === defaultBoard(product) ? "" : board);
  const root = defaultBoard(product);
  function patchSp(fn: (n: URLSearchParams) => void) {
    const n = new URLSearchParams(sp);
    fn(n);
    setSp(n, { replace: true });
  }
  function setView(next: "features" | "reqs") {
    patchSp((n) => {
      if (next === "reqs") n.set("tab", "reqs");
      else {
        n.delete("tab");
        n.delete("tag");
      }
    });
  }
  function setReqTag(next: "product" | "project") {
    patchSp((n) => {
      n.set("tab", "reqs");
      if (next === "project") n.set("tag", "project");
      else n.delete("tag");
    });
  }
  return (
    <div className="domain-surface">
      <div className="topo-surface">
        <div className="topo-rail" role="tablist">
          <button type="button" className={view === "features" ? "active" : ""} onClick={() => setView("features")}>
            功能
          </button>
          <button type="button" className={view === "reqs" ? "active" : ""} onClick={() => setView("reqs")}>
            需求
          </button>
        </div>
        <div className="topo-main">
          {view === "reqs" ? (
            <div className="req-panel">
              <div className="req-bar">
                <div className="req-tag-tabs" role="tablist">
                  <button type="button" className={`tag-product${tag === "product" ? " is-active" : ""}`} onClick={() => setReqTag("product")}>
                    产品
                  </button>
                  <button type="button" className={`tag-project${tag === "project" ? " is-active" : ""}`} onClick={() => setReqTag("project")}>
                    项目
                  </button>
                </div>
                <ReqListTools
                  needle={reqView.needle}
                  sort={reqView.sort}
                  dir={reqView.dir}
                  onNeedle={reqView.setNeedle}
                  onSort={reqView.setSort}
                  onDir={reqView.setDir}
                />
              </div>
              <div className="req-list-wrap">
                <ul className="req-list">
                  {reqView.rows.map((r) => (
                    <ReqListRow key={r.id} product={product} item={r} />
                  ))}
                </ul>
                {!reqView.total ? <p className="empty">{reqView.needle.trim() ? "无匹配" : "无需求"}</p> : null}
              </div>
              <ReqPager page={reqView.page} pages={reqView.pages} total={reqView.total} onPage={reqView.setPage} />
            </div>
          ) : (
            <div className="topo-view" data-view="features">
              <div className="feature-domain-bar">
                <select
                  aria-label="功能领域"
                  value={domain}
                  onChange={(e) => {
                    const d = domains.find((x) => x.id === e.target.value);
                    if (!d) return;
                    nav(d.rel_path === root ? `/${product}` : `/${product}?board=${encodeURIComponent(d.rel_path)}`);
                  }}
                >
                  {!domain ? <option value="">子图</option> : null}
                  {domains.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.title}
                    </option>
                  ))}
                </select>
                {!isDomain ? (
                  <button type="button" className="user-link" onClick={() => nav(`/${product}`)}>
                    根图
                  </button>
                ) : null}
              </div>
              {empty ? (
                <div className="chart-panel">
                  <p className="empty chart-empty">{g.elided ? "没有可显示的节点" : "空图"}</p>
                  {canBoard ? <ExpandEditLink to={editorHref} /> : null}
                </div>
              ) : (
                <ChartStage
                  fitKey={`${product}:${g.board}`}
                  extra={canBoard ? <ExpandEditLink to={editorHref} /> : null}
                >
                  <TopologyChart
                    graph={g}
                    snakeDurations={Object.fromEntries(
                      Object.entries(snake.data?.features || {}).map(([k, v]) => [k, v.duration]),
                    )}
                    onNodeClick={(n) => nav(moduleHref(product, n.id, board))}
                    onGroupClick={(gid) => nav(moduleHref(product, gid, board))}
                    onPrefetch={(id) => prefetchModule(qc, product, id)}
                    onNested={(n) => n.nested_board && nav(`/${product}?board=${encodeURIComponent(n.nested_board)}`)}
                    onMetricsClick={(id) => nav(moduleHref(product, id, board))}
                    onTestClick={async () => {
                      setTestErr("");
                      const w = window.open("about:blank", "_blank");
                      if (w) w.opener = null;
                      try {
                        const j = await api<{ url: string }>(`/api/v1/products/${product}/test-sso`, { method: "POST" });
                        if (w) w.location.replace(j.url);
                        else window.location.assign(j.url);
                      } catch (e) {
                        w?.close();
                        setTestErr(e instanceof ApiError ? e.detail : "无法打开自测系统");
                      }
                    }}
                  />
                </ChartStage>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="chart-footer">
        {testErr ? <p className="err">{testErr}</p> : null}
        <div className="help-wrap">
          <button type="button" className="guide-link" onClick={() => setHelp(!help)}>
            【读图指南】
          </button>
          {help ? (
            <div className="help-pop" role="dialog">
              <p className="help-title">读图指南</p>
              <ul>
                <li>
                  节点色带：
                  <i className="lc-sample pre" />需求
                  <i className="lc-sample dev" />开发
                  <i className="lc-sample test" />测试
                  <i className="lc-sample done" />完成
                </li>
                <li>进行中边框绕圈；完成满环</li>
                <li>▸ 下层拓扑 · −/+ 折叠包络</li>
                <li>产品名旁「指标」进入该产品评价指标；「TEST」进入自测系统</li>
                <li>滚轮缩放，拖动画布平移</li>
              </ul>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
