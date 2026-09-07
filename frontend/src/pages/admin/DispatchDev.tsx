// 派单开发者模式：看问题簇、重建簇、一键补索引、看历史工单。
// 入口在「其他」，权限 frontend:admin:dispatch-dev:show（admin 直通仍可见）。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import ReactECharts from '@/shared/components/ReactECharts';

export const PERM_DISPATCH_DEV = 'frontend:admin:dispatch-dev:show';

interface ClusterPerson { engineer_id: string; name: string; count: number }
interface ClusterTicket { ticket_id: string; title: string; engineer_id: string; engineer_name?: string }
interface Cluster {
  id: number;
  titles: string[];
  ticket_count: number;
  people: ClusterPerson[];
  tickets: ClusterTicket[];
}
interface ClusterPoint {
  ticket_id: string;
  title: string;
  engineer_id: string;
  engineer_name?: string;
  cluster_id: number;
  x: number;
  y: number;
}
interface ClusterSnap {
  ready: boolean;
  ticket_total: number;
  clustered: number;
  noise: number;
  clusters: Cluster[];
  points?: ClusterPoint[];
  params?: Record<string, number | string | undefined>;
}
interface HistoryTicket {
  ticket_id: string;
  title: string;
  engineer_id: string;
  engineer_name: string;
  task_type: string;
  robot_type: string;
  fault_code: string;
  closed_at: string;
}
interface HistorySnap {
  mysql_total: number;
  qdrant_collection: string;
  qdrant_points: number;
  tickets: HistoryTicket[];
  params?: Record<string, number | string | undefined>;
}
interface Overview { clusters: ClusterSnap; history: HistorySnap }
interface ReindexResult { total: number; indexed: number; skipped: number; collection: string }

const CLUSTER_COLORS = [
  '#227197',
  '#e37318',
  '#2ba471',
  '#7b61ff',
  '#d4537e',
  '#c9a227',
  '#0f9d91',
  '#c45c26',
];
const NOISE_COLOR = '#c9d4d9';

function clusterColor(id: number): string {
  if (id < 0) return NOISE_COLOR;
  return CLUSTER_COLORS[id % CLUSTER_COLORS.length];
}

const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');

function unwrap<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && 'data' in raw) {
    return (raw as { data: T }).data;
  }
  return raw as T;
}

export default function DispatchDev() {
  const navigate = useNavigate();
  const allowed = useAuthStore((s) => s.hasPermission(PERM_DISPATCH_DEV));
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [clusters, setClusters] = useState<ClusterSnap | null>(null);
  const [history, setHistory] = useState<HistorySnap | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [indexHint, setIndexHint] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = unwrap<Overview>(await request('/dispatch-dev/overview', { skipCache: true }));
      setClusters(data.clusters);
      setHistory(data.history);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!allowed) {
      navigate('/admin/entries', { replace: true });
      return;
    }
    load();
  }, [allowed, load, navigate]);

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const data = unwrap<ClusterSnap>(await request('/dispatch-dev/clusters/rebuild', {
        method: 'POST',
        timeout: 180000,
      }));
      setClusters(data);
      Toast({ message: data.ready ? `已重建 ${data.clusters.length} 个簇` : '已重建，当前没有簇', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '重建失败', theme: 'error' });
    } finally {
      setRebuilding(false);
    }
  };

  const reindex = async () => {
    setReindexing(true);
    setIndexHint('');
    try {
      const data = unwrap<{ index: ReindexResult; history: HistorySnap }>(
        await request('/dispatch-dev/history/reindex', { method: 'POST', timeout: 600000 }),
      );
      setHistory(data.history);
      const ix = data.index || { total: 0, indexed: 0, skipped: 0, collection: '' };
      setIndexHint(`写入 ${ix.indexed} / ${ix.total}，失败 ${ix.skipped}，集合 ${ix.collection || '无'}`);
      Toast({ message: '补索引完成', theme: 'success' });
    } catch (e) {
      Toast({ message: e instanceof Error ? e.message : '补索引失败', theme: 'error' });
    } finally {
      setReindexing(false);
    }
  };

  const scatterOption = useMemo(() => {
    const pts = clusters?.points || [];
    const clusterList = clusters?.clusters || [];
    const series = clusterList.map((c) => ({
      name: `簇${c.id + 1}`,
      type: 'scatter' as const,
      symbolSize: 14,
      itemStyle: { color: clusterColor(c.id) },
      data: pts.filter((p) => p.cluster_id === c.id).map((p) => ({
        value: [p.x, p.y],
        cluster_id: p.cluster_id,
        title: p.title,
        person: p.engineer_name || p.engineer_id,
      })),
    }));
    const noise = pts.filter((p) => p.cluster_id < 0);
    if (noise.length) {
      series.push({
        name: '未进簇',
        type: 'scatter',
        symbolSize: 10,
        itemStyle: { color: NOISE_COLOR },
        data: noise.map((p) => ({
          value: [p.x, p.y],
          cluster_id: -1,
          title: p.title,
          person: p.engineer_name || p.engineer_id,
        })),
      });
    }
    return {
      color: CLUSTER_COLORS,
      tooltip: {
        trigger: 'item',
        formatter: (item: { data?: { title?: string; person?: string }; seriesName?: string }) => {
          const d = item?.data || {};
          return `${item?.seriesName || ''}<br/>${d.title || '（无标题）'}<br/>${d.person || ''}`;
        },
      },
      legend: {
        type: 'scroll',
        top: 0,
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: '#888d8f', fontSize: 11 },
      },
      grid: { left: 12, right: 12, top: 32, bottom: 8, containLabel: true },
      xAxis: { type: 'value', min: -1.15, max: 1.15, splitLine: { lineStyle: { color: '#f1f4f4' } }, axisLabel: { show: false } },
      yAxis: { type: 'value', min: -1.15, max: 1.15, splitLine: { lineStyle: { color: '#f1f4f4' } }, axisLabel: { show: false } },
      series,
    };
  }, [clusters]);

  if (!allowed) return null;

  const params = clusters?.params || history?.params || {};
  const hasScatter = (clusters?.points || []).length > 0;

  return (
    <div className="dispatch-dev">
      {loading ? (
        <div className="dispatch-dev__empty"><Loading text="加载中..." /></div>
      ) : error ? (
        <div className="dispatch-dev__empty">{error}</div>
      ) : (
        <>
          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">历史工单（A 路索引）</span>
              <button type="button" className="dispatch-dev__btn" disabled={reindexing} onClick={reindex}>
                {reindexing ? '补索引中…' : '一键补索引'}
              </button>
            </div>
            <p className="dispatch-dev__hint">
              已解决 / 已关闭且有处理人的单会写入 Qdrant，供相似工单召回。工单多时要等一会儿。
            </p>
            <div className="dispatch-dev__stats">
              <span>库里 {history?.mysql_total ?? 0} 张</span>
              <span>已索引 {history?.qdrant_points ?? 0} 条</span>
              <span>集合 {history?.qdrant_collection || '尚未创建'}</span>
            </div>
            {indexHint ? <p className="dispatch-dev__hint">{indexHint}</p> : null}
            <ul className="dispatch-dev__list">
              {(history?.tickets || []).length === 0 ? (
                <li className="dispatch-dev__empty-row">暂无已解决 / 已关闭工单</li>
              ) : (
                (history?.tickets || []).map((t) => (
                  <li key={t.ticket_id || t.title}>
                    <strong>{t.title || '（无标题）'}</strong>
                    <em>{t.engineer_name || t.engineer_id || '无人'}</em>
                    <span>
                      {[t.task_type, t.robot_type, t.fault_code].filter(Boolean).join(' · ') || '—'}
                    </span>
                  </li>
                ))
              )}
            </ul>
          </section>

          <section className="dispatch-dev__card">
            <div className="dispatch-dev__head">
              <span className="dispatch-dev__title">问题簇（B 路）</span>
              <button type="button" className="dispatch-dev__btn" disabled={rebuilding} onClick={rebuild}>
                {rebuilding ? '重建中…' : '重建簇'}
              </button>
            </div>
            <p className="dispatch-dev__hint">
              最近 {params.cluster_window ?? 500} 张历史单自动聚簇。合并阈值 {params.cluster_merge ?? '—'}，
              最小团 {params.cluster_min_size ?? '—'}。进程刚起来、还没重建时这里是空的。
            </p>
            <div className="dispatch-dev__stats">
              <span>簇 {clusters?.clusters.length ?? 0} 个</span>
              <span>进簇 {clusters?.clustered ?? 0}</span>
              <span>未进簇 {clusters?.noise ?? 0}</span>
              <span>窗口 {clusters?.ticket_total ?? 0}</span>
            </div>
            <div className="dispatch-dev__chart">
              <span className="dispatch-dev__sub">向量分布（PCA 二维，轴无业务含义）</span>
              {hasScatter ? (
                <ReactECharts
                  option={scatterOption}
                  style={{ height: 300 }}
                  notMerge
                  onEvents={{
                    click: (item: { data?: { cluster_id?: number } }) => {
                      const cid = item?.data?.cluster_id;
                      if (typeof cid === 'number' && cid >= 0) setOpenId(cid);
                    },
                  }}
                />
              ) : (
                <div className="dispatch-dev__empty-row">再点一次「重建簇」后才能看到分布图</div>
              )}
            </div>
            {(clusters?.clusters || []).length === 0 ? (
              <div className="dispatch-dev__empty-row">
                {clusters?.ready ? '历史单不够，没聚出簇' : '还没有簇缓存，点「重建簇」'}
              </div>
            ) : (
              (clusters?.clusters || []).map((c) => {
                const open = openId === c.id;
                return (
                  <div key={c.id} className="dispatch-dev__cluster" style={{ borderColor: clusterColor(c.id) }}>
                    <button type="button" className="dispatch-dev__cluster-head" onClick={() => setOpenId(open ? null : c.id)}>
                      <span className="dispatch-dev__cluster-title">
                        <i className="dispatch-dev__dot" style={{ background: clusterColor(c.id) }} />
                        #{c.id + 1} {c.titles.slice(0, 2).join(' / ') || '（无标题）'}
                      </span>
                      <span className="dispatch-dev__cluster-meta">
                        {c.ticket_count} 张 · {c.people.map((p) => `${p.name}×${p.count}`).join('、') || '无人'}
                        {open ? ' ▴' : ' ▾'}
                      </span>
                    </button>
                    {open ? (
                      <ul className="dispatch-dev__list">
                        {c.tickets.map((t, i) => (
                          <li key={`${t.ticket_id}-${i}`}>
                            <strong>{t.title || '（无标题）'}</strong>
                            <em>{t.engineer_name || t.engineer_id || '无人'}</em>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                );
              })
            )}
          </section>
        </>
      )}
    </div>
  );
}
