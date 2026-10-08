// 代他人提单 —— 被代理人选择器。
//
// 与 UserSelect 的差异：
//  1. 数据源是后端专用接口 GET /api/tasks/on-behalf-candidates（限在职用户 + 排除自己），
//     而不是全量用户接口 —— 被代理人必须是注册在职用户（微信模板消息依赖 openid）；
//  2. 列表按后端下发的 group 显式分两段：「同项目」在上、「其他在职人员」在下。
//     **不靠顺序猜**（后端 project-members 的 role_name 可能为空，无法据此分组）；
//  3. 无同项目人员时该段整段隐藏，不显示空标题。
//
// 浮层沿用 UserSelect 的原生 fixed 实现，避免与页面已有 tdesign Popup 嵌套冲突。
import { useEffect, useMemo, useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import { getOnBehalfCandidates } from '@/api/ticket';
import type { OnBehalfCandidate } from '@/api/ticket';

interface Props {
  value?: string | null;
  onChange?: (user: OnBehalfCandidate | null) => void;
  /** 项目ID：决定「同项目」分段的成员来源（可为空，为空时全部归入「其他在职人员」） */
  projectId?: string;
  placeholder?: string;
  title?: string;
  disabled?: boolean;
}

const OnBehalfSelect = ({
  value,
  onChange,
  projectId,
  placeholder = '请选择被代理人',
  title = '选择被代理人',
  disabled = false,
}: Props) => {
  const [visible, setVisible] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [list, setList] = useState<OnBehalfCandidate[]>([]);
  const [selected, setSelected] = useState<OnBehalfCandidate | null>(null);

  // 受控回填：外部清空 value（如关闭弹窗重置）时同步清掉本地选中态，
  // 否则触发器会残留上一次的选择文本（幽灵选中）。
  useEffect(() => {
    if (!value && selected) setSelected(null);
  }, [value, selected]);

  // 拉取候选：后端已按 同项目 → 其他在职 排序，并带 group 标记
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    setLoading(true);
    getOnBehalfCandidates({ project_id: projectId || undefined })
      .then((data) => {
        if (cancelled) return;
        setList(Array.isArray(data) ? data : []);
      })
      .catch((err) => {
        console.error('代提选人候选加载失败', err);
        if (!cancelled) {
          Toast({ theme: 'error', message: '候选人员加载失败' });
          setList([]);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [visible, projectId]);

  // 前端二次按关键字过滤（接口已支持 keyword，此处保证输入即时响应，不额外发请求）
  const { projectItems, otherItems } = useMemo(() => {
    const kw = keyword.trim().toLowerCase();
    const match = (u: OnBehalfCandidate) => {
      if (!kw) return true;
      return (
        (u.name || '').toLowerCase().includes(kw) ||
        (u.username || '').toLowerCase().includes(kw)
      );
    };
    const filtered = list.filter(match);
    return {
      projectItems: filtered.filter((u) => u.group === 'project'),
      otherItems: filtered.filter((u) => u.group !== 'project'),
    };
  }, [list, keyword]);

  const handlePick = (user: OnBehalfCandidate) => {
    setSelected(user);
    setVisible(false);
    setKeyword('');
    onChange?.(user);
  };

  const handleClear = (e: React.MouseEvent) => {
    e.stopPropagation();
    setSelected(null);
    onChange?.(null);
  };

  const renderRow = (u: OnBehalfCandidate) => (
    <div
      key={u.id}
      className={`user-select__item ${u.id === value ? 'is-selected' : ''}`}
      onClick={() => handlePick(u)}
    >
      <div className="user-select__item-name">{u.name || u.username}</div>
      <div className="user-select__item-meta">
        <span>{u.username}</span>
      </div>
    </div>
  );

  const empty = !loading && projectItems.length === 0 && otherItems.length === 0;

  return (
    <div className="user-select on-behalf-select">
      <button
        type="button"
        className="user-select__trigger"
        onClick={() => !disabled && setVisible(true)}
        disabled={disabled}
      >
        {selected ? (
          /* 回显只显示被代理人姓名：不加「代 … 提交」字样（语义已由字段标题「被代理人」表达） */
          <span className="user-select__trigger-text">
            {selected.name || selected.username}
          </span>
        ) : (
          <span className="user-select__trigger-placeholder">{placeholder}</span>
        )}
        {selected && !disabled ? (
          <span className="on-behalf-select__clear" onClick={handleClear}>✕</span>
        ) : (
          <span className="user-select__arrow">▾</span>
        )}
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
              placeholder="搜索姓名 / 账号…"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
            />
            <div className="user-select__list">
              {loading ? (
                <div className="user-select__empty">加载中…</div>
              ) : empty ? (
                <div className="user-select__empty">未找到匹配人员</div>
              ) : (
                <>
                  {/* 同项目段：无人时整段隐藏，不显示空标题 */}
                  {projectItems.length > 0 && (
                    <div className="on-behalf-select__group">
                      <div className="on-behalf-select__group-title">同项目</div>
                      {projectItems.map(renderRow)}
                    </div>
                  )}
                  {otherItems.length > 0 && (
                    <div className="on-behalf-select__group">
                      <div className="on-behalf-select__group-title">其他在职人员</div>
                      {otherItems.map(renderRow)}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
};

export default OnBehalfSelect;
