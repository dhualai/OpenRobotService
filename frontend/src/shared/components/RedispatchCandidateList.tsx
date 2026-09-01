// 二次派单感知增强（M2）：重派弹窗候选列表
// 数据源：详情 redispatch.candidates（精排 Top 快照，含 department/modules/duty）
// 排序规则（分四层）：
//   ① 精排推荐：精排分数前 5（保留原顺序）
//   ② 同部门：与工单当前接单人部门（refDept）相同的候选择
//   ③ 其他部门：有画像但非同部门的候选
//   ④ 待补充：无职责画像的候选（department/modules/duty 全空）——排最后
// 交互：单选（可取消），选中项以高亮 + ✓ 表示。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import type { RedispatchCandidate } from '@/api/ticket';
import { getUsers } from '@/api/users';
import type { UserItem } from '@/api/users';

interface Props {
  candidates?: RedispatchCandidate[] | null;
  refDept?: string | null;
  value?: string | null; // 选中 engineer_id
  onChange?: (c: RedispatchCandidate | null) => void;
  loading?: boolean;
}

// 全量可指派用户缓存（5 分钟内复用，与 UserSelect 一致），减少重复请求
let allUsersCache: UserItem[] | null = null;
let allUsersCacheTs = 0;

type GroupKey = 'top' | 'same' | 'other' | 'noprofile' | 'all';

/** 是否"有职责画像"：以责任模块/职责文本为准——只要有 modules 或 duty 就算有画像。
 *  department/job_level 缺失不算"整张画像缺失"（那只是组织归属信息）；仅当 modules/duty 都空才视为待补画像。 */
function hasProfile(c: RedispatchCandidate): boolean {
  return (
    !!(c.department && String(c.department).trim()) ||
    !!(c.modules && c.modules.length) ||
    !!(c.duty && String(c.duty).trim())
  );
}

function GroupLabel({ label }: { label: string }) {
  return (
    <div
      style={{
        padding: '4px 12px',
        fontSize: 12,
        color: 'var(--td-text-color-secondary, #999)',
        background: 'var(--td-bg-color-container-hover, #f5f5f5)',
      }}
    >
      {label}
    </div>
  );
}

/** 候选搜索框：输入关键词即在全部可指派用户中模糊搜索（找不到精排推荐的人时用） */
function SearchBox({ keyword, setKeyword }: { keyword: string; setKeyword: (v: string) => void }) {
  return (
    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--td-bg-color-component, #eee)' }}>
      <input
        type="text"
        placeholder="搜索全部用户（姓名/账号）"
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        style={{
          width: '100%', boxSizing: 'border-box', padding: '7px 10px', fontSize: 13,
          border: '1px solid var(--td-component-border, #dcdcdc)', borderRadius: 6, outline: 'none',
        }}
      />
    </div>
  );
}

export default function RedispatchCandidateList({
  candidates,
  refDept,
  value,
  onChange,
  loading,
}: Props) {
  // 候选搜索：输入关键词时从全量可指派用户里模糊匹配，让用户能选到精排之外的人。
  const [keyword, setKeyword] = useState('');
  const [allUsers, setAllUsers] = useState<UserItem[]>(allUsersCache || []);
  useEffect(() => {
    const now = Date.now();
    if (allUsersCache && now - allUsersCacheTs < 5 * 60 * 1000) {
      setAllUsers(allUsersCache);
      return;
    }
    getUsers()
      .then((list) => {
        allUsersCache = list;
        allUsersCacheTs = Date.now();
        setAllUsers(list);
      })
      .catch(() => {
        // 搜索用户列表拉取失败不阻断精排候选展示
        Toast({ message: '用户列表加载失败，仅展示推荐候选', theme: 'warning' });
      });
  }, []);

  const searching = keyword.trim().length > 0;
  // 用户 → 候选条目（搜索项 / 全部用户分组共用），画像字段一并映射，便于展示部门/职级/模块
  const toCandidate = (u: UserItem): RedispatchCandidate => ({
    rank: 0,
    engineer_id: u.id,
    name: u.name || u.username || u.id,
    department: u.department || null,
    job_level: u.job_level ?? null,
    modules: u.modules || [],
    duty: u.duty || null,
    missing: (u.department || u.modules?.length || u.duty) ? [] : ['department'],
  });
  const searchResults = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    if (!kw) return [];
    return allUsers
      .filter((u) => (u.name || '').toLowerCase().includes(kw) || (u.username || '').toLowerCase().includes(kw))
      .map(toCandidate);
  }, [allUsers, keyword]);

  if (loading) {
    return <div className="redispatch-cand__empty">候选加载中…</div>;
  }
  const list = candidates ?? [];

  // 搜索模式：展示所有匹配用户，用户可任选其一作为倾向处理人
  if (searching) {
    const picked = (c: RedispatchCandidate) => {
      onChange?.(value === c.engineer_id ? null : c);
    };
    return (
      <div className="redispatch-cand" style={{ maxHeight: 320, overflowY: 'auto' }}>
        <SearchBox keyword={keyword} setKeyword={setKeyword} />
        {searchResults.length === 0 ? (
          <div className="redispatch-cand__empty" style={{ textAlign: 'center', padding: '18px 0', color: '#999' }}>
            未找到匹配用户
          </div>
        ) : (
          searchResults.map((c) => {
            const selected = value === c.engineer_id;
            return (
              <div
                key={`search-${c.engineer_id}`}
                className={`redispatch-cand__item${selected ? ' redispatch-cand__item--selected' : ''}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                  borderBottom: '1px solid var(--td-bg-color-component, #eee)', cursor: 'pointer',
                  background: selected ? 'rgba(0, 82, 217, 0.08)' : undefined,
                }}
                onClick={() => picked(c)}
              >
                <div
                  style={{
                    width: 36, height: 36, borderRadius: '50%', background: '#e8eef7', color: '#0052d9',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 600, flexShrink: 0,
                  }}
                >
                  {(c.name || '?').slice(0, 1)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{c.name}</div>
                  <div style={{ fontSize: 11, color: '#888', marginTop: 2 }}>全部用户</div>
                </div>
                <div style={{ color: '#0052d9', fontWeight: 700 }}>{selected ? '✓' : '○'}</div>
              </div>
            );
          })
        )}
      </div>
    );
  }

  // 分组：
  // - 「精排推荐」= 真正有精排分数（scores.total>0）的候选，按分数降序取前 5
  //   （避免把「无精排分、仅兜底补齐」的人误标为精排推荐）。
  // - 「全部用户」= 默认主体：候选里有职责画像的 + 全量可指派用户里有画像的，合并去重。
  //   让重派默认就展示全部有画像的人，而不是被一堆"同部门/待补"分组切碎。
  // - 「待补充画像」= 真无职责画像（modules/duty 都空）的候选，仅少量。
  // list 为空（无精排候选快照）时，仍展示「全部用户」分组与搜索框，保证有可选项。
  const hasRank = (c: RedispatchCandidate) => (c.scores?.total ?? 0) > 0;
  const ranked = list.filter(hasRank).sort((a, b) => (b.scores?.total ?? 0) - (a.scores?.total ?? 0));
  const top = ranked.slice(0, 5);

  // 全部用户：候选中有画像的 + 全部可指派用户中有画像的，合并去重
  const candWithProfile = list.filter(hasProfile);
  const allGroupData: RedispatchCandidate[] = [...candWithProfile];
  const allSeen = new Set<string>(allGroupData.map((c) => c.engineer_id));
  for (const u of allUsers) {
    if (!u.has_profile || allSeen.has(u.id)) continue;
    const cand = toCandidate(u);
    allSeen.add(u.id);
    allGroupData.push(cand);
  }

  // 待补充画像：候选里既无职责画像、又不在「全部用户」里的（modules/duty 都空）
  const noProfile = list.filter((c) => !hasProfile(c));

  const groups: Array<{ key: GroupKey; label: string; data: RedispatchCandidate[] }> = [
    { key: 'top', label: '精排推荐', data: top },
    { key: 'all', label: '全部用户', data: allGroupData },
    { key: 'noprofile', label: '待补充画像', data: noProfile },
  ];

  const onPick = (c: RedispatchCandidate) => {
    onChange?.(value === c.engineer_id ? null : c);
  };

  return (
    <div className="redispatch-cand" style={{ maxHeight: 320, overflowY: 'auto' }}>
      <SearchBox keyword={keyword} setKeyword={setKeyword} />
      {groups.map(
        (g) =>
          g.data.length > 0 && (
            <div key={g.key}>
              <GroupLabel label={g.label} />
              {g.data.map((c) => {
                const selected = value === c.engineer_id;
                const dept = c.department || (g.key === 'all' ? '' : '-');
                const level = c.job_level ? `L${c.job_level}` : '';
                const modules = (c.modules || []).slice(0, 3).join(' · ');
                return (
                  <div
                    key={c.engineer_id}
                    className={`redispatch-cand__item${selected ? ' redispatch-cand__item--selected' : ''}`}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 10,
                      padding: '10px 12px',
                      borderBottom: '1px solid var(--td-bg-color-component, #eee)',
                      cursor: 'pointer',
                      background: selected ? 'rgba(0, 82, 217, 0.08)' : undefined,
                    }}
                    onClick={() => onPick(c)}
                  >
                    <div
                      style={{
                        width: 36,
                        height: 36,
                        borderRadius: '50%',
                        background: selected ? 'var(--td-brand-color, #0052d9)' : '#e8eef7',
                        color: selected ? '#fff' : '#0052d9',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontWeight: 600,
                        flexShrink: 0,
                      }}
                    >
                      {c.name.slice(0, 1)}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 600, fontSize: 14 }}>{c.name}</span>
                        <span style={{ fontSize: 11, color: '#888' }}>{dept}{level ? `（${level}）` : ''}</span>
                        {/* 二次派单感知增强（M4）：候选标记（如「项目负责人」），帮用户快速识别 */}
                        {(c.tags || []).map((tag) => (
                          <span
                            key={tag}
                            style={{
                              fontSize: 10,
                              lineHeight: '16px',
                              padding: '0 6px',
                              borderRadius: 3,
                              color: '#0052d9',
                              background: 'rgba(0, 82, 217, 0.10)',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {tag}
                          </span>
                        ))}
                      </div>
                      {modules || c.duty ? (
                        <div style={{ fontSize: 11, color: '#666', marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {modules || c.duty}
                        </div>
                      ) : c.department ? (
                        <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>{c.department}</div>
                      ) : (
                        <div style={{ fontSize: 11, color: '#b45309', marginTop: 2 }}>画像待补充</div>
                      )}
                    </div>
                    <div style={{ color: '#0052d9', fontWeight: 700 }}>
                      {selected ? '✓' : '○'}
                    </div>
                  </div>
                );
              })}
            </div>
          ),
      )}
    </div>
  );
}
