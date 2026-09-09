// 项目选择器：下拉展开项目列表 + 模糊搜索（按名称/编码过滤）
// 数据源：全量项目（GET /api/admin/projects/），用于兜底双工单场景——需对比用户项目是否在全量列表。
// 支持按 AI 项目名预填匹配。复用 UserSelect 的浮层结构与样式（user-select__*）。
// 相关性排序（0907 需求）：提过单的项目 > 名下项目 > 其他，前两类带小字标注，
// 搜索过滤后排序仍生效。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getProjects, getMyProjectRelevance } from '@/api/projects';
import type { ProjectItem, ProjectRelevance } from '@/api/projects';

interface Props {
  value?: string | null; // project_code
  onChange?: (project: ProjectItem) => void;
  placeholder?: string;
  title?: string;
  /** AI 给的项目名：value(project_code) 为空时按名称在名下项目里匹配预填，减少用户手选 */
  nameHint?: string | null;
}

// 库里遗留的英文状态（列默认值）映射为中文；中文生命周期状态原样透传
const STATUS_LABELS: Record<string, string> = { active: '活跃项目', inactive: '停用项目' };

// 模块级缓存，5 分钟内复用，减少重复请求
let projectCache: ProjectItem[] | null = null;
let projectCacheTs = 0;
const EMPTY_RELEVANCE: ProjectRelevance = { ticketed: [], owned: [] };
let relevanceCache: ProjectRelevance | null = null; // 排序信号（提过单/名下编码集）
let relevanceCacheTs = 0;

export default function ProjectSelect({
  value,
  onChange,
  placeholder = '请选择绑定项目',
  title = '选择项目',
  nameHint = null,
}: Props) {
  const [visible, setVisible] = useState(false);
  const [projects, setProjects] = useState<ProjectItem[]>(projectCache || []);
  const [relevance, setRelevance] = useState<ProjectRelevance>(relevanceCache || EMPTY_RELEVANCE);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [error, setError] = useState('');

  const selected = useMemo(
    () => projects.find((p) => p.project_code === value) || null,
    [projects, value],
  );

  const loadProjects = async () => {
    const now = Date.now();
    // 相关性信号（提过单/名下）：失败静默——排序退化为原序，不阻塞选项目
    const loadRelevance = (async () => {
      if (relevanceCache && now - relevanceCacheTs < 5 * 60 * 1000) {
        setRelevance(relevanceCache);
        return;
      }
      try {
        const rel = await getMyProjectRelevance();
        relevanceCache = rel;
        relevanceCacheTs = now;
        setRelevance(rel);
      } catch {
        /* 保持上次信号/空集 */
      }
    })();
    if (projectCache && now - projectCacheTs < 5 * 60 * 1000) {
      setProjects(projectCache);
      await loadRelevance;
      return;
    }
    setLoading(true);
    setError('');
    try {
      const list = await getProjects();
      projectCache = list;
      projectCacheTs = now;
      setProjects(list);
    } catch (e) {
      setError('获取项目列表失败');
      Toast({ message: `获取项目列表失败: ${e instanceof Error ? e.message : ''}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (visible) loadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  // AI 项目名预填：value(project_code) 为空且 AI 给了项目名 → 按名称在名下项目里匹配，替用户预选。
  // 匹配策略：① 忽略大小写/首尾空格精确等值；② 互为包含（AI 名可能带"项目"后缀或简写）；命中即预选。
  useEffect(() => {
    if (value || !nameHint || projects.length === 0) return;
    const hint = nameHint.trim().toLowerCase();
    const hit = projects.find((p) => {
      const n = (p.name || '').trim().toLowerCase();
      return n && (n === hint || n.includes(hint) || hint.includes(n));
    });
    if (hit) onChange?.(hit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, value, nameHint]);

  // 相关性排序：搜索过滤后按 提过单 > 名下 > 其他 排列，
  // 同一项目两属性兼有时归入「提过单」组；提单组内按提单数降序（同数保持原序）
  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const base = kw
      ? projects.filter(
          (p) =>
            (p.name || '').toLowerCase().includes(kw) ||
            (p.project_code || '').toLowerCase().includes(kw),
        )
      : projects;
    if (relevance.ticketed.length === 0 && relevance.owned.length === 0) return base;
    const ticketed = new Set(relevance.ticketed.map((t) => t.code));
    const ticketedCount = new Map(relevance.ticketed.map((t) => [t.code, t.count]));
    const owned = new Set(relevance.owned);
    const g1 = base
      .filter((p) => ticketed.has(p.project_code))
      .sort(
        (a, b) =>
          (ticketedCount.get(b.project_code) || 0) - (ticketedCount.get(a.project_code) || 0),
      );
    const g2 = base.filter((p) => !ticketed.has(p.project_code) && owned.has(p.project_code));
    const rest = base.filter((p) => !ticketed.has(p.project_code) && !owned.has(p.project_code));
    return g1.length || g2.length ? [...g1, ...g2, ...rest] : base;
  }, [projects, keyword, relevance]);

  const relLabel = (code: string): { text: string; cls: string } | null => {
    if (relevance.ticketed.some((t) => t.code === code)) return { text: '你提过单的项目', cls: 'user-select__status--ticketed' };
    if (relevance.owned.includes(code)) return { text: '你名下的项目', cls: 'user-select__status--owned' };
    return null;
  };

  const handlePick = (p: ProjectItem) => {
    onChange?.(p);
    setVisible(false);
    setKeyword('');
  };

  return (
    <div className="user-select">
      <button type="button" className="user-select__trigger" onClick={() => setVisible(true)}>
        {selected ? (
          <span className="user-select__trigger-text">{selected.name}</span>
        ) : value || nameHint ? (
          // selected 匹配不到（项目不在名下/列表未加载完）时，优先显示 AI 给的项目名称而非 project_id 编码
          <span className="user-select__trigger-text">{nameHint || value}</span>
        ) : (
          <span className="user-select__trigger-placeholder">{placeholder}</span>
        )}
        <span className="user-select__arrow">▾</span>
      </button>

      {visible && (
        <>
          <div className="user-select__mask" onClick={() => setVisible(false)} />
          <div className="user-select__panel">
            <div className="user-select__panel-header">
              <span>{title}</span>
              <span className="user-select__close" onClick={() => setVisible(false)}>✕</span>
            </div>
            <input
              className="tasks-search user-select__search"
              placeholder="搜索项目名称 / 编码…"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="user-select__list">
              {loading ? (
                <div className="user-select__empty">加载中…</div>
              ) : error ? (
                <div className="user-select__empty">{error}</div>
              ) : filtered.length === 0 ? (
                <div className="user-select__empty">未找到匹配项目</div>
              ) : (
                filtered.map((p) => {
                  const rel = relLabel(p.project_code);
                  return (
                    <div
                      key={p.project_code}
                      className={`user-select__item ${p.project_code === value ? 'is-selected' : ''}`}
                      onClick={() => handlePick(p)}
                    >
                      <div className="user-select__item-name">{p.name}</div>
                      <div className="user-select__item-meta">
                        <span>{p.project_code}</span>
                        {rel && <span className={`user-select__status ${rel.cls}`}>{rel.text}</span>}
                        {p.status && (
                          <span className="user-select__status user-select__status--info">
                            {STATUS_LABELS[p.status] || p.status}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
