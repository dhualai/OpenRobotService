// 项目信息管理卡（项目详细信息）—— 对照原型 components/project/ProjectDetailCard.tsx：
// 「显示内容」标签池 + 勾选筛选 + Markdown 文档式浏览态，右上角「编辑」跳转独立编辑页；
// 标签角标：感叹号=该标签下有信息未填写，红点=该标签下有未读的操作记录（与编辑页行内
// 红点同一套已读水位，看过「历史」即消）；标签下有对应图例说明两种角标；
// 子节点右侧的星标 = 关注该节点（其最新变动会展示在「项目动态」卡，对照原型同款交互）。
//
// 数据源：后端 /api/admin/info-nodes/*（经 shared/utils/projectInfoTree.ts 数据层），异步加载；
// 关注落到后端 project_info_node_mark（按登录人隔离，每人一份关注列表，服务端按 token 过滤）；
// 标签筛选/折叠是个人界面偏好，仍存本机。
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast } from 'tdesign-mobile-react';
import API_CONFIG from '@/config/api';
import { useAuthStore } from '@/stores/auth';
import {
  MacChevronDown, MacChevronUp, MacDownload, MacFileText, MacImage, MacPencil, MacStar,
} from '@/shared/components/macaronIcons';
import {
  computeInfoCompleteness,
  countInfoValues,
  formatFileSize,
  isInfoNodeVisible,
  loadCardCollapsed,
  loadHistoryLatest,
  loadHistorySeen,
  loadInfoNodeMarks,
  loadInfoNodes,
  loadSelectedTags,
  saveCardCollapsed,
  saveSelectedTags,
  toggleInfoNodeMark,
  unseenHistoryNodes,
  unseenHistoryRoots,
  type ProjectInfoFileValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';

export default function ProjectInfoCard({ projectId, canEdit, onMarkChange }: {
  projectId: string;
  canEdit: boolean;
  /** 关注状态变化后通知外层（「项目动态」卡刷新用） */
  onMarkChange?: () => void;
}) {
  const navigate = useNavigate();
  const username = useAuthStore((s) => s.username);
  const [nodes, setNodes] = useState<ProjectInfoNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);
  const [marked, setMarked] = useState<Set<string>>(() => new Set());
  const [latestByNode, setLatestByNode] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Set<string>>(() => loadSelectedTags(projectId));
  const [collapsed, setCollapsed] = useState<boolean>(() => loadCardCollapsed(projectId));

  // 信息树来自后端接口；切换项目、从编辑页返回（组件重新挂载）与手动重试时重新拉取
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    loadInfoNodes(projectId)
      .then((data) => { if (!cancelled) setNodes(data); })
      .catch(() => { if (!cancelled) { setNodes([]); setLoadError(true); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, reloadToken]);

  // 关注列表单独拉取：失败只影响星标显示（按未关注渲染），不挡正文
  useEffect(() => {
    let cancelled = false;
    loadInfoNodeMarks(projectId)
      .then((ids) => { if (!cancelled) setMarked(new Set(ids)); })
      .catch(() => { if (!cancelled) setMarked(new Set()); });
    return () => { cancelled = true; };
  }, [projectId, reloadToken]);

  // 各节点最新操作记录：只为标签池红点用（与编辑页同一份 summary 接口、同一套已读水位），
  // 失败静默——红点只是辅助提示，按「无红点」渲染，不打扰正文
  useEffect(() => {
    let cancelled = false;
    loadHistoryLatest(projectId)
      .then((latest) => { if (!cancelled) setLatestByNode(latest); })
      .catch(() => { if (!cancelled) setLatestByNode({}); });
    return () => { cancelled = true; };
  }, [projectId, reloadToken]);

  // 点星标：先乐观切换，后端确认后以其返回值为准；失败回滚并提示
  const toggleMark = useCallback(async (nodeId: string) => {
    const wasMarked = marked.has(nodeId);
    const flip = (value: boolean) => setMarked((prev) => {
      const next = new Set(prev);
      if (value) next.add(nodeId); else next.delete(nodeId);
      return next;
    });
    flip(!wasMarked);
    try {
      const nowMarked = await toggleInfoNodeMark(nodeId, projectId);
      flip(nowMarked);
      onMarkChange?.();
    } catch (err) {
      flip(wasMarked);
      Toast({ message: `关注操作失败: ${err instanceof Error ? err.message : ''}`, theme: 'error' });
    }
  }, [marked, onMarkChange, projectId]);

  useEffect(() => { setSelected(loadSelectedTags(projectId)); }, [projectId]);

  useEffect(() => { saveSelectedTags(projectId, selected); }, [projectId, selected]);

  const byParent = useMemo(() => {
    const map = new Map<string | null, ProjectInfoNode[]>();
    nodes.forEach((node) => {
      const siblings = map.get(node.parent_id) ?? [];
      siblings.push(node);
      map.set(node.parent_id, siblings);
    });
    map.forEach((items) => items.sort((a, b) => a.sort_order - b.sort_order));
    return map;
  }, [nodes]);

  const roots = byParent.get(null) ?? [];
  const visibleRoots = selected.size === 0 ? roots : roots.filter((node) => selected.has(node.id));
  const completeness = useMemo(() => computeInfoCompleteness(nodes), [nodes]);
  // 有值的节点数：整棵没值的分支在渲染时被裁掉，标签点开也只有空白
  const valueCounts = useMemo(() => countInfoValues(nodes), [nodes]);
  // 选中的标签整片没值（含未选中任何标签但整棵树都没写过的情形）→ 不渲染内容，改为提示补充
  const noSelectedValue =
    !loading && !loadError && visibleRoots.length > 0
    && visibleRoots.every((node) => (valueCounts.get(node.id) ?? 0) === 0);
  const emptyTagTitles = visibleRoots
    .filter((node) => (valueCounts.get(node.id) ?? 0) === 0)
    .map((node) => node.title)
    .join('、');

  // 标签池红点：该一级标签下（含标签自身）有本机没看过的操作记录。
  // 与编辑页行内红点共用水位（项目+登录用户存本机的已读记录 id）——
  // 在编辑页点开对应节点的「历史」推进水位后，回到本页红点即消失。
  const rootDotIds = useMemo(() => {
    const unseen = unseenHistoryNodes(latestByNode, loadHistorySeen(projectId, username));
    return unseenHistoryRoots(nodes, unseen);
  }, [nodes, latestByNode, projectId, username]);

  const toggleTag = (id: string) => setSelected((current) => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const toggleCard = () => setCollapsed((value) => {
    saveCardCollapsed(projectId, !value);
    return !value;
  });

  return (
    <section className="mac-card mac-card--pad" style={{ marginBottom: 12 }}>
      <div className="mac-info__head">
        <h3 className="mac-info__title">
          项目信息管理
        </h3>
        <div className="mac-info__actions">
          {canEdit && (
            <button
              type="button"
              className="mac-btn mac-btn--ghost mac-info__edit"
              onClick={() => navigate(`/admin/project-detail/${projectId}/edit`)}
            >
              <MacPencil size={13} />编辑
            </button>
          )}
          <button
            type="button"
            className="mac-btn mac-btn--ghost mac-info__collapse"
            onClick={toggleCard}
            aria-label={collapsed ? '展开' : '折叠'}
          >
            {collapsed ? <MacChevronDown size={16} /> : <MacChevronUp size={16} />}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="mac-info__poolhead">
          <span className="mac-info__poolhead-label">显示内容</span>
          <div className="mac-info__poolhead-ops">
            <button type="button" className="mac-info__poolbtn" onClick={() => setSelected(new Set(roots.map((node) => node.id)))}>全选</button>
            <button type="button" className="mac-info__poolbtn" onClick={() => setSelected(new Set())}>清空</button>
          </div>
        </div>
      )}

      {/* 标签池（一级节点）：选中即只看该标签及其全部子内容；不选显示全部（对照原型）。
          角标两类：感叹号=该标签下有信息未填写（常显），红点=有本机没看过的操作记录（看过「历史」即消） */}
      {roots.length > 0 && (
        <div className="mac-tagpool">
          {roots.map((node) => {
            const incomplete = completeness.get(node.id)?.incomplete;
            const hasDot = rootDotIds.has(node.id);
            return (
              <button
                key={node.id}
                type="button"
                className={`mac-tagpool__chip${selected.has(node.id) ? ' is-active' : ''}`}
                onClick={() => toggleTag(node.id)}
              >
                {node.title}
                {incomplete ? <span className="mac-tagpool__warn" aria-label="信息不全">!</span> : null}
                {hasDot ? (
                  <span className={`mac-tagpool__dot${incomplete ? ' mac-tagpool__dot--on-warn' : ''}`} aria-label="有新变动" />
                ) : null}
              </button>
            );
          })}
        </div>
      )}

      {!collapsed && (
        <>
          {roots.length > 0 && (
            <p className="mac-info__legend">
              <span className="mac-info__legend-item">
                <span className="mac-tagpool__warn mac-tagpool__warn--static" aria-hidden="true">!</span>
                标签下有信息未填写
              </span>
              <span className="mac-info__legend-item">
                <span className="mac-tagpool__dot mac-tagpool__dot--static" aria-hidden="true" />
                标签下有新变动，点开对应节点的「历史」后消失
              </span>
            </p>
          )}
          <p className="mac-info__hint">不选择标签时显示全部内容；只展示已填写的信息</p>
          {loading ? (
            <div className="mac-info__state">正在加载信息节点…</div>
          ) : loadError ? (
            <div className="mac-info__state">
              信息节点加载失败
              <div className="mac-info__state-sub">请检查网络后重试</div>
              <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 12 }} onClick={() => setReloadToken((value) => value + 1)}>
                重新加载
              </button>
            </div>
          ) : visibleRoots.length === 0 ? (
            <div className="mac-info__state">
              暂无内容，点击右上角「编辑」添加信息节点
              <div className="mac-info__state-sub">信息节点对所有协作者共享</div>
            </div>
          ) : noSelectedValue ? (
            /* 选中的标签整片没填过内容：不给空壳正文，直接提示补充（带「!」的标签点开就是这里） */
            <div className="mac-info__state">
              信息不足请补充
              <div className="mac-info__state-sub">
                「{emptyTagTitles}」下还没有任何已填写的信息，请补充后再查看
              </div>
              {canEdit && (
                <button
                  type="button"
                  className="mac-btn mac-btn--outline"
                  style={{ marginTop: 12 }}
                  onClick={() => navigate(`/admin/project-detail/${projectId}/edit`)}
                >
                  <MacPencil size={13} />去补充信息
                </button>
              )}
            </div>
          ) : (
            <article className="mac-doc">
              {visibleRoots.map((root) => (
                <DocSection
                  key={root.id}
                  node={root}
                  depth={1}
                  byParent={byParent}
                  marked={marked}
                  onToggleMark={toggleMark}
                  valueCounts={valueCounts}
                />
              ))}
            </article>
          )}
        </>
      )}
    </section>
  );
}

/** 文档式节点：标题与内容同一行并列（层级用缩进与字号/颜色区分，不再分行堆叠）；分支节点只占一行标题 */
function DocSection({ node, depth, byParent, marked, onToggleMark, valueCounts }: {
  node: ProjectInfoNode;
  depth: number;
  byParent: Map<string | null, ProjectInfoNode[]>;
  marked: Set<string>;
  onToggleMark: (nodeId: string) => void;
  /** 各节点名下已填写的末级字段数：为 0 的整棵不渲染（父组件已保证根节点 > 0） */
  valueCounts: Map<string, number>;
}) {
  // 只展示有信息的内容：没填的字段不占位、也不显示「（未填写）」；空分支连标题一起去掉，
  // 免得留下一个只有字段名、点进去什么都没有的空壳
  const allChildren = (byParent.get(node.id) ?? [])
    .filter((child) => (valueCounts.get(child.id) ?? 0) > 0);
  // 与编辑页一致：区域细分字段按所选区域显隐（节点仍在数据里，只是不渲染）
  const children = allChildren.filter((child) => isInfoNodeVisible(child, allChildren));
  const level = Math.min(depth, 4);
  // 末级判定看的是整棵树里有没有子节点，不是「有值的子节点」——叶子字段自己就是有值才渲染到这里，
  // 用 allChildren 判会把「子节点全空、自己也没值」的分支误判成叶子，多渲染一个空的（未填写）
  const isLeaf = (byParent.get(node.id) ?? []).length === 0;
  // 有没有自己的值：末级节点都有；非末级节点只有下拉/附件这类才有，纯文本分组没有。
  // 车型1 就是「有值又有子节点」——它下面还挂着数量，但自己的下拉选中项同样要展示。
  const showsValue = isLeaf || node.content_type !== 'text';
  const isMedia = showsValue && (node.content_type === 'file' || node.content_type === 'image');
  const isMarked = marked.has(node.id);
  const rowClass = [
    'mac-doc__row',
    `mac-doc__row--d${level}`,
    showsValue ? '' : 'mac-doc__row--branch',
    isMedia ? 'mac-doc__row--media' : '',
  ].filter(Boolean).join(' ');
  return (
    <section className={`mac-doc__section mac-doc__section--d${level}`}>
      <div className={rowClass}>
        <span className="mac-doc__label">{node.title}</span>
        {showsValue && (
          <div className="mac-doc__value">
            <DocContent node={node} />
          </div>
        )}
        {/* 关注星标（子节点=一级标签下的节点）：点它把该节点的最新变动订进「项目动态」 */}
        {depth > 1 && (
          <button
            type="button"
            className={`mac-doc__star${isMarked ? ' is-active' : ''}`}
            onClick={() => onToggleMark(node.id)}
            aria-label={isMarked ? `取消关注${node.title}` : `关注${node.title}`}
            aria-pressed={isMarked}
            title={isMarked ? '取消关注' : '关注此节点的变动'}
          >
            <MacStar size={14} />
          </button>
        )}
      </div>
      {children.map((child) => (
        <DocSection key={child.id} node={child} depth={depth + 1} byParent={byParent} marked={marked} onToggleMark={onToggleMark} valueCounts={valueCounts} />
      ))}
    </section>
  );
}

function DocContent({ node }: { node: ProjectInfoNode }) {
  if (node.content_type === 'select') {
    const data = (node.value ?? {}) as Partial<ProjectInfoSelectValue>;
    return <span className="mac-doc__pill">{data.selected || '未选择'}</span>;
  }
  if (node.content_type === 'file' || node.content_type === 'image') {
    return <DocAttachment node={node} />;
  }
  const text = typeof node.value === 'string' ? node.value.trim() : '';
  if (!text) return <span className="mac-doc__empty">（未填写）</span>;
  return <span className="mac-doc__text">{text}</span>;
}

function DocAttachment({ node }: { node: ProjectInfoNode }) {
  const file = (node.value ?? {}) as Partial<ProjectInfoFileValue>;
  const [imageBroken, setImageBroken] = useState(false);
  if (!file.name || file.resource_id == null) return <span className="mac-doc__empty">（未上传）</span>;
  // 资源管理服务下载接口（与项目文档同一接口）；图片节点顺带渲染缩略图，加载失败则只留附件行
  const url = `${API_CONFIG.ADMIN.BASE_URL}/resource-manager/resources/${file.resource_id}/download`;
  return (
    <div className="mac-doc__attach">
      <div className="mac-doc__file">
        {node.content_type === 'image' ? <MacImage size={15} /> : <MacFileText size={15} />}
        <span className="mac-doc__file-name">{file.name}</span>
        {typeof file.size === 'number' && <span>{formatFileSize(file.size)}</span>}
        <a className="mac-doc__dl" href={url} download={file.name} aria-label="下载"><MacDownload size={15} /></a>
      </div>
      {node.content_type === 'image' && !imageBroken && (
        <a href={url} target="_blank" rel="noreferrer">
          <img className="mac-doc__thumb" src={url} alt={file.name} onError={() => setImageBroken(true)} />
        </a>
      )}
    </div>
  );
}
