// 项目选择器：下拉展开项目列表 + 模糊搜索（按名称/编码过滤）
// 数据源：当前登录用户（提单人）名下项目（GET /api/admin/projects/me），非全量；支持按 AI 项目名预填匹配。
// 用于工单草稿确认页绑定项目。复用 UserSelect 的浮层结构与样式（user-select__*）。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getMyProjects } from '@/api/projects';
import type { ProjectItem } from '@/api/projects';

interface Props {
  value?: string | null; // project_code
  onChange?: (project: ProjectItem) => void;
  placeholder?: string;
  title?: string;
  /** AI 给的项目名：value(project_code) 为空时按名称在名下项目里匹配预填，减少用户手选 */
  nameHint?: string | null;
}

// 模块级缓存，5 分钟内复用，减少重复请求
let projectCache: ProjectItem[] | null = null;
let projectCacheTs = 0;

export default function ProjectSelect({
  value,
  onChange,
  placeholder = '请选择绑定项目',
  title = '选择项目',
}: Props) {
  const [visible, setVisible] = useState(false);
  const [projects, setProjects] = useState<ProjectItem[]>(projectCache || []);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [error, setError] = useState('');

  const selected = useMemo(
    () => projects.find((p) => p.project_code === value) || null,
    [projects, value],
  );

  const loadProjects = async () => {
    const now = Date.now();
    if (projectCache && now - projectCacheTs < 5 * 60 * 1000) {
      setProjects(projectCache);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const list = await getMyProjects();
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

  // AI 项目名预填：value(project_code) 为空且 AI 给了项目名 → 按名称在名下项目里匹配，替用户预选
  useEffect(() => {
    if (value || !nameHint || projects.length === 0) return;
    const hit = projects.find((p) => (p.name || '').trim() === nameHint.trim());
    if (hit) onChange?.(hit);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, value, nameHint]);

  const filtered = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    return kw
      ? projects.filter(
          (p) =>
            (p.name || '').toLowerCase().includes(kw) ||
            (p.project_code || '').toLowerCase().includes(kw),
        )
      : projects;
  }, [projects, keyword]);

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
        ) : value ? (
          <span className="user-select__trigger-text">{value}</span>
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
                filtered.map((p) => (
                  <div
                    key={p.project_code}
                    className={`user-select__item ${p.project_code === value ? 'is-selected' : ''}`}
                    onClick={() => handlePick(p)}
                  >
                    <div className="user-select__item-name">{p.name}</div>
                    <div className="user-select__item-meta">
                      <span>{p.project_code}</span>
                      {p.status && (
                        <span className={`user-select__status user-select__status--${p.status}`}>
                          {p.status}
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
