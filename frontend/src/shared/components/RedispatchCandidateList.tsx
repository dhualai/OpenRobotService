// 二次派单感知增强（M2）：重派弹窗候选列表
// 数据源：详情 redispatch.candidates（精排 Top 快照，含 department/modules/duty）
// 排序规则（分四层）：
//   ① 精排推荐：精排分数前 5（保留原顺序）
//   ② 同部门：与工单当前接单人部门（refDept）相同的候选择
//   ③ 其他部门：有画像但非同部门的候选
//   ④ 待补充：无职责画像的候选（department/modules/duty 全空）——排最后
// 交互：单选（可取消），选中项以高亮 + ✓ 表示。
import type { RedispatchCandidate } from '@/api/ticket';

interface Props {
  candidates?: RedispatchCandidate[] | null;
  refDept?: string | null;
  value?: string | null; // 选中 engineer_id
  onChange?: (c: RedispatchCandidate | null) => void;
  loading?: boolean;
}

type GroupKey = 'top' | 'same' | 'other' | 'noprofile';

/** 是否"有画像"：优先用快照权威 `missing` 字段（全空=完整），缺失（历史数据无该字段）时回退启发式 */
function hasProfile(c: RedispatchCandidate): boolean {
  if (Array.isArray(c.missing)) {
    return c.missing.length === 0;
  }
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

export default function RedispatchCandidateList({
  candidates,
  refDept,
  value,
  onChange,
  loading,
}: Props) {
  if (loading) {
    return <div className="redispatch-cand__empty">候选加载中…</div>;
  }
  const list = candidates ?? [];
  if (!list.length) {
    return (
      <div className="redispatch-cand__empty" style={{ textAlign: 'center', padding: '18px 0', color: '#999' }}>
        暂无精排候选
      </div>
    );
  }

  // 分层分组
  const top = list.slice(0, 5);
  const rest = list.slice(5);
  const sameDept: RedispatchCandidate[] = [];
  const other: RedispatchCandidate[] = [];
  const noProfile: RedispatchCandidate[] = [];
  for (const c of rest) {
    if (!hasProfile(c)) {
      noProfile.push(c);
    } else if (refDept && c.department && c.department === refDept) {
      sameDept.push(c);
    } else {
      other.push(c);
    }
  }

  const groups: Array<{ key: GroupKey; label: string; data: RedispatchCandidate[] }> = [
    { key: 'top', label: '精排推荐', data: top },
    { key: 'same', label: '同部门推荐', data: sameDept },
    { key: 'other', label: '其他部门', data: other },
    { key: 'noprofile', label: '待补充画像（排最后）', data: noProfile },
  ];

  const onPick = (c: RedispatchCandidate) => {
    onChange?.(value === c.engineer_id ? null : c);
  };

  return (
    <div className="redispatch-cand" style={{ maxHeight: 320, overflowY: 'auto' }}>
      {groups.map(
        (g) =>
          g.data.length > 0 && (
            <div key={g.key}>
              <GroupLabel label={g.label} />
              {g.data.map((c) => {
                const selected = value === c.engineer_id;
                const dept = c.department || '-';
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
