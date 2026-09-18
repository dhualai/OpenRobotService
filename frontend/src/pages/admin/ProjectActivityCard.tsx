// 项目动态卡 —— 对照原型 components/project/ProjectActivityCard.tsx 的「关注节点变动」分组：
// 节点在「项目信息管理」卡被星标关注后，它的最新一条变动展示在这里。
// 按需求只展示变动内容本身：不带修改时间、不带操作人员；每个被关注节点只留最新一条。
//
// 数据源：GET /api/admin/info-nodes/projects/{id}/activity（后端把「关注标注」
// 与「节点操作记录」聚合好，见 info_node_mark_service.marked_activity）。
import { useEffect, useState } from 'react';
import { MacStar } from '@/shared/components/macaronIcons';
import { loadProjectActivity, type ProjectActivityItem } from '@/shared/utils/projectInfoTree';

export default function ProjectActivityCard({ projectId, reloadToken = 0 }: {
  projectId: string;
  /** 外层（信息卡星标变化）翻动它触发重新拉取 */
  reloadToken?: number;
}) {
  const [items, setItems] = useState<ProjectActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [retryToken, setRetryToken] = useState(0);

  // 从编辑页返回（组件重新挂载）、切换项目、星标变化与手动重试时重新拉取
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    loadProjectActivity(projectId)
      .then((data) => { if (!cancelled) setItems(data); })
      .catch(() => { if (!cancelled) { setItems([]); setLoadError(true); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, reloadToken, retryToken]);

  return (
    <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
      <h3 className="mac-card-title">项目动态</h3>
      <div className="mac-activity__head">
        <MacStar size={14} />关注节点变动
      </div>
      {loading ? (
        <div className="mac-info__state">正在加载项目动态…</div>
      ) : loadError ? (
        <div className="mac-info__state">
          项目动态加载失败
          <div className="mac-info__state-sub">请检查网络后重试</div>
          <button
            type="button"
            className="mac-btn mac-btn--outline"
            style={{ marginTop: 12 }}
            onClick={() => setRetryToken((value) => value + 1)}
          >
            重新加载
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="mac-activity__empty">
          暂无变动。在「项目信息管理」中点子节点右侧的星标，即可关注该节点的后续变动。
        </div>
      ) : (
        <ul className="mac-activity__list">
          {items.map((item) => (
            <li key={item.node_id} className="mac-activity__item">
              <span className="mac-activity__node">
                {item.root_title && item.root_title !== item.node_title ? `${item.root_title} · ` : ''}{item.node_title}
              </span>
              <span className="mac-activity__text">{item.detail || '（无变动描述）'}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
