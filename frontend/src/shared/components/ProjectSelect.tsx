// 项目选择器：下拉展开项目列表 + 模糊搜索（按名称/编码过滤）
// 数据源：全量项目（GET /api/admin/projects/），用于兜底双工单场景——需对比用户项目是否在全量列表。
// 支持按 AI 项目名预填匹配。复用 UserSelect 的浮层结构与样式（user-select__*）。
// 相关性排序（0907 需求）：提过单的项目 > 名下项目 > 其他，前两类带小字标注，
// 搜索过滤后排序仍生效。
// ORS-713 智能匹配：调用方传入 matched（会话信号→AI 推荐项目）时，
// 推荐组排在票史之前并标注「与当前问题最相关」；matched 为空=退化原排序。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getProjects, getMyProjectRelevance } from '@/api/projects';
import type { ProjectItem, ProjectRelevance } from '@/api/projects';
import type { ProjectMatchItem } from '@/api/ai';

interface Props {
  value?: string | null; // project_code
  onChange?: (project: ProjectItem) => void;
  placeholder?: string;
  title?: string;
  /** AI 给的项目名：value(project_code) 为空时按名称在名下项目里匹配预填，减少用户手选 */
  nameHint?: string | null;
  /** 外部项目列表：传入时不再拉取全量项目，只在传入列表内搜索/选择（如“仅用户关联项目”场景） */
  projects?: ProjectItem[] | null;
  /** ORS-713 智能匹配推荐（按会话信号排序）：命中项排最前并带「与当前问题最相关」标注 */
  matched?: ProjectMatchItem[] | null;
}

// 库里遗留的英文状态（列默认值）映射为中文；中文生命周期状态原样透传
const STATUS_LABELS: Record<string, string> = { active: '活跃项目', inactive: '停用项目' };

// 终态项目判断（ORS-713）：已验收/已结束/中止/暂停——领导定调「已验收的没有
// 优先级，直接滚」。与后端 project_rank._STAGE_TIER 的 tier 2/3 对齐，前端按
// 包含匹配兜住历史数据里的措辞变体（如「已验收」）。
const TERMINAL_STATUS_WORDS = ['验收', '结束', '中止', '暂停', '已关闭', '停用'];
function isTerminalProject(p: { status?: string }): boolean {
  const s = (p.status || '').trim();
  if (!s) return false;
  return TERMINAL_STATUS_WORDS.some((w) => s.includes(w));
}

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
  projects: externalProjects = null,
  matched = null,
}: Props) {
  const [visible, setVisible] = useState(false);
  const [projects, setProjects] = useState<ProjectItem[]>(projectCache || []);
  const [relevance, setRelevance] = useState<ProjectRelevance>(relevanceCache || EMPTY_RELEVANCE);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [error, setError] = useState('');

  // 外部列表模式（仅用户关联项目）：候选集直接取外部传入列表
  const candidateProjects = externalProjects ?? projects;

  const selected = useMemo(
    () => candidateProjects.find((p) => p.project_code === value) || null,
    [candidateProjects, value],
  );

  const loadProjects = async () => {
    // 外部列表模式：不拉全量项目，直接使用调用方传入的关联项目列表
    if (externalProjects) {
      setProjects(externalProjects);
      return;
    }
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

  // AI 项目名预填：value(project_code) 为空且 AI 给了项目名 → 按名称在候选列表里匹配，替用户预选。
  // 匹配策略：① 忽略大小写/首尾空格精确等值；② 互为包含（AI 名可能带"项目"后缀或简写）；命中即预选。
  useEffect(() => {
    if (value || !nameHint || candidateProjects.length === 0) return;
    const hint = nameHint.trim().toLowerCase();
    const hit = candidateProjects.find((p) => {
      const n = (p.name || '').trim().toLowerCase();
      return n && (n === hint || n.includes(hint) || hint.includes(n));
    });
    if (hit) onChange?.(hit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateProjects, value, nameHint]);

  // 相关性排序（ORS-713）：智能匹配推荐 > 提过单 > 名下 > 其他，
  // 推荐组按 AI 返回顺序（相关度降序）；票史组内按提单数降序（同数保持原序）。
  // 终态项目（已验收/已结束/中止/暂停）统一沉到列表最末尾——领导定调「已验收的
  // 没有优先级，直接滚」，不参与上面任何分组；与后端 project_rank 的分层一致。
  // 搜索过滤后排序仍生效——搜出来的结果里推荐/票史标记照常显示。
  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const base = kw
      ? candidateProjects.filter(
          (p) =>
            (p.name || '').toLowerCase().includes(kw) ||
            (p.project_code || '').toLowerCase().includes(kw),
        )
      : candidateProjects;
    // 终态拆分：先排非终态（正常分组），终态整组垫底
    const live = base.filter((p) => !isTerminalProject(p));
    const dead = base.filter((p) => isTerminalProject(p));
    const orderLive = (list: ProjectItem[]) => list;
    const tail = (list: ProjectItem[]) => [...orderLive(list), ...dead];
    const matchedOrder = new Map((matched || []).map((m, i) => [m.code, i]));
    const hasMatched = matchedOrder.size > 0;
    if (!hasMatched && relevance.ticketed.length === 0 && relevance.owned.length === 0)
      return tail(live);
    const ticketed = new Set(relevance.ticketed.map((t) => t.code));
    const ticketedCount = new Map(relevance.ticketed.map((t) => [t.code, t.count]));
    const owned = new Set(relevance.owned);
    const g0 = live
      .filter((p) => matchedOrder.has(p.project_code))
      .sort((a, b) => (matchedOrder.get(a.project_code) ?? 0) - (matchedOrder.get(b.project_code) ?? 0));
    const g1 = live
      .filter((p) => !matchedOrder.has(p.project_code) && ticketed.has(p.project_code))
      .sort(
        (a, b) =>
          (ticketedCount.get(b.project_code) || 0) - (ticketedCount.get(a.project_code) || 0),
      );
    const g2 = live.filter(
      (p) => !matchedOrder.has(p.project_code) && !ticketed.has(p.project_code) && owned.has(p.project_code),
    );
    const rest = live.filter(
      (p) => !matchedOrder.has(p.project_code) && !ticketed.has(p.project_code) && !owned.has(p.project_code),
    );
    if (!g0.length && !g1.length && !g2.length) return tail(live);
    // 终态组内部仍按 票史 > 名下 > 其他 排，避免一片乱序
    const deadTicketed = dead.filter((p) => ticketed.has(p.project_code));
    const deadOwned = dead.filter((p) => !ticketed.has(p.project_code) && owned.has(p.project_code));
    const deadRest = dead.filter((p) => !ticketed.has(p.project_code) && !owned.has(p.project_code));
    return [...g0, ...g1, ...g2, ...rest, ...deadTicketed, ...deadOwned, ...deadRest];
  }, [candidateProjects, keyword, relevance, matched]);

  const relLabel = (code: string): { text: string; cls: string } | null => {
    if ((matched || []).some((m) => m.code === code))
      return { text: '与当前问题最相关', cls: 'user-select__status--matched' };
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
